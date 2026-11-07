"""Route Agent (owner: LLM-B). Spec: docs/agents/route_agent.md"""
from agents import route_agent
from core.schemas import Card, Opportunity


def card(who="DNCR State Parks") -> Card:
    return Card(opp_id="1", can_dncr_apply="yes", who_applies=who, objective_id="3.5", division="State Parks",
                money_type="competitive", prerequisites=[], worth_it="yes", summary="s", why_fit="f",
                why_not_fit="n", evidence=[])


def test_dhs_routes_to_emergency_management():
    opp = Opportunity(id="1", number="N", title="Hazard Mitigation", top_agency_code="DHS", close_date="2026-12-01")
    assert "Emergency Management" in route_agent.run(opp, card()).route_to


def test_keyword_rule():
    opp = Opportunity(id="1", number="N", title="Clean School Bus Program")
    assert "Public Instruction" in route_agent.run(opp, card()).route_to


def test_no_match_uses_card():
    opp = Opportunity(id="1", number="N", title="Something else")
    assert route_agent.run(opp, card("Arts Council")).route_to == "Arts Council"


def test_deadline_comes_from_listing_and_offline_draft():
    opp = Opportunity(id="1", number="N", title="Hazard Mitigation", top_agency_code="DHS", close_date="2026-12-01")
    draft = route_agent.run(opp, card())
    assert draft.deadline == "2026-12-01" and draft.message.startswith("[offline draft]")
