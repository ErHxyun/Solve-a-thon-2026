# Multi-Agent Workstream — How We Work

**Lead:** [Name] — owns `core/`, `graphs/`, the code agents Collector, Gatekeeper, Verifier + guard, Alert, and all merges into `main`.
**Criteria:** [Name] — Fit Scorer (criteria, weights, calibration, ablation). **LLM-A:** [Name] — Card Agent. **LLM-B:** [Name] — Route Agent + Apply Assist.

> Independence rule: whoever writes scoring code (Criteria, Lead) does **not** label the evaluation set. Labels come from two teammates who don't write scoring code.

## 1. Agents and owners

| Agent | File | Spec | Owner | Branch | Type |
|---|---|---|---|---|---|
| Collector | `agents/collector.py` | [collector.md](collector.md) | Lead | `agent/collector` | Code |
| Gatekeeper | `agents/gatekeeper.py` | [gatekeeper.md](gatekeeper.md) | Lead | `agent/gatekeeper` | Code |
| Fit Scorer | `agents/fit_scorer.py` | [fit_scorer.md](fit_scorer.md) | Criteria | `agent/fit-scorer` | Code + embeddings |
| Card Agent | `agents/card_agent.py` | [card_agent.md](card_agent.md) | LLM-A | `agent/card` | LLM |
| Verifier (+ guard) | `agents/verifier.py`, `core/guard.py` | [verifier.md](verifier.md) | Lead | `agent/verifier` | Code |
| Alert | `agents/alert.py` | [alert.md](alert.md) | Lead | `agent/alert` | Code |
| Route Agent | `agents/route_agent.py` | [route_agent.md](route_agent.md) | LLM-B | `agent/route` | LLM, on click |
| Apply Assist | `agents/apply_assist.py` | [apply_assist.md](apply_assist.md) | LLM-B | `agent/apply-assist` | LLM, on click |

Each agent branch already holds a **working baseline** of that agent that runs offline. Your job is to make it good: fill the TODOs, meet your spec's quality bar, and grow your tests.

## 2. Branch workflow

```
main  ← final merges only (lead). Must always pass `pytest` and run `python pipeline.py`.
 ├── setup/scaffold     shared foundation only: core/, graphs/ (+ stubs), pipeline.py, app/,
 │                      shared reference data, fixtures, smoke + guard tests, docs, Docker files
 ├── agent/<name>       = setup/scaffold + ONLY that agent's files (code, prompt, test, spec)
 └── dev/frontend       later, from main, for the npm / UI work
```

**Each agent branch contains only its own files on top of the foundation:**

| Branch | Its own files |
|---|---|
| `agent/collector` | `agents/collector.py`, `tests/test_collector.py`, `scripts/fetch_fixture.py`, `docs/agents/collector.md` |
| `agent/gatekeeper` | `agents/gatekeeper.py`, `tests/test_gatekeeper.py`, `docs/agents/gatekeeper.md` |
| `agent/fit-scorer` | `agents/fit_scorer.py`, `tests/test_fit_scorer.py`, `docs/agents/fit_scorer.md` |
| `agent/card` | `agents/card_agent.py`, `prompts/card_agent.md`, `tests/test_card_agent.py`, `docs/agents/card_agent.md` |
| `agent/verifier` | `agents/verifier.py`, `tests/test_verifier.py`, `docs/agents/verifier.md` |
| `agent/alert` | `agents/alert.py`, `tests/test_alert.py`, `docs/agents/alert.md` |
| `agent/route` | `agents/route_agent.py`, `prompts/route_agent.md`, `reference/program_owners.json`, `tests/test_route_agent.py`, `docs/agents/route_agent.md` |
| `agent/apply-assist` | `agents/apply_assist.py`, `prompts/apply_assist.md`, `reference/process_map.json`, `tests/test_apply_assist.py`, `docs/agents/apply_assist.md` |

**How it runs without the other agents:** `graphs/batch_graph.py` loads `agents/<name>.py` if it exists on the branch, otherwise a stand-in from `graphs/stubs.py` (prints `[graph] using stubs for: ...`). So on your branch, `python pipeline.py` runs your real agent with stubs around it, and on `main` the stubs disappear one by one as agents are merged. The smoke test uses the normalized fixture `tests/fixtures/opportunities_sample.json`, so no test depends on another person's agent.

**Start working on your branch:**
```bash
git fetch
git checkout agent/<name>
git pull
```

