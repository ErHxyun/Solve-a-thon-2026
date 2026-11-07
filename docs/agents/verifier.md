# Verifier (+ Sensitive-Data Guard) — Spec

| | |
|---|---|
| Owner | P2 |
| Branch | `agent/verifier` |
| Code | `agents/verifier.py`, `core/guard.py` |
| Type | Code (no LLM) |
| Runs | Daily batch after Card Agent (and again after one retry); guard runs inside every LLM call |
| Status | Quote check + confidence + retry flag work; date/eligibility cross-checks TODO |

## 1. Description
The trust layer. Checks every card against the source text with deterministic code: each evidence quote must really exist in the listing or the plan objective; facts like dates and eligibility always come from the raw record, never from the model. Cards with fake quotes go back to the Card Agent once with feedback; if they still fail they are marked Low confidence (hidden by default, still viewable). The guard redacts identifiers (EIN, UEI, SSN, bank, passwords, keys) before any text reaches the LLM. Serves trust ("reasons I can check") and **R7**.

## 2. Input
| Source | Field | Notes |
|---|---|---|
| `state["cards"]` | `evidence[]` with `source` listing / plan | |
| `state["opportunities"]` | title, description, eligibility_text, close_date | source of truth |
| `reference/dncr_profile.json` | objective text | source for `plan` quotes |
| `state["top"]` | ranked objective + confidence | |
| `state["attempts"]` | attempt count per card | |

## 3. Process
1. For each quote: fuzzy `partial_ratio` against its source text; ≥ 90 = verified.
2. Warnings: unverified quotes, no evidence, objective mismatch, (TODO) date/eligibility contradictions.
3. Confidence: all verified → keep ranked confidence; ≥ 50% → cap at Medium; else Low.
4. If quotes failed and attempts < 2 → `needs_retry` + feedback listing the bad quotes.

## 4. Procedure
- ✅ Quote check, confidence rules, retry flag/feedback, guard patterns + tests
- ☐ T6: scan card text for dates/amounts; if a date in the card ≠ listing close date → warning; UI always shows the listing date
- ☐ Eligibility contradiction: card says "can apply" but no `00`/`99` code → warning
- ☐ Tune `QUOTE_THRESHOLD` on the first live cards (log score distribution)
- ☐ Guard: add patterns found during testing; keep false positives low on grant numbers
- ☐ Report stats for evaluation: % quotes verified, # retries, # warnings by type

## 5. Validation
- Output validates as `CheckedCard`.
- Pure function: same input → same output (no LLM, no randomness).
- Never deletes a card; only lowers confidence and adds warnings.

## 6. Output
| State key | Shape |
|---|---|
| `state["checked"]` | `list[CheckedCard]`: card, verified_quotes, total_quotes, warnings[], confidence, needs_retry, verifier_feedback, attempts |

## 7. Testing module
`tests/test_verifier.py`, `tests/test_guard.py` (exist)

| Test | Input | Expected |
|---|---|---|
| real quotes | verbatim listing + plan quotes | 2/2 verified, confidence kept |
| T5 fake quote | invented quote, attempt 1 | `needs_retry`, feedback |
| T5 still fake | attempt 2 | no retry, Low |
| no evidence | empty evidence | Low |
| T7 guard | EIN, SSN, password | redacted |
| guard no false positive | grant number, CFDA, dates | unchanged |
| ☐ T6 | card with wrong close date | warning |

## 8. Quality assurance
- **Done when:** T5, T6, T7 pass; on live cards, no hallucinated quote reaches the UI with High confidence.
- **Metrics:** % quotes verified (target ≥ 90%), retries per run, warnings by type.
- **Known limits:** proves a quote **exists**, not that the reasoning is right; humans still judge.
- **GenAI log:** each hallucination caught (this is our "where AI was wrong" evidence).
