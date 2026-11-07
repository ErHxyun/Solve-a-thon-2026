"""Apply Assist — builds a starter pack when the user clicks Chase.  Owner: P1
Spec: docs/agents/apply_assist.md

Drafts SCAFFOLDING only (requirements checklist, SF-424 fields to gather, outline,
backward timeline, email to the division lead). It never drafts the narrative or budget,
never asks for or stores EIN/UEI/credentials, and never submits anything.
"""
from __future__ import annotations

import json

from core import config
from core.llm import LLMUnavailable, chat_json
from core.schemas import Card, Opportunity, StarterPack

SYSTEM_PROMPT = (config.PROMPTS_DIR / "apply_assist.md").read_text(encoding="utf-8")


def load_process_map() -> dict:
    return json.loads((config.REFERENCE_DIR / "process_map.json").read_text(encoding="utf-8"))


def run(opp: Opportunity, card: Card) -> StarterPack:
    pm = load_process_map()
    prep_days = pm["prep_time_days_estimate"].get(card.money_type, 30)
    user = (
        f"Opportunity: {opp.title} ({opp.agency_name})\nClose date (from listing): {opp.close_date or 'not stated'}\n"
        f"Days to close: {opp.days_to_close}\nTypical prep time for {card.money_type}: {prep_days} days\n"
        f"Division: {card.division}\nPrerequisites on card: {card.prerequisites}\n"
        f"Process steps: {json.dumps(pm['steps'])}\nSF-424 fields: {pm['sf424_fields_to_gather']}\n"
        f"Listing description: {opp.description[:6000]}\nEligibility: {opp.eligibility_text[:2000]}"
    )
    try:
        pack = chat_json(agent="apply_assist", system=SYSTEM_PROMPT, user=user, schema=StarterPack,
                         reasoning_effort="medium", max_tokens=6000)
    except LLMUnavailable:
        pack = StarterPack(
            opp_id=opp.id,
            requirements=["[offline] Read the full NOFO at " + opp.url],
            sf424_fields_to_gather=pm["sf424_fields_to_gather"],
            outline=["[offline] Project need", "Approach", "Outcomes", "Budget summary"],
            timeline=[f"[offline] Close date: {opp.close_date or 'not stated'}"],
            email_to_division_lead=f"[offline draft] Please review '{opp.title}': {opp.url}",
            open_questions=[],
        )
    pack.opp_id = opp.id
    return pack
