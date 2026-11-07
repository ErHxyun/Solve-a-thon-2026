"""Apply Assist (owner: LLM-B). Spec: docs/agents/apply_assist.md"""
from agents import apply_assist
from core.guard import scrub
from core.schemas import Card, Opportunity

OPP = Opportunity(id="1", number="N", title="Battlefield Land Acquisition", close_date="2026-12-15", days_to_close=38,
                  url="https://www.grants.gov/search-results-detail/1")
CARD = Card(opp_id="1", can_dncr_apply="yes", who_applies="DNCR SHPO", objective_id="3.6", division="SHPO",
            money_type="competitive", prerequisites=["1:1 match"], worth_it="yes", summary="s", why_fit="f",
            why_not_fit="n", evidence=[])


def test_offline_pack_uses_process_map():
    pack = apply_assist.run(OPP, CARD)
    assert pack.opp_id == "1"
    assert pack.sf424_fields_to_gather == apply_assist.load_process_map()["sf424_fields_to_gather"]


def test_pack_contains_no_sensitive_values():
    pack = apply_assist.run(OPP, CARD)
    text = " ".join(pack.requirements + pack.sf424_fields_to_gather + pack.outline + pack.timeline + [pack.email_to_division_lead])
    assert scrub(text).findings == []


def test_model_cannot_change_opp_id(monkeypatch):
    fake = apply_assist.run(OPP, CARD).model_copy(update={"opp_id": "WRONG"})
    monkeypatch.setattr(apply_assist, "chat_json", lambda **kw: fake)
    assert apply_assist.run(OPP, CARD).opp_id == "1"
