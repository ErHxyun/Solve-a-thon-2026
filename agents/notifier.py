"""Notifier — weekly digest email + same-day urgent email.  Owner: LLM-A
Spec: docs/agents/notifier.md

Why weekly: about 26 state-eligible listings are posted a week and only ~1 of them fits DNCR
(Grants.gov export 2026-08-18), so a daily email would be empty most days. Why also urgent: some
windows are ~30 days, and a week's delay can make a grant impossible (the school-bus case).

The pipeline still runs every day. Each run this node decides:
  - weekly day (default Monday, ET) and not sent this week   -> WEEKLY digest (includes "Act now")
  - otherwise, an urgent trigger not emailed before          -> URGENT email, only if EMAIL_URGENT=on
  - otherwise                                                 -> no email
Default is weekly only (client preference, 2026-11-07): urgent items still appear in the weekly "Act now".
Urgent triggers (each item is emailed once per trigger):
  - a Waitlisted forecast is now open for applications       (Alert kind waitlist_opening)
  - a new short-list item whose window is <= 45 days          (new_since_yesterday + close date)
  - an item the user is chasing closes within 7 days          (closing_soon + decision Chase)

Every run also renders previews (notify/preview/weekly.json, urgent.json: subject + html) so the demo and the
web app can show the email without sending anything. Sending never breaks the pipeline: errors
are recorded in notify/latest.json. No credentials are logged or stored.

Delivery (EMAIL_MODE): off | preview (default: render only) | smtp | acs (Azure Communication Services).
CLI:  python -m agents.notifier --preview weekly      render from the last published run
      python -m agents.notifier --test                send a short test email with the current settings
"""
from __future__ import annotations

import argparse
import html
import os
import smtplib
import ssl
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Optional

from core import config, storage
from core.reference import load_profile
from core.schemas import Alert, Opportunity

SENT_PATH = "notify/sent.json"            # {"weekly": {week: date}, "urgent": {key: date}}
LATEST_PATH = "notify/latest.json"
HISTORY_PATH = "opportunities/seen_history.json"
DECISIONS_PATH = "decisions/dncr.json"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Design tokens copied from index.html so the email looks like the web app
INK, TEXT, DIM, LINE, GRAY = "#053131", "#212529", "#5A5F63", "#DCD6CF", "#FAF7F4"
HERO, ORANGE, OK, OK_BG, WARN, WARN_BG = "#042A2A", "#BD5B00", "#1E6B3A", "#DFF1E5", "#8A3B00", "#FCE6D2"
SANS = "'Source Sans 3', 'Segoe UI', Arial, sans-serif"
MONO = "'DM Mono', 'Courier New', monospace"


# --------------------------------------------------------------------------- #
# Settings (read at call time so tests and the CLI can change them)
# --------------------------------------------------------------------------- #
def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def settings() -> dict:
    return {
        "mode": _env("EMAIL_MODE", "preview").lower(),
        "to": [a.strip() for a in _env("EMAIL_TO").replace(";", ",").split(",") if a.strip()],
        "from": _env("EMAIL_FROM"),
        "reply_to": _env("EMAIL_REPLY_TO"),
        "weekly_day": _env("EMAIL_WEEKLY_DAY", "mon").lower()[:3],
        "urgent": _env("EMAIL_URGENT", "off").lower() in ("on", "true", "1", "yes"),   # same-day emails; default weekly only
        "urgent_new_days": int(_env("EMAIL_URGENT_NEW_DAYS", "45")),
        "urgent_closing_days": int(_env("EMAIL_URGENT_CLOSING_DAYS", "7")),
        "deadline_window_days": int(_env("EMAIL_DEADLINE_WINDOW_DAYS", "30")),
        "app_url": _env("APP_URL", "http://localhost:8000").rstrip("/"),
    }


# --------------------------------------------------------------------------- #
# What goes into an email
# --------------------------------------------------------------------------- #
def _days_left(opp: Opportunity, today: date) -> Optional[int]:
    if opp.close_date:
        try:
            return (date.fromisoformat(opp.close_date[:10]) - today).days
        except ValueError:
            pass
    return opp.days_to_close


