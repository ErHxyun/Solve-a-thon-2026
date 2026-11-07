"""Route Agent — drafts a forwarding note when the user clicks Route.  Owner: P3
Spec: docs/agents/route_agent.md

Runs on click (called from the UI), not in the daily batch.
The owner comes from reference/program_owners.json (code), the wording from the LLM.
The deadline is filled by code from the listing — never by the LLM. Nothing is ever sent.
"""
from __future__ import annotations

import json

from core import config
from core.llm import LLMUnavailable, chat_json
from core.schemas import Card, Opportunity, RouteDraft

SYSTEM_PROMPT = (config.PROMPTS_DIR / "route_agent.md").read_text(encoding="utf-8")


def lookup_owner(opp: Opportunity) -> tuple[str, str] | None:
    rules = json.loads((config.REFERENCE_DIR / "program_owners.json").read_text(encoding="utf-8"))["rules"]
    text = f"{opp.title} {opp.description}".lower()
    for rule in rules:
        m = rule["match"]
        if "top_agency_code" in m and opp.top_agency_code == m["top_agency_code"]:
            return rule["owner"], rule["reason"]
        if "keyword" in m and m["keyword"].lower() in text:
            return rule["owner"], rule["reason"]
    return None


def run(opp: Opportunity, card: Card) -> RouteDraft:
    owner = lookup_owner(opp)
    route_to, reason = owner if owner else (card.who_applies, "Suggested by the decision card")
    try:
        draft = chat_json(
            agent="route_agent",
            system=SYSTEM_PROMPT,
            user=f"Route to: {route_to}\nReason: {reason}\nOpportunity: {opp.title} ({opp.agency_name})\n"
                 f"Summary: {card.summary}\nWhy it fits: {card.why_fit}\nListing URL: {opp.url}",
            schema=RouteDraft,
            reasoning_effort="low",
        )
    except LLMUnavailable:
        draft = RouteDraft(opp_id=opp.id, route_to=route_to, reason=reason, deadline=None,
                           message=f"[offline draft] Forwarding '{opp.title}' to {route_to}. {reason}. Details: {opp.url}")
    draft.opp_id, draft.route_to, draft.reason = opp.id, route_to, reason
    draft.deadline = opp.close_date  # from the listing, never the LLM
    return draft
