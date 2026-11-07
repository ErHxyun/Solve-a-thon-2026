"""Fit Scorer (owner: lead). Spec: docs/agents/fit_scorer.md"""
from agents import fit_scorer
from core.schemas import GateResult


def run_on(opportunities):
    gates = [GateResult(opp_id=o["id"], passed=True, eligible=True, open_or_coming=True).model_dump() for o in opportunities]
    return fit_scorer.run({"opportunities": opportunities, "gates": gates})


def test_sorted_bounded_and_top_n(opportunities):
    out = run_on(opportunities)
    scores = [s["score"] for s in out["scored"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 100 for s in scores)
    assert len(out["top"]) <= fit_scorer.config.TOP_N


def test_known_good_in_top_3(opportunities):
    top3 = [s["opp_id"] for s in run_on(opportunities)["scored"][:3]]
    assert "361480" in top3  # NEH Collections Stewardship (Field Notes worked example)


def test_forecasts_capped_at_medium(opportunities):
    forecasts = {o["id"] for o in opportunities if o["status"] == "forecasted"}
    for s in run_on(opportunities)["scored"]:
        if s["opp_id"] in forecasts:
            assert s["confidence"] != "High"


def test_every_item_has_a_real_objective(opportunities):
    from core.reference import load_profile

    ids = {o.objective_id for o in load_profile().objectives}
    assert all(s["objective_id"] in ids for s in run_on(opportunities)["scored"])
