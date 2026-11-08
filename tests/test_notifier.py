"""Notifier (owner: LLM-A). Spec: docs/agents/notifier.md"""
import smtplib
import sys
import types
from datetime import date, timedelta

import pytest

from agents import notifier
from core import storage
from core.schemas import Alert, Opportunity

MON, TUE = date(2026, 11, 9), date(2026, 11, 10)


def _opp(i, days, status="posted", title=None):
    close = (TUE + timedelta(days=days)).isoformat() if days is not None else None
    return Opportunity(id=i, number=f"N-{i}", title=title or f"Grant {i}", agency_name="National Park Service",
                       status=status, close_date=close, award_ceiling=250_000).model_dump()


def _state(opps, alerts=(), top=None):
    return {"opportunities": opps, "alerts": [a.model_dump() for a in alerts],
            "top": [{"opp_id": o["id"], "score": 60, "suggested_decision": "Chase", "objective_id": "3.5",
                     "division": "State Parks"} for o in (top if top is not None else opps)],
            "checked": []}


def _decide(*pairs):
    storage.write_json("decisions/dncr.json", [{"opp_id": i, "decision": d, "at": f"2026-11-0{n}T10:00:00"}
                                               for n, (i, d) in enumerate(pairs, 1)])


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "preview")
    monkeypatch.setenv("EMAIL_TO", "grants@example.org")
    monkeypatch.setenv("EMAIL_FROM", "DoNotReply@example.azurecomm.net")
    monkeypatch.setenv("APP_URL", "https://ffm.example.org/")
    monkeypatch.setenv("EMAIL_URGENT", "on")      # these tests cover the same-day path; the default is weekly only
    return notifier.settings()


# ---------- what is urgent ---------- #
def test_urgent_triggers(cfg):
    opps = {o["id"]: Opportunity(**o) for o in [_opp("a", 20), _opp("b", 120), _opp("c", 5), _opp("d", 5), _opp("e", 3)]}
    alerts = [Alert(opp_id="a", kind="new_since_yesterday", message="New"),       # short window -> urgent
              Alert(opp_id="b", kind="new_since_yesterday", message="New"),       # long window -> weekly only
              Alert(opp_id="c", kind="closing_soon", message="closes", days_to_close=5),   # chased -> urgent
              Alert(opp_id="d", kind="closing_soon", message="closes", days_to_close=5),   # not chased -> weekly only
              Alert(opp_id="e", kind="waitlist_opening", message="now open")]
    got = notifier.urgent_items(alerts, opps, {"c": "Chase", "e": "Waitlist"}, TUE, cfg)
    assert [u["key"] for u in got] == ["short_window:a", "chase_closing:c", "opening:e"]


def test_skipped_items_are_never_urgent(cfg):
    opps = {"a": Opportunity(**_opp("a", 3))}
    alerts = [Alert(opp_id="a", kind="waitlist_opening", message="now open")]
    assert notifier.urgent_items(alerts, opps, {"a": "Skip"}, TUE, cfg) == []


# ---------- which email, and only once ---------- #
def test_weekly_on_monday_once_per_week(cfg):
    ctx = {"urgent": [{"key": "opening:x"}]}
    assert notifier.decide_email(ctx, {}, MON, cfg)[0] == "weekly"
    assert notifier.decide_email(ctx, {"weekly": {"2026-W46": "sent"}}, MON, cfg)[0] == "urgent"
    assert notifier.decide_email(ctx, {"weekly": {"2026-W46": "sent"}, "urgent": {"opening:x": "sent"}}, MON, cfg)[0] is None


def test_no_email_on_a_quiet_weekday(cfg):
    assert notifier.decide_email({"urgent": []}, {}, TUE, cfg) == (None, [])


def test_new_this_week_needs_a_baseline():
    assert notifier.new_this_week(["a"], {MON.isoformat(): {"a": "posted"}}, MON) == []       # first run
    hist = {"2026-11-01": {"a": "posted"}, "2026-11-08": {"a": "posted", "b": "posted"}, MON.isoformat(): {"a": "", "b": ""}}
    assert notifier.new_this_week(["a", "b"], hist, MON) == ["b"]


# ---------- rendering ---------- #
def test_render_matches_site_and_escapes(cfg):
    opps = [_opp("a", 20, title="Parks <b>&</b> Trails"), _opp("b", 18000)]
    storage.write_json("opportunities/seen_history.json", {"2026-11-01": {}, MON.isoformat(): {"a": "", "b": ""}})
    ctx = notifier.build_context(_state(opps), MON, cfg)
    subject, page, text = notifier.render("weekly", ctx)
    assert subject.startswith("DNCR weekly: 2 new")
    assert "#053131" in page and "#BD5B00" in page and "Source Sans 3" in page          # web app tokens
    assert "https://ffm.example.org/#item-a" in page and "https://ffm.example.org/#today" in page
    assert "Parks &lt;b&gt;&amp;&lt;/b&gt; Trails" in page and "<b>&</b>" not in page
    assert "Closes in 21 days" in page and f"Closes {opps[1]['close_date']}" in page   # far dates not counted
    assert "Parks <b>&</b> Trails" in text and "https://ffm.example.org/#item-a" in text


