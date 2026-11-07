"""Card Agent (owner: LLM-A). Spec: docs/agents/card_agent.md"""
import pytest
from rapidfuzz import fuzz

from agents import card_agent
from core import config
from core.llm import LLMUnavailable
from core.reference import load_profile
from core.schemas import Card, Evidence, Opportunity, ScoreBreakdown, ScoredItem

OPP = Opportunity(id="361480", number="N", title="Collections Stewardship", description="Preserve humanities collections.")
ITEM = ScoredItem(opp_id="361480", score=80, confidence="High", breakdown=ScoreBreakdown(), objective_id="1.5")


def _opp(opportunities, opp_id) -> Opportunity:
    return Opportunity(**next(o for o in opportunities if o["id"] == opp_id))


def _item(opp_id, **kw) -> ScoredItem:
    base = dict(opp_id=opp_id, score=70, confidence="High", breakdown=ScoreBreakdown(), objective_id="5.1", division="SHPO")
    return ScoredItem(**{**base, **kw})


def _llm_card(opp_id="361480", **kw) -> Card:
    base = dict(opp_id=opp_id, can_dncr_apply="Yes.", who_applies="DNCR – Museums", objective_id="1.5", division="Museums",
                money_type="competitive", prerequisites=[], worth_it="Worth it.", summary="Pays for collections care.",
                why_fit="Fits digitization.", why_not_fit="Competitive.", evidence=[])
    return Card(**{**base, **kw})


# ---- prompt -------------------------------------------------------------- #
def test_prompt_contains_listing_and_objective():
    prompt = card_agent.build_user_prompt(OPP, ITEM, load_profile())
    assert "Collections Stewardship" in prompt and "Objective 1.5" in prompt


def test_feedback_goes_into_retry_prompt():
    prompt = card_agent.build_user_prompt(OPP, ITEM, load_profile(), feedback="quote X not found")
    assert "quote X not found" in prompt


def test_prompt_separates_quotable_text_from_facts(opportunities):
    opp = _opp(opportunities, "363771")
    prompt = card_agent.build_user_prompt(opp, _item(opp.id, objective_id="1.5"), load_profile())
    listing, facts = prompt.split("## FACTS", 1)
    assert "LISTING TEXT" in listing and "Award: floor" in facts  # computed numbers live only in FACTS
    assert "Award" not in listing.split("## PLAN TEXT")[0].split("Description:")[0]


def test_placeholder_plan_text_is_not_offered_for_quoting():
    # dncr_profile.json still holds "TODO (P1)" wording; the model must not quote it
    prompt = card_agent.build_user_prompt(OPP, ITEM, load_profile())
    assert "TODO" not in prompt and "do not quote the plan" in prompt


# ---- money type hint ----------------------------------------------------- #
def test_t3_disaster_supplemental_hint(opportunities):
    opp = _opp(opportunities, "359225")  # EDA FY25 Disaster Supplemental
    hint, why = card_agent.money_type_hint(opp, _item(opp.id))
    assert hint == "one-time disaster" and ("disaster" in why or "supplemental" in why)


def test_cfda_match_alone_does_not_force_type(opportunities):
    # ORLP shares CFDA 15.916 with the LWCF state program but is competitive (GenAI log 2026-11-07),
    # even when the Fit Scorer tags it already_yours
    opp = _opp(opportunities, "362715")
    hint, _ = card_agent.money_type_hint(opp, _item(opp.id, already_yours=True))
    assert hint == "competitive"


@pytest.mark.parametrize("opp_id", ["362717", "359695", "360611"])  # RARI, NIH chemical threats, NIH COBRE
def test_passing_mention_is_not_a_disaster_program(opportunities, opp_id):
    """'disaster resilience' / 'chemical disasters' / 'supplemental start-up packages' were false positives."""
    opp = _opp(opportunities, opp_id)
    assert card_agent.money_type_hint(opp, _item(opp.id))[0] != "one-time disaster"


# ---- code-owned fields --------------------------------------------------- #
def test_offline_placeholder_is_valid_card():
    card = card_agent.make_card(OPP, ITEM, load_profile())
    assert isinstance(card, Card) and "[offline placeholder]" in card.can_dncr_apply


def test_model_cannot_change_opp_id(monkeypatch):
    fake = _llm_card(opp_id="WRONG")
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: fake)
    assert card_agent.make_card(OPP, ITEM, load_profile()).opp_id == "361480"


def test_ranked_objective_wins_and_quotes_are_cleaned(monkeypatch):
    fake = _llm_card(objective_id="9.9", why_not_fit="  ", evidence=[
        Evidence(field="summary", quote='  "Preserve   humanities collections."  ', source="listing"),
        Evidence(field="why_fit", quote="Preserve humanities collections.", source="listing"),
        Evidence(field="x", quote="   ", source="listing"),
    ])
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: fake)
    card = card_agent.make_card(OPP, ITEM, load_profile())
    assert card.objective_id == "1.5"
    assert "objective 9.9, which is not in the DNCR plan" in card.why_not_fit   # invented id: kept visible
    assert [e.quote for e in card.evidence] == ["Preserve humanities collections."]


def test_empty_why_not_fit_becomes_not_stated(monkeypatch):
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: _llm_card(why_not_fit="  "))
    assert card_agent.make_card(OPP, ITEM, load_profile()).why_not_fit == "not stated"


def test_objective_disagreement_stays_visible(monkeypatch):
    """Review 2026-11-07: overwriting objective_id hid the model's disagreement from the Verifier and the user."""
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: _llm_card(objective_id="5.1", why_not_fit="Competitive."))
    card = card_agent.make_card(OPP, ITEM, load_profile())
    assert card.objective_id == "1.5"                                      # UI and score stay consistent
    assert card.why_not_fit.startswith("Competitive.")
    assert "objective 5.1 fits better than the ranked objective 1.5" in card.why_not_fit


