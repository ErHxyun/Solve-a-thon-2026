"""Card Agent — turns each top-N item into a plain-English decision card.  Owner: lead
Spec: docs/agents/card_agent.md

The card answers the user's 5 questions (can DNCR apply / which objective & division /
money type / time & prerequisites / worth it) plus "why it might NOT fit",
with verbatim evidence quotes the Verifier can check.

When LLM_ENABLED=false (or the LLM fails) a clearly-labelled offline placeholder card is
produced so the pipeline and UI still work.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from core import config
from core.llm import LLMUnavailable, chat_json
from core.reference import load_profile
from core.schemas import Card, Opportunity, Profile, ScoredItem

SYSTEM_PROMPT = (config.PROMPTS_DIR / "card_agent.md").read_text(encoding="utf-8")
MAX_WORKERS = 5  # keep below Azure OpenAI rate limits


def build_user_prompt(opp: Opportunity, item: ScoredItem, profile: Profile, feedback: str | None = None) -> str:
    obj = next((o for o in profile.objectives if o.objective_id == item.objective_id), None)
    parts = [
        "## Opportunity listing (source = 'listing')",
        f"Title: {opp.title}",
        f"Funder: {opp.agency_name} ({opp.agency_code})",
        f"Status: {opp.status}",
        f"Eligible applicant types: {', '.join(a.description for a in opp.applicant_types) or 'not stated'}",
        f"Eligibility text: {opp.eligibility_text or 'not stated'}",
        f"Award floor/ceiling: {opp.award_floor} / {opp.award_ceiling}",
        f"Cost sharing required: {opp.cost_sharing}",
        f"Funding instruments: {', '.join(opp.funding_instruments) or 'not stated'}",
        f"Description: {opp.description[:6000]}",
        "",
        "## Best-matching DNCR objective (source = 'plan')",
        f"Objective {obj.objective_id} (p. {obj.page}): {obj.text}" if obj else "none",
        f"Divisions: {', '.join(obj.divisions) if obj and obj.divisions else 'not stated'}",
        "",
        f"## Ranking context: score {item.score}, confidence {item.confidence}, already_yours={item.already_yours}",
    ]
    if feedback:
        parts += ["", "## Verifier feedback on your previous card — fix these problems", feedback]
    return "\n".join(parts)


def placeholder_card(opp: Opportunity, item: ScoredItem) -> Card:
    return Card(
        opp_id=opp.id,
        can_dncr_apply="[offline placeholder] State governments listed as eligible" if any(a.id == "00" for a in opp.applicant_types) else "[offline placeholder] check eligibility",
        who_applies="not stated",
        objective_id=item.objective_id or "",
        division=item.division or "not stated",
        money_type="not stated",
        prerequisites=["Cost sharing required"] if opp.cost_sharing else [],
        worth_it="not stated",
        summary=opp.description[:300],
        why_fit=f"Ranked {item.score} against objective {item.objective_id}",
        why_not_fit="not assessed (LLM disabled)",
        evidence=[],
    )


def make_card(opp: Opportunity, item: ScoredItem, profile: Profile, feedback: str | None = None) -> Card:
    try:
        card = chat_json(
            agent="card_agent",
            system=SYSTEM_PROMPT,
            user=build_user_prompt(opp, item, profile, feedback),
            schema=Card,
            reasoning_effort="low",
        )
        card.opp_id = opp.id  # never trust the model with ids
        return card
    except LLMUnavailable:
        return placeholder_card(opp, item)


def run(state: dict) -> dict:
    profile = load_profile()
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    items = [ScoredItem(**s) for s in state["top"]]
    feedback = state.get("card_feedback", {})  # opp_id -> verifier feedback (retry round)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        cards = list(pool.map(lambda it: make_card(opps[it.opp_id], it, profile, feedback.get(it.opp_id)), items))
    return {"cards": [c.model_dump() for c in cards]}