def test_weekly_email_is_never_empty(cfg):
    ctx = notifier.build_context(_state([], top=[]), MON, cfg)
    _, page, _ = notifier.render("weekly", ctx)
    assert "No new matches this week" in page


# ---------- pipeline node and delivery ---------- #
class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def _urgent_state():
    opps = [_opp("a", 20)]
    return _state(opps, [Alert(opp_id="a", kind="new_since_yesterday", message="New")])


def test_preview_mode_renders_but_never_sends(cfg, monkeypatch):
    monkeypatch.setattr(notifier, "send", lambda *a: pytest.fail("preview mode must not send"))
    out = notifier.run(_urgent_state(), today=TUE)["notification"]
    assert out["kind"] == "urgent" and out["status"].startswith("preview only")
    assert storage.read_json("notify/preview/urgent.json")["html"].startswith("<!doctype html>")
    assert storage.read_json("notify/preview/weekly.json")["subject"].startswith("DNCR weekly")


def test_smtp_sends_once_then_stays_quiet(cfg, monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_USER", "bot")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    assert notifier.run(_urgent_state(), today=TUE)["notification"]["status"] == "sent"
    assert notifier.run(_urgent_state(), today=TUE)["notification"]["status"] == "nothing to send"   # rerun: no duplicate
    msg = FakeSMTP.sent[0]
    assert len(FakeSMTP.sent) == 1 and msg["To"] == "grants@example.org" and msg.is_multipart()
    assert "app-password" not in str(storage.read_json("notify/latest.json"))


def test_acs_message_shape(cfg, monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "acs")
    monkeypatch.setenv("EMAIL_ACS_CONNECTION_STRING", "endpoint=https://x.communication.azure.com/;accesskey=k")
    captured = {}

    class Poller:
        def result(self):
            return {"id": "op-1", "status": "Succeeded"}

    class Client:
        @classmethod
        def from_connection_string(cls, conn):
            return cls()

        def begin_send(self, message):
            captured.update(message)
            return Poller()

    monkeypatch.setitem(sys.modules, "azure.communication.email", types.SimpleNamespace(EmailClient=Client))
    out = notifier.run(_urgent_state(), today=TUE)["notification"]
    assert out["status"] == "sent" and out["provider"] == "acs:Succeeded:op-1"
    assert captured["senderAddress"] == "DoNotReply@example.azurecomm.net"
    assert captured["recipients"]["to"] == [{"address": "grants@example.org"}]
    assert captured["content"]["html"].startswith("<!doctype html>") and captured["content"]["plainText"]


def test_send_failure_is_recorded_not_raised(cfg, monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "acs")       # no connection string set
    monkeypatch.delenv("EMAIL_ACS_CONNECTION_STRING", raising=False)
    out = notifier.run(_urgent_state(), today=TUE)["notification"]
    assert out["status"] == "error" and "EMAIL_ACS_CONNECTION_STRING" in out["error"]
    assert storage.read_json("notify/sent.json", default=None) is None            # will retry next run


def test_mode_off_does_nothing(cfg, monkeypatch):
    monkeypatch.setenv("EMAIL_MODE", "off")
    assert notifier.run(_urgent_state(), today=TUE)["notification"]["status"] == "off"
    assert storage.read_json("notify/latest.json", default=None) is None


def test_api_serves_the_preview(cfg):
    from fastapi.testclient import TestClient

    from app.api import app

    client = TestClient(app)
    assert client.get("/api/email/preview").status_code == 404
    notifier.run(_urgent_state(), today=TUE)
    r = client.get("/api/email/preview?kind=urgent")
    assert r.status_code == 200 and "Act today" in r.text
    assert client.get("/api/email/status").json()["kind"] == "urgent"


def test_default_is_weekly_only(monkeypatch):
    monkeypatch.delenv("EMAIL_URGENT", raising=False)
    cfg = notifier.settings()
    ctx = {"urgent": [{"key": "opening:x"}]}
    assert cfg["urgent"] is False
    assert notifier.decide_email(ctx, {}, TUE, cfg) == (None, [])          # no same-day email
    assert notifier.decide_email(ctx, {}, MON, cfg)[0] == "weekly"         # urgent items ride in the weekly "Act now"
