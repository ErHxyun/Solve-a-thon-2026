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
