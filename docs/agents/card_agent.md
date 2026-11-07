# Card Agent — Spec

| | |
|---|---|
| Owner | Lead |
| Branch | `agent/card` |
| Code | `agents/card_agent.py`, prompt `prompts/card_agent.md` |
| Type | LLM — deployment `chat` (gpt-6.1-terra), fallback `chat-fallback` (gpt-4.1) |
| Runs | Daily batch, top N only (≤ 15 calls/day + ≤ 15 retries) |
| Status | Prompt + structured output wired; offline placeholder works; untested against live model |

## 1. Description
Turns each short-listed opportunity into a plain-English decision card that answers the user's five questions without opening the listing: (1) can DNCR apply and who should, (2) which objective and division, (3) what kind of money, (4) how much time and what must happen first, (5) is it worth it, plus **why it might NOT fit**. Every claim is backed by verbatim quotes the Verifier checks. Replaces "read the NOFO synopsis and summarize it for the division." Serves **R3** and **R8**.

## 2. Input
| Source | Field | Notes |
|---|---|---|
| `state["top"]` | `ScoredItem` (objective_id, score, confidence, already_yours) | |
| `state["opportunities"]` | listing text, eligibility, award, cost sharing, instruments | description truncated to 6,000 chars |
| `reference/dncr_profile.json` | matched objective text + page + divisions | |
| `state["card_feedback"]` | Verifier feedback (retry round only) | |

## 3. Process
1. Build the user prompt from the listing + matched objective (`build_user_prompt`).
2. Call `core.llm.chat_json(schema=Card, reasoning_effort="low")` — strict JSON schema.
3. Overwrite `opp_id` from code (never trust the model with ids).
4. On `LLMUnavailable`, emit a clearly labelled offline placeholder card.
5. Up to 5 calls in parallel.
6. The graph sends rejected cards back once with Verifier feedback (`card_retry` node).

## 4. Procedure
- ✅ Prompt v1, `Card` schema (strict-compatible), parallel calls, placeholder, retry hook
- ☐ First live run with `LLM_ENABLED=true` on 15 fixture items; read every card
- ☐ Measure: quote pass rate, latency, reasoning tokens, finish_reason (from `logs/llm/*.jsonl`)
- ☐ Model comparison on the same 10 items: `chat` vs `chat-sol` vs `chat-luna` vs `chat-astra` vs `chat-fallback`
- ☐ Prompt v2 from failures (record v1 → v2 diff in GenAI log)
- ☐ Cache cards by `opp_id` + listing `lastUpdatedDate`; regenerate only when the listing changes
- ☐ Consistency check: run twice, compare money_type / who_applies / objective (GPT-6 can't use temperature 0)

## 5. Validation
- Output must parse as `Card` (strict schema); parse failure → fallback deployment → placeholder.
- `opp_id` overwritten by code; `objective_id` must equal the ranked objective (Verifier warns otherwise).
- Prompt forbids invented dates, amounts, eligibility codes; "not stated" when missing.
- All text passes `core.guard.scrub()` inside the gateway before leaving our process.

## 6. Output
| State key | Shape |
|---|---|
| `state["cards"]` | `list[Card]`: can_dncr_apply, who_applies, objective_id, division, money_type, prerequisites[], worth_it, summary, why_fit, why_not_fit, evidence[{field, quote, source}] |

## 7. Testing module
`tests/test_card_agent.py` (create it)

| Test | Input | Expected |
|---|---|---|
| prompt contains objective | fixture item | objective text + page in user prompt |
| placeholder offline | `LLM_ENABLED=false` | valid `Card`, labelled `[offline placeholder]` |
| opp_id overwrite | mocked `chat_json` returning wrong id | id corrected |
| T3 (live, manual) | ESHPF-type disaster grant | money_type "one-time disaster", objective 5.1, SHPO |
| retry uses feedback | feedback string | appears in prompt |

## 8. Quality assurance
- **Done when:** on 15 live cards, ≥ 90% of quotes verify, 0 invented dates/amounts (manual spot check of 5), every card has a non-empty `why_not_fit`.
- **Metrics:** quote verification rate, retries per run, Low-confidence share, avg latency, reasoning tokens.
- **Known risks:** GPT-6 rejects `temperature` (handled in gateway); outputs vary run to run → caching + consistency metric.
- **GenAI log:** prompt v1/v2, model comparison table, every hallucinated quote the Verifier caught.
