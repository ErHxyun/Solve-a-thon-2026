"""Alert (owner: lead). Spec: docs/agents/alert.md"""
from agents import alert
from core import storage


def state(days, opp_id="1"):
    opp = {"id": opp_id, "number": opp_id, "title": "t", "status": "posted", "days_to_close": days}
    return {"opportunities": [opp], "top": [{"opp_id": opp_id}]}


def kinds(out):
    return [a["kind"] for a in out["alerts"]]


def test_closing_soon():
    assert "closing_soon" in kinds(alert.run(state(7)))


def test_not_soon():
    assert "closing_soon" not in kinds(alert.run(state(30)))


def test_first_run_flags_nothing_as_new():
    assert "new_since_yesterday" not in kinds(alert.run(state(30)))


def test_new_item_after_previous_run():
    storage.write_json("opportunities/previous_ids.json", ["other"])
    assert "new_since_yesterday" in kinds(alert.run(state(30)))


def test_waitlist_21_days():
    storage.write_json("decisions/dncr.json", [{"opp_id": "1", "decision": "Waitlist"}])
    assert "waitlist_21_days" in kinds(alert.run(state(15)))