def _latest_decisions() -> dict[str, str]:
    latest: dict[str, tuple[str, str]] = {}
    for d in storage.read_json(DECISIONS_PATH, default=[]):
        opp_id, at = d.get("opp_id"), d.get("at", "")
        if opp_id and (opp_id not in latest or at >= latest[opp_id][0]):
            latest[opp_id] = (at, d.get("decision", ""))
    return {k: v[1] for k, v in latest.items()}


def new_this_week(top_ids: list[str], history: dict[str, dict], today: date) -> list[str]:
    """Short-list items first seen in the last 7 days. Needs a baseline: nothing is new on day one."""
    dates = sorted(history)
    if not dates or dates[0] >= today.isoformat():
        return []
    week_ago = (today - timedelta(days=6)).isoformat()
    out = []
    for opp_id in top_ids:
        seen = [d for d in dates if opp_id in history[d]]
        first = seen[0] if seen else today.isoformat()
        if first >= week_ago and first > dates[0]:
            out.append(opp_id)
    return out


def urgent_items(alerts: list[Alert], opps: dict[str, Opportunity], decided: dict[str, str],
                 today: date, cfg: dict) -> list[dict]:
    """[{key, opp_id, reason}] for every urgent trigger today (before de-duplication)."""
    out = []
    for a in alerts:
        opp = opps.get(a.opp_id)
        if opp is None or decided.get(a.opp_id) == "Skip":
            continue
        n = _days_left(opp, today)
        if a.kind == "waitlist_opening":
            out.append({"key": f"opening:{a.opp_id}", "opp_id": a.opp_id, "reason": a.message})
        elif a.kind == "new_since_yesterday" and opp.status == "posted" and n is not None and 0 <= n <= cfg["urgent_new_days"]:
            out.append({"key": f"short_window:{a.opp_id}", "opp_id": a.opp_id,
                        "reason": f"New match with a short window: closes in {n} days ({opp.close_date})"})
        elif (a.kind == "closing_soon" and decided.get(a.opp_id) == "Chase" and n is not None
              and 0 <= n <= cfg["urgent_closing_days"]):
            out.append({"key": f"chase_closing:{a.opp_id}", "opp_id": a.opp_id, "reason": a.message})
    return out


def build_context(state: dict, today: date, cfg: dict) -> dict:
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    top = [t for t in state.get("top", []) if t["opp_id"] in opps]
    cards = {c["card"]["opp_id"]: c for c in state.get("checked", [])}
    alerts = [Alert(**a) for a in state.get("alerts", [])]
    decided = _latest_decisions()
    history = storage.read_json(HISTORY_PATH, default={})
    objectives = {o.objective_id: o for o in load_profile().objectives}
    top_ids = [t["opp_id"] for t in top]

    def item(opp_id: str, reason: str = "") -> dict:
        opp = opps[opp_id]
        scored = next((t for t in top if t["opp_id"] == opp_id), {})
        checked = cards.get(opp_id) or {}
        card = checked.get("card") or {}
        obj = objectives.get(scored.get("objective_id") or card.get("objective_id") or "")
        return {"opp_id": opp_id, "title": opp.title, "funder": opp.agency_name or opp.agency_code,
                "close_date": opp.close_date, "days_left": _days_left(opp, today), "status": opp.status,
                "award_ceiling": opp.award_ceiling, "suggested": scored.get("suggested_decision"),
                "my_decision": decided.get(opp_id), "confidence": checked.get("confidence") or scored.get("confidence"),
                "money_type": card.get("money_type"), "why_fit": card.get("why_fit") or "",
                "objective": f"Objective {obj.objective_id}, plan p. {obj.page}" if obj else "",
                "division": scored.get("division") or card.get("division") or "", "reason": reason,
                "url": f"{cfg['app_url']}/#item-{opp_id}"}

    urgent = urgent_items(alerts, opps, decided, today, cfg)
    new_ids = [i for i in new_this_week(top_ids, history, today) if decided.get(i) != "Skip"]
    chased = [i for i, d in decided.items() if d == "Chase" and i in opps]
    deadline_ids = []
    for opp_id in dict.fromkeys(top_ids + chased):
        n = _days_left(opps[opp_id], today)
        if (decided.get(opp_id) not in ("Skip", "Waitlist") and opps[opp_id].status == "posted"
                and n is not None and 0 <= n <= cfg["deadline_window_days"]):
            deadline_ids.append(opp_id)
    deadline_ids.sort(key=lambda i: _days_left(opps[i], today))
    waitlist = [i for i, d in decided.items() if d == "Waitlist" and i in opps]
    best = [i for i in top_ids if decided.get(i) not in ("Skip",) and i not in new_ids][:5]

    return {
        "today": today, "app_url": cfg["app_url"], "n_top": len(top_ids),
        "urgent": [item(u["opp_id"], u["reason"]) | {"key": u["key"]} for u in urgent],
        "new": [item(i) for i in new_ids],
        "deadlines": [item(i) for i in deadline_ids],
        "waitlist": [item(i) for i in waitlist],
        "best": [item(i) for i in best],
    }


