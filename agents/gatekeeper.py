"""Gatekeeper — 3 hard gates before ranking.  Owner: lead
Spec: docs/agents/gatekeeper.md

Baseline rules included so the pipeline runs end to end. P3 refines:
region / federal-land locks (BLM), research detection, ambiguous "Others (25)" handling.
"""
from __future__ import annotations

from core.schemas import GateResult, Opportunity

STATE_GOV = "00"
UNRESTRICTED = "99"
OTHERS_SEE_TEXT = "25"

# TODO(lead): extend from known false positives (e.g., BLM Youth Conservation Corps)
REGION_LOCK_TERMS = ["bureau of land management lands", "blm-managed lands", "gulf coast states", "alaska only", "hawaii only", "u.s. territories only"]
NIH_PREFIXES = ("HHS-NIH",)
RESEARCH_TERMS = ["research project grant", "basic research", "principal investigator", "r01", "r21"]


def check(opp: Opportunity) -> GateResult:
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

    # Gate 2: open or coming
    open_or_coming = opp.status in ("posted", "forecasted") and (opp.days_to_close is None or opp.days_to_close >= 0)
    if not open_or_coming:
        reasons.append("Closed or past deadline")

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
