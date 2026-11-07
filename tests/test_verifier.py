"""Verifier (owner: lead). Test case T5 in the test plan: a planted fake quote must be caught."""
from agents.verifier import check
from core.schemas import Card, Evidence, Opportunity, ScoreBreakdown, ScoredItem

OPP = Opportunity(id="1", number="N1", title="Battlefield Land Acquisition",
                  description="Grants to acquire and preserve threatened Civil War battlefield land.")
ITEM = ScoredItem(opp_id="1", score=80, confidence="High", breakdown=ScoreBreakdown(), objective_id="3.6")
PLAN = "Protect and restore natural and cultural resources."


def card(quotes: list[tuple[str, str]]) -> Card:
    return Card(opp_id="1", can_dncr_apply="yes", who_applies="DNCR", objective_id="3.6", division="SHPO",
                money_type="competitive", prerequisites=[], worth_it="yes", summary="s", why_fit="f",
                why_not_fit="n", evidence=[Evidence(field="why_fit", quote=q, source=s) for q, s in quotes])


def test_real_quotes_keep_confidence():
    r = check(card([("acquire and preserve threatened Civil War battlefield land", "listing"),
                    ("Protect and restore natural and cultural resources", "plan")]), OPP, ITEM, PLAN, 1)
    assert r.verified_quotes == 2 and r.confidence == "High" and not r.needs_retry


def test_fake_quote_triggers_retry_then_low():  # T5
    fake = card([("Funds may be used for broadband at all state parks", "listing")])
    first = check(fake, OPP, ITEM, PLAN, attempts=1)
    assert first.needs_retry and first.verifier_feedback
    second = check(fake, OPP, ITEM, PLAN, attempts=2)
    assert not second.needs_retry and second.confidence == "Low"


def test_no_evidence_is_low():
    assert check(card([]), OPP, ITEM, PLAN, 1).confidence == "Low"
