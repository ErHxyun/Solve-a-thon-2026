"""Evaluate the Card Agent: quote pass rate, invented figures, money-type agreement, latency, consistency.
Owner: LLM-A.  Spec: docs/agents/card_agent.md §4 (live run, model comparison, consistency check).

Usage (needs LLM_ENABLED=true and AOAI_* in .env for real numbers):
    python scripts/eval_card_agent.py                                  # chat deployment, top 10
    python scripts/eval_card_agent.py --deployments chat,chat-fallback --n 10
    python scripts/eval_card_agent.py --repeat 2                       # consistency check
With LLM_ENABLED=false it scores the offline extractive cards (a baseline, not a model test).

Ranking comes from whatever Gatekeeper / Fit Scorer exist on this branch (stubs otherwise),
on the committed fixture, so every model sees the same items. Cache is bypassed.
Writes data/eval/card_eval_<timestamp>.json and prints a markdown table for the GenAI log.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rapidfuzz import fuzz  # noqa: E402

from agents import card_agent  # noqa: E402
from core import config, storage  # noqa: E402
from core.reference import load_profile  # noqa: E402
from core.schemas import Card, Opportunity, ScoredItem  # noqa: E402
from graphs.batch_graph import collector, fit_scorer, gatekeeper  # noqa: E402

QUOTE_THRESHOLD = 90  # same rule as the Verifier


def quote_ok(quote: str, source: str, opp: Opportunity, objective_text: str) -> bool:
    haystack = (objective_text if source == "plan" else card_agent._listing_text(opp)).lower()
    return bool(quote.strip()) and fuzz.partial_ratio(quote.lower(), haystack) >= QUOTE_THRESHOLD


def top_items(n: int) -> tuple[dict[str, Opportunity], list[ScoredItem]]:
    state: dict = {}
    for node in (collector, gatekeeper, fit_scorer):
        state.update(node.run(state))
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    return opps, [ScoredItem(**s) for s in state["top"][:n]]


def evaluate(deployment: str, opps, items, profile) -> dict:
    objectives = {o.objective_id: o.text for o in profile.objectives}
    rows, cards = [], {}
    for item in items:
        opp = opps[item.opp_id]
        started = time.time()
        card: Card = card_agent.make_card(opp, item, profile, deployment=deployment, use_cache=False)
        latency = time.time() - started
        quotes = [quote_ok(e.quote, e.source, opp, objectives.get(item.objective_id or "", "")) for e in card.evidence]
        hint, _ = card_agent.money_type_hint(opp, item)
        cards[item.opp_id] = card
        rows.append({
            "opp_id": item.opp_id,
            "title": opp.title[:60],
            "offline": card.can_dncr_apply.startswith(card_agent.OFFLINE),
            "quotes_ok": sum(quotes),
            "quotes": len(quotes),
            "invented_figures": card_agent.unsupported_figures(card, opp),
            "masked": "[see listing]" in card.model_dump_json(),
            "why_not_fit_empty": card.why_not_fit in ("", "not stated"),
            "money_type": card.money_type,
            "hint": hint,
            "latency_s": round(latency, 2),
        })
    total_q = sum(r["quotes"] for r in rows) or 1
    summary = {
        "deployment": deployment,
        "cards": len(rows),
        "offline_cards": sum(r["offline"] for r in rows),
        "quote_pass_rate": round(sum(r["quotes_ok"] for r in rows) / total_q, 3),
        "cards_all_quotes_ok": sum(r["quotes"] > 0 and r["quotes_ok"] == r["quotes"] for r in rows),
        "cards_with_invented_figures": sum(bool(r["invented_figures"]) for r in rows),
        "cards_masked": sum(r["masked"] for r in rows),
        "empty_why_not_fit": sum(r["why_not_fit_empty"] for r in rows),
        "money_type_agrees_with_hint": sum(r["money_type"] == r["hint"] for r in rows),
        "median_latency_s": statistics.median(r["latency_s"] for r in rows) if rows else 0,
    }
    return {"summary": summary, "rows": rows, "cards": {k: v.model_dump() for k, v in cards.items()}}


def consistency(run_a: dict, run_b: dict) -> dict:
    fields = ("money_type", "who_applies", "objective_id", "division")
    same = {f: 0 for f in fields}
    for opp_id, a in run_a["cards"].items():
        b = run_b["cards"].get(opp_id)
        for f in fields:
            same[f] += bool(b) and a[f] == b[f]
    n = len(run_a["cards"]) or 1
    return {f: round(v / n, 2) for f, v in same.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deployments", default=config.AOAI_CHAT_DEPLOYMENT)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--repeat", type=int, default=1, help="2 = run twice and report consistency")
    args = ap.parse_args()

    if not config.LLM_ENABLED:
        print("[eval] LLM_ENABLED=false -> scoring offline extractive cards (baseline only)")
    profile = load_profile()
    opps, items = top_items(args.n)
    report: dict = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "prompt_version": card_agent.PROMPT_VERSION, "items": [i.opp_id for i in items], "runs": []}

    print("| deployment | cards | quote pass | all quotes ok | invented figs | masked | empty why_not_fit | money=hint | median s |")
    print("|---|---|---|---|---|---|---|---|---|")
    for dep in [d.strip() for d in args.deployments.split(",") if d.strip()]:
        runs = [evaluate(dep, opps, items, profile) for _ in range(args.repeat)]
        s = runs[0]["summary"]
        print(f"| {dep} | {s['cards']} ({s['offline_cards']} offline) | {s['quote_pass_rate']:.0%} | {s['cards_all_quotes_ok']} "
              f"| {s['cards_with_invented_figures']} | {s['cards_masked']} | {s['empty_why_not_fit']} "
              f"| {s['money_type_agrees_with_hint']} | {s['median_latency_s']} |")
        entry = {"deployment": dep, "runs": runs}
        if len(runs) > 1:
            entry["consistency"] = consistency(runs[0], runs[1])
            print(f"  consistency (run 1 vs 2): {entry['consistency']}")
        report["runs"].append(entry)

    path = f"eval/card_eval_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
    storage.write_json(path, report)
    print(f"[eval] wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
