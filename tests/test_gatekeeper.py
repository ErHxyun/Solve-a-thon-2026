"""Gatekeeper (owner: lead). Test cases T1, T2 in the test plan. Add a test for every rule you add."""
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


# ---------- rubric gates (DNCR Grant Scoring Rules, Final) ----------
def test_other_region_fails_unless_nc_covered():
    great_lakes = make(title="Great Lakes Restoration Initiative: Community Forest")
    assert not check(great_lakes).passed
    southeast = make(title="Gulf of Mexico and Southeast Atlantic habitat")
    assert check(southeast).passed  # text also covers the Southeast / Atlantic


def test_foreign_mission_fails():
    assert not check(make(agency_name="U.S. Mission to Cambodia")).passed


def test_stale_forecast_fails():
    from datetime import date

    old = make(title="Climate Program Office for FY 2012", status="forecasted", days_to_close=None)
    r = check(old, today=date(2026, 11, 7))
    assert not r.passed and any("stale" in x for x in r.reasons)
    fresh = make(title="Climate Program Office FY 2026", status="forecasted", days_to_close=None)
    assert check(fresh, today=date(2026, 11, 7)).passed


def test_stale_rolling_post_fails():  # found in the first live run: NOAA-OAR-CPO-2012-2003041, posted, no close date
    from datetime import date

    old = make(title="Climate Program Office for FY 2012", number="NOAA-OAR-CPO-2012-2003041", status="posted",
               days_to_close=None, close_date=None)
    assert not check(old, today=date(2026, 11, 7)).passed
    dated = make(title="Program FY 2012 legacy name", status="posted", close_date="2027-01-15", days_to_close=69)
    assert check(dated, today=date(2026, 11, 7)).passed  # a real future deadline is not stale


def test_restore_act_is_gulf_only():
    from agents import gatekeeper as gk
    assert gk.OTHER_REGION.search("RESTORE Act Centers of Excellence Research Grants Program")