**Rules:**
1. Edit only your files: `agents/<yours>.py`, `prompts/<yours>.md`, `tests/test_<yours>.py`, `docs/agents/<yours>.md`, and reference files your spec says you own.
2. Need a change in `core/` (schemas, llm, storage) or `graphs/`? Ask the lead. LLM tool-calling support (`chat_with_tools`), if we add it, also goes into `core/llm.py` via the lead. Adding an **optional** field to a schema is usually fine. Renaming or removing one breaks others.
3. Never import `openai` directly. All LLM calls go through `core.llm.chat_json()` / `core.llm.embed()`.
4. Never commit `.env`, keys, or connection strings.
5. Commit small and often; push your branch at least every hour so the lead can see progress.

**Ready to merge (open a Pull Request into `main`):**
- [ ] `pytest` passes (all tests, not just yours)
- [ ] `python pipeline.py` exits 0 with `LLM_ENABLED=false`
- [ ] Your spec's **Quality assurance** checklist is ticked
- [ ] Your spec's sections are updated to match what you actually built
- [ ] At least one entry in the GenAI log (prompt used, or AI mistake caught)

The lead reviews, merges into `main`, then re-runs the full pipeline. Merge order: **collector → gatekeeper → fit_scorer → card → verifier → alert → route → apply-assist** (data flows in that order, so each merge can be tested on real upstream output).

## 3. Shared state (what flows between agents)

| Key | Written by | Shape (`core/schemas.py`) |
|---|---|---|
| `opportunities` | Collector | `list[Opportunity]` |
| `gates` | Gatekeeper | `list[GateResult]` |
| `scored`, `top` | Fit Scorer | `list[ScoredItem]` (top = first `TOP_N`) |
| `cards` | Card Agent | `list[Card]` |
| `checked` | Verifier | `list[CheckedCard]` |
| `attempts`, `card_feedback` | Graph retry loop | `dict[opp_id, int / str]` |
| `alerts` | Alert | `list[Alert]` |

On-click agents (Route, Apply Assist) are called by the UI with `(Opportunity, Card)` and return `RouteDraft` / `StarterPack`.

## 4. Test plan roles

| Role | Who | What |
|---|---|---|
| Test lead | P1 | Owns this test plan; tracks issues |
| Testers | LLM-A ↔ LLM-B (cross-test); Lead reviews both LLM agents | Run each other's test cases; record actual vs expected |
| Integration tester | Lead | `tests/test_pipeline_smoke.py` + full pipeline after every merge |
| User acceptance | P5 | Acts as the DNCR user on the UI |

| # | Test case | Expected | Covered by |
|---|---|---|---|
| T1 | NIH biomedical item | Down-weighted ×0.4 | `test_gatekeeper.py` |
| T2 | BLM-land item | Gated with reason | `test_gatekeeper.py` |
| T3 | ESHPF-type disaster grant | High, Obj 5.1, SHPO, one-time disaster | TODO (card) |
| T4 | NEA Partnership | Tagged "already yours" | TODO (fit_scorer) |
| T5 | Fake quote in card | Verifier flags, one retry, then Low | `test_verifier.py` |
| T6 | Close date mismatch | Listing date wins + warning | TODO (verifier) |
| T7 | EIN in text | Redacted before LLM | `test_guard.py` |
| T8 | Daily job run | Succeeds; run log written | `test_pipeline_smoke.py` |
| T9 | Skip then undo | Item returns | TODO (UI) |
| T10 | New user, first card | Decision < 1 min | UAT (P5) |

## 5. Tools for LLM agents (`core/tools.py`, owner: lead)

LLM agents may call read-only tools through `core.llm.chat_with_tools(agent=..., tools=[...], max_steps=3)`. The final answer is still a structured schema checked by the Verifier. Every tool call is written to `logs/llm/*.jsonl`.

| Tool | What it returns | Default allow-list |
|---|---|---|
| `lookup_program_owner` | NC office that owns this kind of program | Card |
| `check_annual_program` | "already yours?" — matches CFDA **and** title (shared CFDA numbers like 15.916 are not enough) | Card |
| `get_objective` / `list_objectives` | DNCR objective text, page, urgency, divisions | Card |
| `get_opportunity_details` | full stored record (live mode: Grants.gov API) | Apply Assist |
| `days_until` | days from today to a date — the model never does date math | Card, Apply Assist |
| `get_prep_time` | typical prep days for a money type | Apply Assist |
| `backward_timeline` | dated backward plan from the close date + `too_late` flag | Apply Assist |

Not allowed, ever: anything that writes, sends, submits, or looks up EIN/UEI/bank/credentials. Route Agent uses no tools (owner lookup is done in code). To give your agent a tool, ask the lead to add it to `AGENT_TOOLS`.

## 6. Spec template

New agent or new major feature? Copy [TEMPLATE.md](TEMPLATE.md).
