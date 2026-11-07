"""Alert (owner: lead). Spec: docs/agents/alert.md"""
from datetime import date, timedelta

from agents import alert
from core import storage

TODAY = date(2026, 11, 7)


def opp(opp_id="1", days=None, status="posted", close=None, stale_days=None):
    close = close or ((TODAY + timedelta(days=days)).isoformat() if days is not None else None)
    return {"id": opp_id, "number": opp_id, "title": f"Grant {opp_id}", "status": status,
            "close_date": close, "days_to_close": stale_days if stale_days is not None else days}


def state(*opps, top=None):
    return {"opportunities": list(opps), "top": [{"opp_id": i} for i in (top if top is not None else [o["id"] for o in opps])]}


def kinds(out, opp_id=None):
    return [a["kind"] for a in out["alerts"] if opp_id is None or a["opp_id"] == opp_id]


def decide(*pairs):
    storage.write_json("decisions/dncr.json", [{"opp_id": i, "decision": d, "at": f"2026-11-0{n}T12:00:00+00:00"}
                                               for n, (i, d) in enumerate(pairs, start=1)])


# ---- the five spec tests (kept from the baseline) ------------------------ #
def test_closing_soon():
    assert "closing_soon" in kinds(alert.run(state(opp(days=7)), today=TODAY))


def test_not_soon():
    assert "closing_soon" not in kinds(alert.run(state(opp(days=30)), today=TODAY))


def test_first_run_flags_nothing_as_new():
    assert "new_since_yesterday" not in kinds(alert.run(state(opp(days=30)), today=TODAY))


def test_new_item_after_previous_run():
    storage.write_json("opportunities/previous_ids.json", ["other"])
    assert "new_since_yesterday" in kinds(alert.run(state(opp(days=30)), today=TODAY))


def test_waitlist_21_days():
    decide(("1", "Waitlist"))
    assert "waitlist_21_days" in kinds(alert.run(state(opp(days=15)), today=TODAY))


# ---- run date, not the snapshot ------------------------------------------ #
def test_days_left_come_from_close_date_and_run_date():
    # Collector stored 30 days at ingest; on the run date only 5 are left
    out = alert.run(state(opp(close=(TODAY + timedelta(days=5)).isoformat(), stale_days=30)), today=TODAY)
    assert out["alerts"][0]["days_to_close"] == 5 and "in 5 days" in out["alerts"][0]["message"]


def test_closed_and_rolling_items_are_quiet():
    out = alert.run(state(opp("a", days=-1), opp("b", close=None)), today=TODAY)
    assert "closing_soon" not in kinds(out)


def test_forecast_never_closing_soon():
    assert kinds(alert.run(state(opp(days=3, status="forecasted")), today=TODAY)) == []


# ---- "since yesterday" --------------------------------------------------- #
def test_new_since_previous_day_and_rerun_same_day():
    alert.run(state(opp("a", days=30)), today=TODAY - timedelta(days=1))
    first = alert.run(state(opp("a", days=30), opp("b", days=30)), today=TODAY)
    rerun = alert.run(state(opp("a", days=30), opp("b", days=30)), today=TODAY)
    assert kinds(first, "b") == ["new_since_yesterday"] and kinds(first, "a") == []
    assert kinds(rerun, "b") == ["new_since_yesterday"]   # a second run today still compares with yesterday


def test_rerun_on_day_one_flags_nothing():
    alert.run(state(opp("a", days=30)), today=TODAY)
    assert "new_since_yesterday" not in kinds(alert.run(state(opp("a", days=30), opp("b", days=30)), today=TODAY))


def test_history_is_pruned():
    for n in range(20):
        alert.run(state(opp(days=30)), today=TODAY - timedelta(days=n))
    assert len(storage.read_json(alert.HISTORY_PATH)) == alert.HISTORY_DAYS


# ---- waitlist ------------------------------------------------------------ #
def test_waitlist_opening_when_forecast_becomes_posted():
    decide(("1", "Waitlist"))
    alert.run(state(opp(status="forecasted", close="2027-01-15")), today=TODAY - timedelta(days=1))
    out = alert.run(state(opp(days=40)), today=TODAY)
    assert kinds(out) == ["waitlist_opening"]


def test_waitlist_alert_even_when_off_the_short_list():
    decide(("1", "Waitlist"))
    assert kinds(alert.run(state(opp(days=12), top=[]), today=TODAY)) == ["waitlist_21_days"]


def test_waitlisted_item_gets_one_alert_not_two():
    decide(("1", "Waitlist"))
    assert kinds(alert.run(state(opp(days=5)), today=TODAY)) == ["waitlist_21_days"]


# ---- decisions ----------------------------------------------------------- #
def test_skipped_items_are_silent():
    decide(("1", "Skip"))
    storage.write_json("opportunities/previous_ids.json", ["other"])
    assert kinds(alert.run(state(opp(days=3)), today=TODAY)) == []


def test_latest_decision_wins():
    decide(("1", "Skip"), ("1", "Chase"))   # undo a Skip
    assert kinds(alert.run(state(opp(days=3)), today=TODAY)) == ["closing_soon"]


def test_chased_item_alerts_even_off_the_short_list():
    decide(("1", "Chase"))
    out = alert.run(state(opp(days=4), top=[]), today=TODAY)
    assert kinds(out) == ["closing_soon"] and "chasing" in out["alerts"][0]["message"]


# ---- output -------------------------------------------------------------- #
def test_alerts_sorted_and_digest_written():
    storage.write_json("opportunities/previous_ids.json", ["x"])
    out = alert.run(state(opp("a", days=9), opp("b", days=2), opp("c", days=60)), today=TODAY)
    soon = [a for a in out["alerts"] if a["kind"] == "closing_soon"]
    assert [a["opp_id"] for a in soon] == ["b", "a"]
    d = storage.read_json("alerts/digest.json")
    assert d["headline"] == "DNCR today: 3 items on the short list, 1 close within 7 days, 3 new"
    assert storage.read_json("alerts/latest.json") == out["alerts"]
