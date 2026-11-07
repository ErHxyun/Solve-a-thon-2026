"""Fit Scorer (owner: Criteria). Spec: docs/agents/fit_scorer.md — "DNCR Grant Scoring Rules (Final)"."""
from datetime import date

import pytest

from agents import fit_scorer as fs
from core.reference import load_profile
from core.schemas import ApplicantType, GateResult, Opportunity

TODAY = date(2026, 11, 7)


def run_on(opportunities):
    gates = [GateResult(opp_id=o["id"], passed=True, eligible=True, open_or_coming=True).model_dump() for o in opportunities]
    return fs.run({"opportunities": opportunities, "gates": gates})


def by_id(items, opp_id):
    return next(s for s in items if s["opp_id"] == opp_id)


# ---------- whole-feed behaviour ----------
def test_sorted_bounded_and_short_list(opportunities):
    out = run_on(opportunities)
    scores = [s["score"] for s in out["scored"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 100 for s in scores)
    assert len(out["top"]) <= fs.config.TOP_N
    assert all(s["suggested_decision"] != "Skip" for s in out["top"])  # cards only for real candidates


def test_known_good_ranks_first(opportunities):
    top = run_on(opportunities)["scored"][0]
    assert top["opp_id"] == "361480"  # NEH Collections Stewardship (Field Notes worked example)
    assert top["objective_id"] == "1.5" and top["suggested_decision"] == "Chase"


def test_small_batch_uses_full_feed_calibration(opportunities):
    out = run_on(opportunities)
    assert max(s["components"]["S"] for s in out["scored"]) > 50  # S is not flattened to 0


def test_every_item_explains_itself(opportunities):
    ids = {o.objective_id for o in load_profile().objectives}
    for s in run_on(opportunities)["scored"]:
        assert s["objective_id"] in ids and s["rationale"].startswith("Final score")
        assert {"A", "S", "K", "U", "W", "H"} <= set(s["components"])


def test_forecasts_never_high(opportunities):
    for s in run_on(opportunities)["scored"]:
        if s["watch"]:
            assert s["confidence"] != "High"


# ---------- rubric rules ----------
def test_final_formula_and_multiplier():
    assert fs.final_score(80, 3, 100) == pytest.approx(80.0)
    assert fs.final_score(80, 0, 0) == pytest.approx(56.0)
    assert fs.final_score(20, 3, 100) < fs.A_SKIP  # urgency and low workload cannot rescue a bad fit


def test_decision_rules():
    assert fs.decide(40, 90, True, False, "x")[0] == "Skip"
    assert fs.decide(80, 70, False, False, "NC Commerce")[0] == "Route"
    assert fs.decide(80, 70, True, True, "x")[0] == "Waitlist"
    assert fs.decide(80, 65, True, False, "x")[0] == "Chase"
    assert fs.decide(60, 55, True, False, "x")[0] == "You decide"


def test_route_rules():
    ob = load_profile().objectives[0]
    eda = Opportunity(id="1", number="N", title="EDA FY25 Disaster Supplemental", agency_name="Economic Development Administration")
    assert fs.route(eda, ob) == ("NC Commerce", False)
    orlp = Opportunity(id="2", number="N", title="Outdoor Recreation Legacy Partnership Program", agency_name="National Park Service")
    assert fs.route(orlp, ob)[1] is True


def test_research_penalty():
    nih = Opportunity(id="1", number="N", title="t", agency_code="HHS-NIH11")
    assert fs.research_penalty(nih)[0] == 0.4
    nsf = Opportunity(id="2", number="N", title="t", agency_name="U.S. National Science Foundation")
    assert fs.research_penalty(nsf)[0] == 0.75


def test_urgency_one_time_lift_and_a250_expiry():
    ob = next(o for o in load_profile().objectives if o.objective_id == "1.5")
    assert fs.urgency(ob, "Disaster Supplemental for Archives", TODAY)[0] == 2
    a250 = next(o for o in load_profile().objectives if o.objective_id == "3.2")
    assert fs.urgency(a250, "t", TODAY)[0] == 3 and fs.urgency(a250, "t", date(2027, 1, 2))[0] == 1


def test_workload_is_reproducible_and_ordered():
    simple, standard, complex_ = (fs.simulate_hours(a)["H"] for a in ("simple", "standard", "complex"))
    assert simple < standard < complex_
    assert fs.simulate_hours("standard") == fs.simulate_hours("standard")
    assert fs.workload_score(20) == 100 and fs.workload_score(150) == 0


def test_rounds_are_merged_latest_deadline_kept():
    base = dict(title="Fiscal Year 2026 Flood Mitigation Assistance", agency_name="FEMA",
                applicant_types=[ApplicantType(id="00", description="State governments")], description="flood mitigation resilience")
    a = Opportunity(id="1", number="A", close_date="2027-02-26", **base)
    b = Opportunity(id="2", number="B", close_date="2028-01-31", **{**base, "title": "Fiscal Year 2027 Flood Mitigation Assistance"})
    items, _ = fs.score_all([a, b], {"1", "2"}, today=TODAY)
    assert len(items) == 1 and items[0].opp_id == "2" and items[0].components["rounds"] == 2


def test_already_yours_needs_title_not_only_cfda():
    orlp = Opportunity(id="1", number="N", title="Outdoor Recreation Legacy Partnership Program", cfda=["15.916"])
    nea = Opportunity(id="2", number="N", title="NEA Partnership Agreements", cfda=["45.025"])
    assert not fs._already_yours(orlp) and fs._already_yours(nea)
