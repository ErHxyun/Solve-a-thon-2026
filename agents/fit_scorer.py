"""Fit Scorer — rank gated opportunities against DNCR objectives.  Owner: Criteria
Spec: docs/agents/fit_scorer.md

Weights (Field Notes §7): semantic 40, keyword 20, eligibility clarity 15,
objective urgency 10, timing 10, known funder 5.

Baseline: semantic similarity uses TF-IDF (offline). P3 swaps in Azure embeddings via
core.llm.embed() when LLM_ENABLED=true, keeping TF-IDF as the fallback.
"""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from core import config
from core.reference import load_annual_cfdas, load_profile
from core.schemas import GateResult, Opportunity, Profile, ScoreBreakdown, ScoredItem

WEIGHTS = {"semantic": 0.40, "keyword": 0.20, "eligibility": 0.15, "urgency": 0.10, "timing": 0.10, "funder": 0.05}
URGENCY_VALUE = {"normal": 0.3, "elevated": 0.6, "high": 0.8, "highest": 1.0}


def _semantic(opps: list[Opportunity], profile: Profile):
    """Return a matrix [opp x objective] of similarities in 0-1. TODO(Criteria): embeddings."""
    obj_texts = [f"{o.text} {' '.join(o.keywords)}" for o in profile.objectives]
    opp_texts = [f"{o.title} {o.description}" for o in opps]
    vec = TfidfVectorizer(stop_words="english").fit(obj_texts + opp_texts)
    return cosine_similarity(vec.transform(opp_texts), vec.transform(obj_texts))


def _keyword(opp: Opportunity, profile: Profile) -> float:
    text = f"{opp.title} {opp.description}".lower()
    hits = sum(1 for k in profile.keywords if k.lower() in text)
    return min(hits / 3, 1.0)


def _eligibility(opp: Opportunity) -> float:
    codes = {a.id for a in opp.applicant_types}
    return 1.0 if "00" in codes else 0.8 if "99" in codes else 0.5


def _timing(opp: Opportunity) -> float:
    if opp.status == "forecasted":
        return 0.5
    if opp.days_to_close is None:
        return 0.8  # rolling / not stated
    return 1.0 if opp.days_to_close >= 30 else 0.6 if opp.days_to_close >= 14 else 0.3


def _funder(opp: Opportunity, profile: Profile) -> float:
    return 1.0 if any(opp.agency_code.startswith(f) or f in opp.agency_name for f in profile.known_funders) else 0.0


def _confidence(score: float, opp: Opportunity) -> str:
    level = "High" if score >= 70 else "Medium" if score >= 50 else "Low"
    if opp.status == "forecasted" and level == "High":
        level = "Medium"  # forecasts cap at Medium (Field Notes gate 2)
    return level


def run(state: dict) -> dict:
    profile = load_profile()
    annual = load_annual_cfdas()
    gates = {g["opp_id"]: GateResult(**g) for g in state["gates"]}
    opps = [Opportunity(**o) for o in state["opportunities"] if gates[o["id"]].passed]
    if not opps:
        return {"scored": [], "top": []}

    sims = _semantic(opps, profile)
    scored: list[ScoredItem] = []
    for i, opp in enumerate(opps):
        best = int(sims[i].argmax())
        obj = profile.objectives[best]
        b = ScoreBreakdown(
            semantic=float(min(sims[i][best] * 2.5, 1.0)),  # TF-IDF cosines are small; rescale (TODO Criteria: calibrate)
            keyword=_keyword(opp, profile),
            eligibility=_eligibility(opp),
            urgency=URGENCY_VALUE[obj.urgency],
            timing=_timing(opp),
            funder=_funder(opp, profile),
        )
        raw = sum(getattr(b, k) * w for k, w in WEIGHTS.items()) * 100
        score = round(raw * gates[opp.id].research_penalty, 1)
        scored.append(ScoredItem(
            opp_id=opp.id,
            score=score,
            confidence=_confidence(score, opp),
            breakdown=b,
            objective_id=obj.objective_id,
            division=obj.divisions[0] if obj.divisions else None,
            already_yours=any(c in annual for c in opp.cfda),
            watch=opp.status == "forecasted",
        ))

    scored.sort(key=lambda s: s.score, reverse=True)
    out = [s.model_dump() for s in scored]
    return {"scored": out, "top": out[: config.TOP_N]}
