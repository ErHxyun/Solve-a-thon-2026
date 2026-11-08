"""Gatekeeper — 3 hard gates before ranking.  Owner: lead
Spec: docs/agents/gatekeeper.md

Gates from "DNCR Grant Scoring Rules (Final)" (pass / fail, never scored):
  G1 applicant: state governments eligible (00), unrestricted (99), or "Others (25)" whose text mentions states (flagged)
  G2 region:    not limited to another region / land type / country
  G3 status:    posted or forecasted, deadline not passed, forecast / rolling listing not stale
"""
from __future__ import annotations

import re
from datetime import date

from core.schemas import GateResult, Opportunity

# G2: other regions (checked on title + eligibility text), unless the text also covers NC
OTHER_REGION = re.compile(r"\b(gulf (coast|of mexico|states)|great lakes|chesapeake|pacific islands?|western states|"
                          r"alaska|hawai.?i|puerto rico|guam|u\.?s\.? virgin islands|delta regional|colorado river|"
                          r"northern border|mekong|appropriate to the (state|region) of|restore act)\b", re.I)
NC_OK = re.compile(r"north carolina|southeast|appalachia|atlantic|all (50 )?states|nationwide|alaska native|"
                   r"american indian|native hawaiian", re.I)
FOREIGN_AGENCY = re.compile(r"U\.S\. Mission to|Embassy|Bureau of Democracy", re.I)
STALE_FORECAST_YEARS = 2   # a forecast whose newest year is this many years old is treated as stale

STATE_GOV = "00"
UNRESTRICTED = "99"
OTHERS_SEE_TEXT = "25"

# TODO(lead): extend from known false positives (e.g., BLM Youth Conservation Corps)
REGION_LOCK_TERMS = ["bureau of land management lands", "blm-managed lands", "gulf coast states", "alaska only", "hawaii only", "u.s. territories only"]
NIH_PREFIXES = ("HHS-NIH",)
RESEARCH_TERMS = ["research project grant", "basic research", "principal investigator", "r01", "r21"]


def check(opp: Opportunity, today: date | None = None) -> GateResult:
    reasons: list[str] = []
    codes = {a.id for a in opp.applicant_types}
    text = (opp.eligibility_text + " " + opp.description).lower()

    # Gate 1: eligible
    eligible = STATE_GOV in codes or UNRESTRICTED in codes
    if not eligible and OTHERS_SEE_TEXT in codes and "state" in opp.eligibility_text.lower():
        eligible = True
        reasons.append("Eligibility is 'Others (see text)'; text mentions states — verify")
    if not eligible:
        reasons.append("State governments are not an eligible applicant type")
    lock = next((t for t in REGION_LOCK_TERMS if t in text), None)
    if lock:
        eligible = False
        reasons.append(f"Limited to a region / land type that excludes NC: '{lock}'")
    scope = f"{opp.title} {opp.eligibility_text}"
    region = OTHER_REGION.search(scope)
    if region and not NC_OK.search(scope):
        eligible = False
        reasons.append(f"G2 Region: program is limited to another region ('{region.group(0)}')")
    if FOREIGN_AGENCY.search(opp.agency_name):
        eligible = False
        reasons.append("G2 Region: program serves another country")

    # Gate 2: open or coming
    open_or_coming = opp.status in ("posted", "forecasted") and (opp.days_to_close is None or opp.days_to_close >= 0)
    if not open_or_coming:
        reasons.append("Closed or past deadline")
    years = [int(y) for y in re.findall(r"20[0-3]\d", f"{opp.title} {opp.number}")]
    this_year = (today or date.today()).year
    no_deadline = opp.status == "forecasted" or not opp.close_date  # forecasts and "rolling" posts never expire
    if no_deadline and years and max(years) <= this_year - STALE_FORECAST_YEARS:
        open_or_coming = False
        reasons.append(f"G3 Status: stale listing with no deadline (newest year mentioned is {max(years)})")

    # Gate 3: not research for researchers (penalty, not removal)
    penalty = 1.0
    if opp.agency_code.startswith(NIH_PREFIXES):
        penalty = 0.4
        reasons.append("NIH biomedical research (down-weighted x0.4)")
    elif any(t in text for t in RESEARCH_TERMS):
        penalty = 0.75
        reasons.append("Research funding (down-weighted x0.75)")

    return GateResult(
        opp_id=opp.id,
        passed=eligible and open_or_coming,
        eligible=eligible,
        open_or_coming=open_or_coming,
        research_penalty=penalty,
        reasons=reasons,
    )


def run(state: dict) -> dict:
    results = [check(Opportunity(**o)) for o in state["opportunities"]]
    return {"gates": [r.model_dump() for r in results]}
