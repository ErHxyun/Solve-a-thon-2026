"""Verifier — deterministic checks on every card.  Owner: lead
Spec: docs/agents/verifier.md

1. Every evidence quote must appear in its source text (fuzzy match >= QUOTE_THRESHOLD)
2. Facts that come from the raw record (dates, eligibility) are never taken from the LLM
3. Assign final confidence; request ONE retry with feedback when quotes fail
"""
from __future__ import annotations

from rapidfuzz import fuzz

from core.reference import load_profile
from core.schemas import Card, CheckedCard, Opportunity, ScoredItem

QUOTE_THRESHOLD = 90   # partial_ratio score; TODO(lead): tune on real cards
MAX_ATTEMPTS = 2


def _source_text(opp: Opportunity, objective_text: str, source: str) -> str:
    if source == "plan":
        return objective_text
    return " ".join([opp.title, opp.description, opp.eligibility_text])


def check(card: Card, opp: Opportunity, item: ScoredItem, objective_text: str, attempts: int) -> CheckedCard:
    warnings: list[str] = []
    verified = 0
    bad_quotes: list[str] = []
    for ev in card.evidence:
        haystack = _source_text(opp, objective_text, ev.source).lower()
        if ev.quote.strip() and fuzz.partial_ratio(ev.quote.lower(), haystack) >= QUOTE_THRESHOLD:
            verified += 1
        else:
            bad_quotes.append(ev.quote[:120])

    total = len(card.evidence)
    if bad_quotes:
        warnings.append(f"{len(bad_quotes)} of {total} quotes not found in source")
    if total == 0:
        warnings.append("Card has no evidence quotes")
    if card.objective_id and item.objective_id and card.objective_id != item.objective_id:
        warnings.append(f"Card objective {card.objective_id} differs from ranked objective {item.objective_id}")

    needs_retry = bool(bad_quotes) and attempts < MAX_ATTEMPTS and total > 0
    if total and verified == total and not warnings:
        confidence = item.confidence
    elif total and verified / total >= 0.5:
        confidence = "Medium" if item.confidence == "High" else item.confidence
    else:
        confidence = "Low"

    return CheckedCard(
        card=card,
        verified_quotes=verified,
        total_quotes=total,
        warnings=warnings,
        confidence=confidence,
        needs_retry=needs_retry,
        verifier_feedback=("These quotes were not found verbatim in the source; copy exact text or remove them: " + " | ".join(bad_quotes)) if needs_retry else None,
        attempts=attempts,
    )


def run(state: dict) -> dict:
    profile = load_profile()
    objectives = {o.objective_id: o.text for o in profile.objectives}
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    items = {s["opp_id"]: ScoredItem(**s) for s in state["top"]}
    attempts = state.get("attempts", {})

    checked = []
    for c in state["cards"]:
        card = Card(**c)
        item = items[card.opp_id]
        n = attempts.get(card.opp_id, 1)
        checked.append(check(card, opps[card.opp_id], item, objectives.get(item.objective_id or "", ""), n).model_dump())
    return {"checked": checked}
