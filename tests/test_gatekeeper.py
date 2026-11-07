"""Gatekeeper (owner: P3). Test cases T1, T2 in the test plan. Add a test for every rule you add."""
from agents.gatekeeper import check
from core.schemas import ApplicantType, Opportunity


def make(**kw) -> Opportunity:
    base = dict(id="1", number="N1", title="t", status="posted", days_to_close=30,
                applicant_types=[ApplicantType(id="00", description="State governments")])
    base.update(kw)
    return Opportunity(**base)


def test_state_eligible_passes():
    assert check(make()).passed


def test_not_state_eligible_fails_with_reason():
    r = check(make(applicant_types=[ApplicantType(id="12", description="Nonprofits")]))
    assert not r.passed and any("State governments" in x for x in r.reasons)


def test_closed_fails():
    assert not check(make(days_to_close=-1)).passed


def test_nih_downweighted_not_removed():  # T1
    r = check(make(agency_code="HHS-NIH11"))
    assert r.passed and r.research_penalty == 0.4


def test_blm_region_lock_fails():  # T2
    r = check(make(description="Work must take place on Bureau of Land Management lands."))
    assert not r.passed


def test_fixture_has_some_passes_and_some_failures(opportunities):
    results = [check(Opportunity(**o)) for o in opportunities]
    assert any(r.passed for r in results) and any(not r.passed for r in results)