def test_agreeing_objective_adds_no_note(monkeypatch):
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: _llm_card(objective_id="1.5", why_not_fit="Competitive."))
    assert card_agent.make_card(OPP, ITEM, load_profile()).why_not_fit == "Competitive."


# ---- invented figures ---------------------------------------------------- #
def test_supported_figures_pass(opportunities):
    opp = _opp(opportunities, "363771")  # ceiling $75,000
    card = _llm_card(opp.id, worth_it="Up to $75,000 for small museums.")
    assert card_agent.unsupported_figures(card, opp) == []


def test_invented_figures_are_repaired_then_masked(monkeypatch, opportunities):
    opp = _opp(opportunities, "363771")
    calls = []

    def fake(**kw):
        calls.append(kw["user"])
        return _llm_card(opp.id, worth_it="Up to $9 million, due March 3, 2027.")

    monkeypatch.setattr(card_agent, "chat_json", fake)
    card = card_agent.make_card(opp, _item(opp.id, objective_id="1.5"), load_profile())
    assert len(calls) == 2 and "not in the listing" in calls[1]       # one self-repair call
    assert "$9 million" not in card.worth_it and "[see listing]" in card.worth_it


# ---- offline extractive card --------------------------------------------- #
@pytest.mark.parametrize("opp_id", ["359225", "363771", "362715", "361480"])
def test_offline_quotes_are_exact_substrings(opportunities, opp_id):
    """Offline cards must pass the Verifier: every listing quote is found verbatim."""
    opp = _opp(opportunities, opp_id)
    card = card_agent.placeholder_card(opp, _item(opp.id), load_profile())
    haystack = card_agent._listing_text(opp).lower()
    assert card.evidence, "offline card should carry at least one quote"
    for ev in card.evidence:
        if ev.source == "listing":
            assert fuzz.partial_ratio(ev.quote.lower(), haystack) == 100


def test_offline_card_flags_risks(opportunities):
    opp = _opp(opportunities, "363771")  # cost sharing required
    card = card_agent.placeholder_card(opp, _item(opp.id), load_profile())
    assert "cost share" in card.why_not_fit.lower()
    assert any("match" in p.lower() for p in card.prerequisites)


# ---- cache --------------------------------------------------------------- #
def test_cache_skips_second_llm_call(monkeypatch):
    calls = []
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: calls.append(1) or _llm_card())
    first = card_agent.make_card(OPP, ITEM, load_profile())
    second = card_agent.make_card(OPP, ITEM, load_profile())
    assert len(calls) == 1 and first == second


def test_cache_hits_next_day_when_only_days_left_and_score_change(monkeypatch, opportunities):
    """Review 2026-11-07: days_to_close and score change daily, so they must not be in the key."""
    calls = []
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: calls.append(1) or _llm_card("363771"))
    opp = _opp(opportunities, "363771")
    today = _item(opp.id, score=72.0, confidence="High", objective_id="1.5")
    card_agent.make_card(opp, today, load_profile())
    tomorrow_opp = opp.model_copy(update={"days_to_close": opp.days_to_close - 1, "ingested_at": "2026-11-08T11:00:00+00:00"})
    reweighted = today.model_copy(update={"score": 64.5, "confidence": "Medium", "watch": True})
    card_agent.make_card(tomorrow_opp, reweighted, load_profile())
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["description", "close_date", "award_ceiling", "objective"])
def test_cache_misses_when_card_inputs_change(monkeypatch, opportunities, change):
    calls = []
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: calls.append(1) or _llm_card("363771"))
    opp = _opp(opportunities, "363771")
    item = _item(opp.id, objective_id="1.5")
    card_agent.make_card(opp, item, load_profile())
    if change == "objective":
        item = item.model_copy(update={"objective_id": "3.2"})
    else:
        opp = opp.model_copy(update={change: {"description": opp.description + " Amended.",
                                               "close_date": "2026-12-01", "award_ceiling": 90000.0}[change]})
    card_agent.make_card(opp, item, load_profile())
    assert len(calls) == 2


def test_prompt_has_no_daily_values(opportunities):
    opp = _opp(opportunities, "361480")  # days_to_close = 38 in the fixture
    item = _item(opp.id, score=81.6, objective_id="1.5")
    prompt = card_agent.build_user_prompt(opp, item, load_profile())
    assert "81.6" not in prompt and "days from today" not in prompt and "Closes 2026-12-15" in prompt


def test_retry_with_feedback_bypasses_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: calls.append(kw["user"]) or _llm_card())
    card_agent.make_card(OPP, ITEM, load_profile())
    card_agent.make_card(OPP, ITEM, load_profile(), feedback="quote X not found")
    assert len(calls) == 2 and "quote X not found" in calls[1]


def test_llm_failure_is_not_cached(monkeypatch):
    def boom(**kw):
        raise LLMUnavailable("down")

    monkeypatch.setattr(card_agent, "chat_json", boom)
    card_agent.make_card(OPP, ITEM, load_profile())
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: _llm_card())
    assert "[offline placeholder]" not in card_agent.make_card(OPP, ITEM, load_profile()).can_dncr_apply


def test_run_returns_one_card_per_top_item(opportunities):
    top = [_item(o["id"]).model_dump() for o in opportunities[:5]]
    out = card_agent.run({"opportunities": opportunities, "top": top})
    assert [c["opp_id"] for c in out["cards"]] == [t["opp_id"] for t in top]
    assert config.LLM_ENABLED is False