# --------------------------------------------------------------------------- #
# Rendering (table layout + inline styles: what email clients support)
# --------------------------------------------------------------------------- #
def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _money(x: Optional[float]) -> str:
    if not x:
        return "Not stated"
    return f"${x / 1e6:.1f}M".replace(".0M", "M") if x >= 1e6 else f"${x / 1e3:.0f}K" if x >= 1e3 else f"${x:,.0f}"


def _deadline(it: dict) -> str:
    n = it["days_left"]
    if it["status"] == "forecasted":
        return "Forecast, not open yet"
    if n is None:
        return "Rolling / not stated"
    if n > 365:
        return f"Closes {it['close_date']}"  # far-off dates (some listings say 2076) are shown, not counted
    when = "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"
    return f"Closes {when} ({it['close_date']})"


def _mono(text: str, color: str = DIM, size: int = 12) -> str:
    return (f'<span style="font-family:{MONO};font-size:{size}px;letter-spacing:.14em;text-transform:uppercase;'
            f'color:{color};">{_e(text)}</span>')


def _button(label: str, url: str, solid: bool = True) -> str:
    bg, fg, border = (ORANGE, "#ffffff", ORANGE) if solid else ("#ffffff", INK, INK)
    return (f'<a href="{_e(url)}" style="display:inline-block;padding:12px 22px;border-radius:999px;border:2px solid {border};'
            f'background:{bg};color:{fg};font-family:{SANS};font-weight:700;font-size:15px;text-decoration:none;">{_e(label)} &rarr;</a>')


def _card(it: dict) -> str:
    tags = []
    if it.get("confidence"):
        tags.append(f'<span style="background:{OK_BG};color:{OK};font-family:{SANS};font-size:12px;font-weight:700;'
                    f'padding:3px 8px;">{_e(it["confidence"])} confidence</span>')
    if it.get("money_type"):
        tags.append(f'<span style="border:1px solid {INK};padding:2px 7px;">{_mono(it["money_type"], INK, 11)}</span>')
    decision = it.get("my_decision") and f"You chose {it['my_decision']}" or (it.get("suggested") and f"Suggested: {it['suggested']}")
    reason = (f'<tr><td colspan="2" style="padding:10px 12px;background:{WARN_BG};color:{WARN};font-family:{SANS};'
              f'font-size:15px;font-weight:600;">{_e(it["reason"])}</td></tr><tr><td colspan="2" style="height:10px"></td></tr>'
              if it.get("reason") else "")
    why = (f'<tr><td colspan="2" style="padding:10px 12px;background:{OK_BG};font-family:{SANS};font-size:15px;color:{TEXT};">'
           f'{_mono("Why it fits", OK, 11)}<br>{_e(it["why_fit"])}</td></tr>' if it.get("why_fit") else "")

    def kv(label: str, value: str) -> str:
        return (f'<td valign="top" style="padding:8px 12px 8px 0;width:50%;">{_mono(label, DIM, 11)}<br>'
                f'<span style="font-family:{SANS};font-size:15px;font-weight:600;color:{TEXT};">{_e(value)}</span></td>')

    plan = " · ".join(x for x in (it.get("objective"), it.get("division")) if x) or "Not matched"
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;border:1px solid {LINE};border-top:4px solid {INK};margin:0 0 16px;">
<tr><td style="padding:18px 20px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    {reason}
    <tr><td colspan="2" style="padding-bottom:8px;">{' '.join(tags)}</td></tr>
    <tr><td colspan="2"><a href="{_e(it['url'])}" style="font-family:{SANS};font-size:20px;font-weight:700;color:{INK};text-decoration:none;">{_e(it['title'])}</a></td></tr>
    <tr><td colspan="2" style="padding:2px 0 10px;font-family:{SANS};font-size:14px;color:{DIM};">{_e(it['funder'])}</td></tr>
    <tr>{kv("Deadline", _deadline(it))}{kv("Award ceiling", _money(it.get("award_ceiling")))}</tr>
    <tr>{kv("Plan fit", plan)}{kv("Decision", decision or "Not decided")}</tr>
    <tr><td colspan="2" style="height:10px;border-top:1px solid {LINE};"></td></tr>
    {why}
    <tr><td colspan="2" style="padding-top:14px;">{_button("Decide on the website", it['url'], solid=False)}</td></tr>
  </table>
