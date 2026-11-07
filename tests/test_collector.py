"""Collector (owner: lead). Spec: docs/agents/collector.md"""
from datetime import date

from agents.collector import normalize


def by_id(raw_records, opp_id):
    return next(r for r in raw_records if str(r["id"]) == opp_id)


def test_normalize_posted_orlp(raw_records):
    o = normalize(by_id(raw_records, "362715"), today=date(2026, 11, 7))
    assert o.status == "posted"
    assert "00" in {a.id for a in o.applicant_types}
    assert o.close_date == "2030-11-01"
    assert o.days_to_close > 0


def test_normalize_forecast(raw_records):
    o = normalize(by_id(raw_records, "322314"))
    assert o.status == "forecasted" and o.description


def test_html_is_stripped_everywhere(raw_records):
    for r in raw_records:
        o = normalize(r)
        assert "<p" not in o.description and "&amp;" not in o.title and "&ndash;" not in o.title


def test_run_dedups_and_writes(monkeypatch):
    from agents import collector
    from core import storage

    out = collector.run({})
    numbers = [o["number"] for o in out["opportunities"]]
    assert len(numbers) == len(set(numbers))
    assert storage.exists("opportunities/latest.json")


# ---------- live full pull, against a fake Grants.gov (no network) ----------
import httpx  # noqa: E402
import pytest  # noqa: E402

from agents import collector as C  # noqa: E402


class FakeGrantsGov:
    """search2 pages through `n` hits; fetchOpportunity returns fixture records (fails for ids in `broken`)."""

    def __init__(self, raw_records, n=None, broken=()):
        self.records = {str(r["id"]): r for r in raw_records}
        self.ids = list(self.records)[: n or len(self.records)]
        self.broken, self.calls = set(broken), {"search": 0, "fetch": 0}

    def __call__(self, url, payload):
        if url == C.SEARCH_URL:
            self.calls["search"] += 1
            if payload.get("keyword"):
                return {"hitCount": 2, "oppHits": [{"id": i} for i in self.ids[:2]]}
            start, rows = payload["startRecordNum"], payload["rows"]
            assert payload["eligibilities"] == C.STATE_ELIGIBLE
            return {"hitCount": len(self.ids), "oppHits": [{"id": i} for i in self.ids[start:start + rows]]}
        self.calls["fetch"] += 1
        opp_id = str(payload["opportunityId"])
        if opp_id in self.broken:
            raise C.GrantsGovError("boom")
        return self.records[opp_id]


def test_search_all_pages_until_hit_count(monkeypatch, raw_records):
    fake = FakeGrantsGov(raw_records)
    monkeypatch.setattr(C, "_post", fake)
    monkeypatch.setattr(C, "PAGE_ROWS", 7)
    monkeypatch.setattr(C, "search_page", lambda kw="", start=0, rows=7, eligibilities="": fake(
        C.SEARCH_URL, {"keyword": kw, "startRecordNum": start, "rows": 7, "eligibilities": eligibilities}))
    hits, total = C.search_all()
    assert total == len(raw_records) and len(hits) == len(raw_records)
    assert fake.calls["search"] == -(-len(raw_records) // 7)  # ceil(40 / 7) pages


def test_search_all_respects_cap(monkeypatch, raw_records):
    monkeypatch.setattr(C, "_post", FakeGrantsGov(raw_records))
    hits, _ = C.search_all(cap=10)
    assert len(hits) <= C.PAGE_ROWS


def test_fetch_details_caches_and_survives_failures(monkeypatch, raw_records):
    fake = FakeGrantsGov(raw_records, broken={"362715"})
    monkeypatch.setattr(C, "_post", fake)
    ids = [str(r["id"]) for r in raw_records]
    details, stats = C.fetch_details(ids, workers=4)
    assert stats["failed"] == 1 and stats["fetched"] == len(ids) - 1 and len(details) == len(ids) - 1
    _, again = C.fetch_details(ids, workers=4)  # same day: served from cache, only the broken one is retried
    assert again["cached"] == len(ids) - 1 and again["failed"] == 1


def test_live_run_writes_records_baseline_and_counts(monkeypatch, raw_records):
    from core import config, storage

    monkeypatch.setattr(C, "_post", FakeGrantsGov(raw_records))
    monkeypatch.setattr(config, "COLLECTOR_MODE", "live")
    out = C.run({})
    stats = storage.read_json("runs/collector_latest.json")
    assert stats["mode"] == "live" and stats["listed"] == len(raw_records) and stats["normalized"] == len(out["opportunities"])
    assert storage.exists(f"raw/{C.date.today().isoformat()}/grants.json")
    assert storage.exists(f"baseline/{C.date.today().isoformat()}/keyword_hits.json")


def test_zero_records_fails_the_run(monkeypatch):
    monkeypatch.setattr(C, "_post", lambda url, payload: {"hitCount": 0, "oppHits": []})
    with pytest.raises(C.GrantsGovError):
        C.collect_live()


def test_transient_errors_are_retried_but_400_is_not():
    req = httpx.Request("POST", C.SEARCH_URL)
    assert C._is_transient(httpx.HTTPStatusError("x", request=req, response=httpx.Response(503, request=req)))
    assert not C._is_transient(httpx.HTTPStatusError("x", request=req, response=httpx.Response(400, request=req)))
    assert C._is_transient(httpx.ConnectTimeout("t"))
