"""Card Agent (owner: lead). Spec: docs/agents/card_agent.md"""
from agents import card_agent
from core.reference import load_profile
from core.schemas import Card, Opportunity, ScoreBreakdown, ScoredItem

OPP = Opportunity(id="361480", number="N", title="Collections Stewardship", description="Preserve humanities collections.")
ITEM = ScoredItem(opp_id="361480", score=80, confidence="High", breakdown=ScoreBreakdown(), objective_id="1.5")


def test_prompt_contains_listing_and_objective():
    prompt = card_agent.build_user_prompt(OPP, ITEM, load_profile())
    assert "Collections Stewardship" in prompt and "Objective 1.5" in prompt


def test_feedback_goes_into_retry_prompt():
    prompt = card_agent.build_user_prompt(OPP, ITEM, load_profile(), feedback="quote X not found")
    assert "quote X not found" in prompt


def test_offline_placeholder_is_valid_card():
    card = card_agent.make_card(OPP, ITEM, load_profile())
    assert isinstance(card, Card) and "[offline placeholder]" in card.can_dncr_apply


def test_model_cannot_change_opp_id(monkeypatch):
    fake = card_agent.placeholder_card(OPP, ITEM).model_copy(update={"opp_id": "WRONG"})
    monkeypatch.setattr(card_agent, "chat_json", lambda **kw: fake)
    assert card_agent.make_card(OPP, ITEM, load_profile()).opp_id == "361480"