</td></tr></table>"""


def _section(title: str, items: list[dict], empty: str) -> str:
    body = "".join(_card(i) for i in items) or (
        f'<p style="font-family:{SANS};font-size:16px;color:{DIM};margin:0 0 16px;">{_e(empty)}</p>')
    return (f'<tr><td style="padding:28px 32px 4px;"><h2 style="margin:0 0 6px;font-family:{SANS};font-size:26px;'
            f'font-weight:700;color:{INK};letter-spacing:-.01em;">{_e(title)}</h2>'
            f'<div style="height:10px;"></div>{body}</td></tr>')


def render(kind: str, ctx: dict) -> tuple[str, str, str]:
    """(subject, html, plain text) for kind = 'weekly' | 'urgent'."""
    today: date = ctx["today"]
    day = f"{today:%a %b} {today.day}, {today.year}"
    if kind == "urgent":
        n = len(ctx["urgent"])
        subject = f"Act today: {n} DNCR grant item{'s' if n != 1 else ''} need{'s' if n == 1 else ''} you"
        eyebrow, headline = f"Urgent · {day}", subject
        lead = "These can't wait for Monday's digest. Each one is listed once; open the website to decide."
        stats = [(str(n), "need you today"), (str(ctx["n_top"]), "on the short list")]
        sections = [("Act today", ctx["urgent"], "")]
    else:
        subject = (f"DNCR weekly: {len(ctx['new'])} new, {len(ctx['deadlines'])} closing within 30 days"
                   + (f", {len(ctx['urgent'])} to act on now" if ctx["urgent"] else ""))
        eyebrow, headline = f"Weekly digest · {day}", "This week’s short list"
        lead = ("Grants.gov is checked every morning; this is the week in one email. Each card explains the fit "
                "in plain English. You decide: Chase, Waitlist, Skip or Route. Nothing is ever submitted or sent.")
        stats = [(str(ctx["n_top"]), "on the short list"), (str(len(ctx["new"])), "new this week"),
                 (str(len(ctx["deadlines"])), "close ≤ 30 days")]
        sections = ([("Act now", ctx["urgent"], "")] if ctx["urgent"] else []) + [
            ("New this week", ctx["new"], "No new matches this week. The full feed was still checked every day."),
            ("Deadlines in the next 30 days", ctx["deadlines"], "Nothing on your list closes in the next 30 days."),
            ("On your waitlist", ctx["waitlist"], "Your waitlist is empty."),
            ("Still worth a look", ctx["best"], "")]
    sections = [s for s in sections if s[1] or s[2]]

    stat_cells = "".join(
        f'<td valign="top" style="padding:0 28px 0 0;"><div style="font-family:{SANS};font-size:34px;font-weight:700;color:#ffffff;'
        f'line-height:1.1;">{_e(v)}</div>{_mono(k, "#BDBDBD", 11)}</td>' for v, k in stats)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light"><title>{_e(subject)}</title>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:{GRAY};">
<div style="display:none;max-height:0;overflow:hidden;">{_e(lead)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{GRAY};"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:{GRAY};">
  <tr><td style="background:{HERO};padding:18px 32px;border-bottom:1px solid #0b3f3f;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:{SANS};font-size:18px;font-weight:700;color:#ffffff;">DNCR Federal Funds Matchmaker</td>
      <td align="right">{_mono("NC DNCR · Student prototype", "#BDBDBD", 11)}</td></tr></table></td></tr>
  <tr><td style="background:{INK};padding:36px 32px 34px;">
    {_mono(eyebrow, "#BDBDBD", 12)}
    <h1 style="margin:10px 0 12px;font-family:{SANS};font-size:40px;line-height:1.05;font-weight:700;color:#ffffff;letter-spacing:-.02em;">{_e(headline)}</h1>
    <p style="margin:0 0 22px;font-family:{SANS};font-size:17px;line-height:1.55;color:#ffffff;">{_e(lead)}</p>
    <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;"><tr>{stat_cells}</tr></table>
    {_button("Open today’s short list", ctx["app_url"] + "/#today")}
  </td></tr>
  {''.join(_section(t, i, e) for t, i, e in sections)}
  <tr><td style="padding:24px 32px 32px;font-family:{SANS};font-size:13px;line-height:1.5;color:{DIM};border-top:1px solid {LINE};">
    You get one digest a week, plus a same-day email only when something can’t wait (a waitlisted grant opens,
    a new match closes within weeks, or a grant you chose to chase closes within a week).<br>
    {_mono("Student prototype — not an official DNCR website", DIM, 10)}
  </td></tr>
</table></td></tr></table></body></html>"""

    lines = [subject, "", lead, "", " · ".join(f"{v} {k}" for v, k in stats), f"Open the short list: {ctx['app_url']}/#today", ""]
    for title, items, empty in sections:
        lines += [title.upper(), "-" * len(title)]
        lines += [f"- {i['title']} ({i['funder']})\n  {i['reason'] + chr(10) + '  ' if i.get('reason') else ''}"
                  f"{_deadline(i)} · award {_money(i.get('award_ceiling'))}\n  {i['url']}" for i in items] or [empty]
        lines.append("")
    lines.append("Student prototype — not an official DNCR website. Nothing is ever submitted or sent for you.")
    return subject, page, "\n".join(lines)


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
class EmailNotConfigured(RuntimeError):
    pass


