"""THE DATA CONTRACT. Every agent reads and writes these shapes.

Rule: do not change a field name or type here without telling the lead —
other agents depend on it. Adding a new OPTIONAL field is fine.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Agency profile (reference/dncr_profile.json)
# --------------------------------------------------------------------------- #
Urgency = Literal["normal", "elevated", "high", "highest"]


class Objective(BaseModel):
    objective_id: str                      # e.g. "5.1"
    goal_id: str                           # e.g. "5"
    text: str                              # objective wording from the plan
    page: int                              # plan page for citations
    urgency: Urgency = "normal"
    urgency_tier: Optional[int] = None     # 0-3 tier from the plan's own wording (scoring rubric U)
    divisions: list[str] = []              # e.g. ["State Parks", "SHPO"]
    keywords: list[str] = []


class Profile(BaseModel):
    agency: str                            # "dncr"
    name: str
    mission: str = ""
    source_url: str = ""
    objectives: list[Objective]
    keywords: list[str] = []               # plan-wide keywords
    known_funders: list[str] = []          # agency codes / names, e.g. "DOI-NPS", "NEA"


# --------------------------------------------------------------------------- #
# Opportunity (Collector output)
# --------------------------------------------------------------------------- #
class ApplicantType(BaseModel):
    id: str                                # Grants.gov code; "00" = State governments
    description: str


class Opportunity(BaseModel):
    id: str
    source: Literal["grants.gov", "sam.gov"] = "grants.gov"
    number: str
    title: str
    agency_code: str = ""
    agency_name: str = ""
    top_agency_code: str = ""
    status: Literal["posted", "forecasted", "closed", "archived"] = "posted"
    open_date: Optional[str] = None        # ISO yyyy-mm-dd
    close_date: Optional[str] = None       # ISO yyyy-mm-dd; None = rolling / not stated
    days_to_close: Optional[int] = None
    applicant_types: list[ApplicantType] = []
    eligibility_text: str = ""
    funding_instruments: list[str] = []
    categories: list[str] = []
    award_floor: Optional[float] = None
    award_ceiling: Optional[float] = None
    cost_sharing: Optional[bool] = None
    description: str = ""                  # plain text (HTML stripped)
    cfda: list[str] = []
    url: str = ""
    ingested_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    low_quality: bool = False


# --------------------------------------------------------------------------- #
# Gatekeeper output
# --------------------------------------------------------------------------- #
class GateResult(BaseModel):
    opp_id: str
    passed: bool
    eligible: bool
    open_or_coming: bool
    research_penalty: float = 1.0          # 1.0 none, 0.75 research, 0.4 NIH biomedical
    reasons: list[str] = []                # human-readable, shown in UI when failed


# --------------------------------------------------------------------------- #
# Fit Scorer output
# --------------------------------------------------------------------------- #
class ScoreBreakdown(BaseModel):
    semantic: float = 0.0                  # 0-1
    keyword: float = 0.0
    eligibility: float = 0.0
    urgency: float = 0.0
    timing: float = 0.0
    funder: float = 0.0


Confidence = Literal["High", "Medium", "Low"]


class ScoredItem(BaseModel):
    opp_id: str
    score: float                           # 0-100 after research penalty
    confidence: Confidence
    breakdown: ScoreBreakdown
    objective_id: Optional[str] = None     # best-matching objective
    division: Optional[str] = None
    already_yours: bool = False            # recurring program DNCR already receives
    watch: bool = False                    # forecast → watch list
    # Optional, filled by the scoring rubric (agents/fit_scorer.py); other code must not require them
    final: Optional[float] = None          # Final score 0-100 (= score)
    components: dict[str, float] = {}      # e.g. A, S, K, U, W, H, H10, H90, penalty
    second_objective_id: Optional[str] = None
    route_to: Optional[str] = None         # who should apply (DNCR division or another NC office)
    suggested_decision: Optional[Literal["Chase", "Waitlist", "Skip", "Route", "You decide"]] = None
    decision_reason: Optional[str] = None
    rationale: Optional[str] = None        # plain-English explanation of the score
    flags: list[str] = []


# --------------------------------------------------------------------------- #
# Card Agent output (LLM, structured)
# NOTE: models filled by the LLM (Card, Evidence, RouteDraft, StarterPack) must not
# have non-None defaults — Azure OpenAI strict structured output rejects them.
# --------------------------------------------------------------------------- #
class Evidence(BaseModel):
    field: str                             # which card field this supports
    quote: str                             # verbatim text
    source: Literal["listing", "plan"]


MoneyType = Literal["formula", "competitive", "one-time disaster", "pass-through", "not stated"]


class Card(BaseModel):
    opp_id: str
    can_dncr_apply: str                    # Q1 plain English
    who_applies: str                       # Q1 "who owns this" (DNCR division / partner / other agency)
    objective_id: str                      # Q2
    division: str                          # Q2
    money_type: MoneyType                  # Q3
    prerequisites: list[str]               # Q4 match / partner / AOR needs
    worth_it: str                          # Q5 award vs effort
    summary: str                           # 2-3 sentences
    why_fit: str
    why_not_fit: str
    evidence: list[Evidence]


# --------------------------------------------------------------------------- #
# Verifier output
# --------------------------------------------------------------------------- #
class CheckedCard(BaseModel):
    card: Card
    verified_quotes: int = 0
    total_quotes: int = 0
    warnings: list[str] = []
    confidence: Confidence = "Low"
    needs_retry: bool = False
    verifier_feedback: Optional[str] = None
    attempts: int = 1


# --------------------------------------------------------------------------- #
# Alert output
# --------------------------------------------------------------------------- #
class Alert(BaseModel):
    opp_id: str
    kind: Literal["closing_soon", "new_since_yesterday", "waitlist_opening", "waitlist_21_days"]
    message: str
    days_to_close: Optional[int] = None


# --------------------------------------------------------------------------- #
# Human decisions (UI)
# --------------------------------------------------------------------------- #
class Decision(BaseModel):
    opp_id: str
    decision: Literal["Chase", "Waitlist", "Skip", "Route"]
    note: str = ""
    user: str = "dncr-grants"
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


# --------------------------------------------------------------------------- #
# On-click agents
# --------------------------------------------------------------------------- #
class RouteDraft(BaseModel):
    opp_id: str
    route_to: str                          # division / local gov / other agency
    reason: str
    deadline: Optional[str] = None         # filled by code from the listing, never the LLM
    message: str                           # forwarding note draft (never sent automatically)


class StarterPack(BaseModel):
    opp_id: str
    requirements: list[str]                # quoted / paraphrased from the listing
    sf424_fields_to_gather: list[str]
    outline: list[str]
    timeline: list[str]                    # backward plan from deadline
    email_to_division_lead: str
    open_questions: list[str]


# --------------------------------------------------------------------------- #
# Run log
# --------------------------------------------------------------------------- #
class RunLog(BaseModel):
    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    counts: dict[str, int] = {}
    errors: list[str] = []
    status: Literal["running", "succeeded", "failed"] = "running"
