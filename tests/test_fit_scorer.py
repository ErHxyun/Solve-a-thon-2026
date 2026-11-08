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


def test_known_good_items_lead_the_list(opportunities):
    # every record passes here (no Gatekeeper), so foreign / other-region listings are still in the ranking
    scored = run_on(opportunities)["scored"]
    top5 = [s["opp_id"] for s in scored[:5]]
    assert top5[0] == "362715"                      # ORLP: plan fit 72 and a large award (V = 0.94)
    assert "361480" in top5                         # NEH Collections Stewardship (Field Notes worked example)
    neh = by_id(scored, "361480")
    assert neh["objective_id"] == "1.5" and neh["suggested_decision"] == "Chase"


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
    assert fs.final_score(80, 1, 1, 3, 100) == pytest.approx(80.0)       # every factor at its best -> Final = A
    assert fs.final_score(80, 0, 0, 0, 0) == pytest.approx(24.0)         # only the 0.30 base is left
    assert fs.final_score(80, 0.25, 0.5, 1, 50) == pytest.approx(80 * (0.30 + 0.05 + 0.15 + 0.05 + 0.025))
    assert fs.final_score(30, 1, 1, 3, 100) < fs.FINAL_CHASE             # the other factors cannot rescue a bad fit


def test_decision_rules():
    assert fs.decide(30, 90, True, False, "x")[0] == "Skip"            # A below 35
    assert fs.decide(80, 70, False, False, "NC Commerce")[0] == "Route"
    assert fs.decide(80, 70, True, True, "x")[0] == "Waitlist"
    assert fs.decide(80, 40, True, False, "x")[0] == "Chase"           # Final at or above 38
    assert fs.decide(60, 30, True, False, "x")[0] == "You decide"


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


def test_excel_report_builds(tmp_path):
    from openpyxl import load_workbook

    from scripts import score_report

    opps, gates, items, cal = score_report.build(10)
    out = tmp_path / "report.xlsx"
    score_report.write(out, opps, gates, items, cal, 10)
    wb = load_workbook(out)
    assert wb.sheetnames == ["Top 10", "How scored", "Funnel", "DNCR objectives"]
    assert wb["Top 10"].cell(5, 3).value  # first ranked title


# ---------- factors added in the Final rubric (P chance, V value, agency-type floor) ----------
STATE_ONLY = [ApplicantType(id="00", description="State governments")]


def test_chance_formula_wording_and_negation():
    formula = Opportunity(id="1", number="N", title="Historic Preservation Fund grants to State Historic Preservation Offices")
    assert fs.win_probability(formula)[:2] == (1.0, "1 formula")
    negated = Opportunity(id="2", number="N", title="Competitive pilot",
                          description="This program is in addition to formula grants to states.")
    assert fs.win_probability(negated)[1] != "1 formula"


def test_chance_states_only_uses_award_count():
    with_count = Opportunity(id="1", number="N", title="t", applicant_types=STATE_ONLY, expected_awards=28)
    assert fs.win_probability(with_count)[0] == pytest.approx(0.5)      # 28 awards / 56 states and territories
    unknown = Opportunity(id="2", number="N", title="t", applicant_types=STATE_ONLY)
    assert fs.win_probability(unknown)[0] == fs.P_STATE_UNKNOWN
    shared = Opportunity(id="3", number="N", title="t", expected_awards=28,
                         applicant_types=STATE_ONLY + [ApplicantType(id="12", description="Nonprofits")])
    assert fs.win_probability(shared)[0] == fs.P_DEFAULT                # anyone can apply -> default estimate


def test_chance_published_rate():
    mfa = Opportunity(id="1", number="N", title="Museums for America")
    assert fs.win_probability(mfa)[0] == pytest.approx(0.32)


def test_value_from_award_amounts():
    both = Opportunity(id="1", number="N", title="t", award_floor=50_000, award_ceiling=500_000)
    award, _ = fs.typical_award(both)
    assert award == pytest.approx((50_000 * 500_000) ** 0.5)            # geometric midpoint
    ceiling_only = Opportunity(id="2", number="N", title="t", award_ceiling=300_000)
    assert fs.typical_award(ceiling_only)[0] == pytest.approx(100_000)
    tiny_floor = Opportunity(id="3", number="N", title="t", award_floor=1, award_ceiling=300_000)
    assert fs.typical_award(tiny_floor)[0] == pytest.approx(100_000)    # floor < 1% of ceiling is ignored
    assert fs.value_score(10_000) == 0 and fs.value_score(3_000_000) == 1 and fs.value_score(9e9) == 1
    assert fs.value_score(None) == fs.V_UNKNOWN


def test_agency_type_floor():
    shpo = Opportunity(id="1", number="N", title="Underrepresented Communities Grants",
                       description="Open to State Historic Preservation Offices and tribes.")
    assert fs.plan_match_A(20.0, 1.0, shpo) == (70.0, True)
    plain = Opportunity(id="2", number="N", title="t", description="Open to nonprofits.")
    assert fs.plan_match_A(20.0, 0.75, plain) == (15.0, False)


def test_rationale_shows_every_factor(opportunities):
    neh = by_id(run_on(opportunities)["scored"], "361480")
    for part in ("chance", "value", "urgency", "workload", "$"):
        assert part in neh["rationale"]
    assert {"P", "V", "award"} <= set(neh["components"])