def send(subject: str, html_body: str, text_body: str, cfg: dict) -> str:
    """Send with the configured backend. Returns a short provider id/status; raises on failure."""
    if not cfg["to"] or not cfg["from"]:
        raise EmailNotConfigured("set EMAIL_TO and EMAIL_FROM")
    if cfg["mode"] == "acs":
        conn = _env("EMAIL_ACS_CONNECTION_STRING")
        if not conn:
            raise EmailNotConfigured("set EMAIL_ACS_CONNECTION_STRING")
        from azure.communication.email import EmailClient  # imported only when used

        message = {"senderAddress": cfg["from"], "recipients": {"to": [{"address": a} for a in cfg["to"]]},
                   "content": {"subject": subject, "plainText": text_body, "html": html_body}}
        if cfg["reply_to"]:
            message["replyTo"] = [{"address": cfg["reply_to"]}]
        result = EmailClient.from_connection_string(conn).begin_send(message).result()
        return f"acs:{result.get('status')}:{result.get('id')}"
    if cfg["mode"] == "smtp":
        host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT", "587"))
        if not host:
            raise EmailNotConfigured("set SMTP_HOST (and SMTP_USER / SMTP_PASSWORD)")
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, cfg["from"], ", ".join(cfg["to"])
        if cfg["reply_to"]:
            msg["Reply-To"] = cfg["reply_to"]
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        ctx = ssl.create_default_context()
        with (smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) if port == 465
              else smtplib.SMTP(host, port, timeout=30)) as s:
            if port != 465 and _env("SMTP_STARTTLS", "true").lower() == "true":
                s.starttls(context=ctx)
            if _env("SMTP_USER"):
                s.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
            s.send_message(msg)
        return "smtp:sent"
    raise EmailNotConfigured(f"EMAIL_MODE={cfg['mode']} does not send (use smtp or acs)")


# --------------------------------------------------------------------------- #
# Pipeline node
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decide_email(ctx: dict, sent: dict, today: date, cfg: dict) -> tuple[Optional[str], list[dict]]:
    """('weekly' | 'urgent' | None, urgent items that are new)."""
    week = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    fresh = [u for u in ctx["urgent"] if u["key"] not in sent.get("urgent", {})]
    if WEEKDAYS[today.weekday()] == cfg["weekly_day"] and week not in sent.get("weekly", {}):
        return "weekly", fresh
    if fresh and cfg.get("urgent"):
        return "urgent", fresh
    return None, []


