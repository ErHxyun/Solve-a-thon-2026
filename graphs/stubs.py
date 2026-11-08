"""Stand-ins used when an agent's module is not on this branch yet.

`main` starts with only the shared foundation; each agent arrives through its own branch.
Until then the graph uses these stubs so the pipeline, UI and smoke test still run.
Each stub returns the smallest output that is valid for the data contract.
"""
from __future__ import annotations

import json

from core import config, storage
from core.schemas import Card, CheckedCard, GateResult, Opportunity, ScoreBreakdown, ScoredItem

FIXTURE = config.FIXTURES_DIR / "opportunities_sample.json"


class collector:  # noqa: N801 — mimics a module
    @staticmethod
    def run(state: dict) -> dict:
        opps = json.loads(FIXTURE.read_text(encoding="utf-8"))
        storage.write_json("opportunities/latest.json", opps)
        return {"opportunities": opps}


class gatekeeper:  # noqa: N801
    @staticmethod
    def run(state: dict) -> dict:
        gates = [GateResult(opp_id=o["id"], passed=True, eligible=True, open_or_coming=True,
                            reasons=["[stub] gatekeeper not merged yet"]) for o in state["opportunities"]]
        return {"gates": [g.model_dump() for g in gates]}


class fit_scorer:  # noqa: N801
    @staticmethod
    def run(state: dict) -> dict:
        passed = {g["opp_id"] for g in state["gates"] if g["passed"]}
        scored = [ScoredItem(opp_id=o["id"], score=0.0, confidence="Low", breakdown=ScoreBreakdown()).model_dump()
                  for o in state["opportunities"] if o["id"] in passed]
        return {"scored": scored, "top": scored[: config.TOP_N]}


class card_agent:  # noqa: N801
    @staticmethod
    def make_card(opp: Opportunity, item: ScoredItem, profile=None, feedback=None) -> Card:
        return Card(opp_id=opp.id, can_dncr_apply="[stub]", who_applies="[stub]", objective_id=item.objective_id or "",
                    division=item.division or "", money_type="not stated", prerequisites=[], worth_it="[stub]",
                    summary=opp.description[:300], why_fit="[stub] card agent not merged yet", why_not_fit="[stub]",
                    evidence=[])

    @staticmethod
    def run(state: dict) -> dict:
        opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
        cards = [card_agent.make_card(opps[s["opp_id"]], ScoredItem(**s)) for s in state["top"]]
        return {"cards": [c.model_dump() for c in cards]}


class verifier:  # noqa: N801
    @staticmethod
    def run(state: dict) -> dict:
        checked = [CheckedCard(card=Card(**c), warnings=["[stub] verifier not merged yet"]).model_dump()
                   for c in state["cards"]]
        return {"checked": checked}


class alert:  # noqa: N801
    @staticmethod
    def run(state: dict) -> dict:
        storage.write_json("alerts/latest.json", [])
        return {"alerts": []}


class notifier:  # noqa: N801
    @staticmethod
    def run(state: dict) -> dict:
        return {"notification": {"kind": None, "status": "stub: notifier not merged yet"}}
