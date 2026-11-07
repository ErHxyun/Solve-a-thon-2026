"""Daily batch workflow as an explicit LangGraph state graph.  Owner: lead

START → collector → gatekeeper → fit_scorer → card_agent → verifier ─┬─→ alert → publish → END
                                                   ▲                  │ quotes failed (once)
                                                   └── card_retry ◄───┘

Each node is a plain function `run(state) -> dict` from agents/. Nodes return only the
keys they add or update; LangGraph merges them into the shared state.
Print the diagram for README/slides with:  python -m graphs.batch_graph
"""
from __future__ import annotations

import importlib
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from core import config, storage
from core.reference import load_profile
from core.schemas import CheckedCard, Opportunity, ScoredItem
from graphs import stubs

MISSING_AGENTS: list[str] = []


def _load(name: str):
    """Use agents/<name>.py if it is on this branch; otherwise the stub in graphs/stubs.py."""
    try:
        return importlib.import_module(f"agents.{name}")
    except ModuleNotFoundError as exc:
        if exc.name != f"agents.{name}":
            raise  # the agent exists but one of ITS imports is broken — surface it
        MISSING_AGENTS.append(name)
        return getattr(stubs, name)


collector = _load("collector")
gatekeeper = _load("gatekeeper")
fit_scorer = _load("fit_scorer")
card_agent = _load("card_agent")
verifier = _load("verifier")
alert = _load("alert")
if MISSING_AGENTS:
    print(f"[graph] using stubs for: {', '.join(MISSING_AGENTS)}")


class PipelineState(TypedDict, total=False):
    opportunities: list[dict]
    gates: list[dict]
    scored: list[dict]
    top: list[dict]
    cards: list[dict]
    checked: list[dict]
    attempts: dict[str, int]          # opp_id -> card attempts
    card_feedback: dict[str, str]     # opp_id -> verifier feedback
    alerts: list[dict]
    published: int


def card_retry(state: PipelineState) -> dict[str, Any]:
    """Regenerate only the cards the Verifier rejected, with its feedback (max 1 retry)."""
    profile = load_profile()
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    items = {s["opp_id"]: ScoredItem(**s) for s in state["top"]}
    attempts = dict(state.get("attempts", {}))
    cards = {c["opp_id"]: c for c in state["cards"]}
    for ch in map(CheckedCard.model_validate, state["checked"]):
        if ch.needs_retry:
            opp_id = ch.card.opp_id
            new = card_agent.make_card(opps[opp_id], items[opp_id], profile, ch.verifier_feedback)
            cards[opp_id] = new.model_dump()
            attempts[opp_id] = ch.attempts + 1
    return {"cards": list(cards.values()), "attempts": attempts}


def after_verify(state: PipelineState) -> str:
    return "retry" if any(c["needs_retry"] for c in state["checked"]) else "done"


def publish(state: PipelineState) -> dict[str, Any]:
    """Join scores + verified cards into the file the UI reads."""
    checked = {c["card"]["opp_id"]: c for c in state.get("checked", [])}
    opps = {o["id"]: o for o in state["opportunities"]}
    rows = []
    for s in state["scored"]:
        rows.append({**s, "opportunity": opps[s["opp_id"]], "checked": checked.get(s["opp_id"])})
    storage.write_json(f"scored/{config.AGENCY}/latest.json", rows)
    storage.write_json("gates/latest.json", state["gates"])
    return {"published": len(rows)}


def build_graph():
    g = StateGraph(PipelineState)
    g.add_node("collector", collector.run)
    g.add_node("gatekeeper", gatekeeper.run)
    g.add_node("fit_scorer", fit_scorer.run)
    g.add_node("card_agent", card_agent.run)
    g.add_node("verifier", verifier.run)
    g.add_node("card_retry", card_retry)
    g.add_node("alert", alert.run)
    g.add_node("publish", publish)

    g.add_edge(START, "collector")
    g.add_edge("collector", "gatekeeper")
    g.add_edge("gatekeeper", "fit_scorer")
    g.add_edge("fit_scorer", "card_agent")
    g.add_edge("card_agent", "verifier")
    g.add_conditional_edges("verifier", after_verify, {"retry": "card_retry", "done": "alert"})
    g.add_edge("card_retry", "verifier")
    g.add_edge("alert", "publish")
    g.add_edge("publish", END)
    return g.compile()


graph = build_graph()

if __name__ == "__main__":
    print(graph.get_graph().draw_mermaid())