def run(state: dict, today: Optional[date] = None) -> dict:
    from agents.alert import today_et  # same "today" as the Alert node (Eastern time)

    today = today or today_et()
    cfg = settings()
    if cfg["mode"] == "off":
        return {"notification": {"kind": None, "status": "off"}}
    try:
        ctx = build_context(state, today, cfg)
    except Exception as exc:  # never break the pipeline over an email
        result = {"date": today.isoformat(), "kind": None, "status": "error", "error": f"context: {exc}"}
        storage.write_json(LATEST_PATH, result)
        return {"notification": result}

    # Previews for the demo / web app, every run
    for kind in ("weekly", "urgent"):
        if kind == "urgent" and not ctx["urgent"]:
            continue
        subject, page, _ = render(kind, ctx)
        storage.write_json(f"notify/preview/{kind}.json", {"date": today.isoformat(), "subject": subject, "html": page})

    sent = storage.read_json(SENT_PATH, default={"weekly": {}, "urgent": {}})
    kind, fresh = decide_email(ctx, sent, today, cfg)
    result: dict = {"date": today.isoformat(), "kind": kind, "mode": cfg["mode"], "recipients": len(cfg["to"]),
                    "urgent_items": [u["opp_id"] for u in fresh]}
    if kind is None:
        result["status"] = "nothing to send"
    else:
        subject, page, text = render(kind, ctx if kind == "weekly" else {**ctx, "urgent": fresh})
        result["subject"] = subject
        if cfg["mode"] == "preview":
            result["status"] = "preview only (EMAIL_MODE=preview)"
        else:
            try:
                result["provider"] = send(subject, page, text, cfg)
                result["status"] = "sent"
                if kind == "weekly":
                    sent.setdefault("weekly", {})[f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"] = _now()
                for u in (ctx["urgent"] if kind == "weekly" else fresh):
                    sent.setdefault("urgent", {})[u["key"]] = _now()
                storage.write_json(SENT_PATH, sent)
            except Exception as exc:  # recorded, not raised
                result["status"] = "error"
                result["error"] = f"{type(exc).__name__}: {exc}"[:300]
    storage.write_json(LATEST_PATH, result)
    print(f"[notifier] {result['status']}" + (f" ({kind})" if kind else ""))
    return {"notification": result}


# --------------------------------------------------------------------------- #
# CLI: preview from the last published run, or send a test email
# --------------------------------------------------------------------------- #
def state_from_storage() -> dict:
    rows = storage.read_json(f"scored/{config.AGENCY}/latest.json", default=[])
    top = [r for r in rows if r.get("suggested_decision") != "Skip"][: config.TOP_N]
    return {"opportunities": [r["opportunity"] for r in rows if r.get("opportunity")],
            "top": top, "checked": [r["checked"] for r in rows if r.get("checked")],
            "alerts": storage.read_json("alerts/latest.json", default=[])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--preview", choices=["weekly", "urgent"], help="render this email from the last run")
    ap.add_argument("--send", action="store_true", help="with --preview: also send it (uses EMAIL_MODE)")
    ap.add_argument("--test", action="store_true", help="send a short test email with the current settings")
    ap.add_argument("--out", default="email_preview.html", help="where --preview writes the HTML")
    args = ap.parse_args()
    cfg = settings()
    if args.test:
        page = (f'<div style="font-family:{SANS};padding:24px;"><h2 style="color:{INK};">Email works</h2>'
                f'<p>DNCR Federal Funds Matchmaker can send to {", ".join(map(_e, cfg["to"]))}.</p></div>')
        print(send("DNCR Matchmaker: test email", page, "Email works.", cfg))
        return
    if args.preview:
        from agents.alert import today_et

        ctx = build_context(state_from_storage(), today_et(), cfg)
        subject, page, text = render(args.preview, ctx)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(page)
        print(f"{subject}\nwrote {args.out}")
        if args.send:
            print(send(subject, page, text, cfg))
        return
    ap.print_help()


if __name__ == "__main__":
    main()
