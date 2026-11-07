"""Read-only tools in core/tools.py (owner: lead)."""
from datetime import date

from core import tools as T


def test_every_tool_has_a_strict_openai_spec():
    specs = T.openai_tool_specs(list(T.TOOLS))
    assert {s["function"]["name"] for s in specs} == set(T.TOOLS)
    assert all(s["function"].get("strict") for s in specs)


def test_agent_allow_lists_only_name_real_tools():
    for names in T.AGENT_TOOLS.values():
        assert set(names) <= set(T.TOOLS)


def test_days_until_is_computed_in_code():
    out = T.days_until(T.DaysUntilArgs(target_date="2026-12-01"), today=date(2026, 11, 7))
    assert out["days"] == 24


def test_days_until_rejects_bad_date():
    assert "error" in T.TOOLS["days_until"].call('{"target_date": "next Friday"}')


def test_backward_timeline_dates_and_too_late():
    out = T.backward_timeline(T.BackwardTimelineArgs(close_date="2026-12-07", prep_days=30), today=date(2026, 11, 7))
    assert out["days_left"] == 30 and not out["too_late"]
    assert out["steps"][0]["date"] == "2026-11-07"  # decide on day -30
    late = T.backward_timeline(T.BackwardTimelineArgs(close_date="2026-11-12", prep_days=30), today=date(2026, 11, 7))
    assert late["too_late"]


def test_get_prep_time_defaults():
    assert T.get_prep_time(T.GetPrepTimeArgs(money_type="competitive"))["prep_days"] == 30


def test_get_objective_and_unknown():
    assert T.get_objective(T.GetObjectiveArgs(objective_id="5.1"))["urgency"] == "highest"
    assert "error" in T.get_objective(T.GetObjectiveArgs(objective_id="9.9"))


def test_list_objectives():
    assert len(T.list_objectives(T.ListObjectivesArgs())["objectives"]) >= 5


def test_check_annual_program_needs_title_not_just_cfda():
    nea = T.check_annual_program(T.CheckAnnualProgramArgs(cfda=["45.025"], title="NEA Partnership Agreements FY2027"))
    assert nea["already_yours"]
    orlp = T.check_annual_program(T.CheckAnnualProgramArgs(cfda=["15.916"], title="Outdoor Recreation Legacy Partnership Program"))
    assert not orlp["already_yours"] and orlp["cfda_only_matches"]  # shares 15.916 with LWCF, but a different program


def test_get_opportunity_details_from_fixture():
    out = T.get_opportunity_details(T.GetOpportunityDetailsArgs(opp_id="361480"))
    assert out["title"] == "Collections Stewardship"


def test_lookup_program_owner_handles_missing_reference(monkeypatch):
    monkeypatch.setattr(T, "_read_reference", lambda name: None)
    assert "error" in T.lookup_program_owner(T.LookupProgramOwnerArgs(top_agency_code="DHS", text="x"))


def test_tool_errors_never_raise():
    assert "error" in T.TOOLS["get_objective"].call("not json")
