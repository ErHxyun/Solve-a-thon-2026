# Card Agent — Spec

| | |
|---|---|
| Owner | LLM-A |
| Branch | `agent/card` |
| Code | `agents/card_agent.py`, prompt `prompts/card_agent.md`, eval `scripts/eval_card_agent.py` |
| Type | LLM — deployment `chat` (gpt-6.1-terra), fallback `chat-fallback` (gpt-4.1) |
| Runs | Daily batch, top N only (≤ 15 calls/day + ≤ 15 retries) |
| Status | Prompt v2.1 + code-owned facts, figure check, cache, extractive offline card; review fixes (cache key, objective disagreement) done; 28 card tests (38 in suite) pass; **not yet run against the live model** (needs `.env` from lead) |

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
1. Build the user prompt (`build_user_prompt`) in three blocks:
   - **LISTING TEXT** (title, eligibility text, description ≤ 6,000 chars) — exactly what the Verifier searches for `source="listing"`
   - **PLAN TEXT** (matched objective + page) — skipped while `dncr_profile.json` still says "TODO", so the model never quotes placeholder wording
   - **FACTS** computed by code (eligibility summary, close date, award, cost-share flag, instruments, CFDA, already-yours, money-type hint). Usable, **never quotable**. **No days-left and no score**: they change daily, the app shows them next to the card.
2. Cache lookup: `data/cache/cards/<opp_id>-<hash>.json`. Hash = prompt version + system prompt + deployment + listing text and listing facts (title, eligibility, description, status, close date, award, cost share, instruments, CFDA, applicant types) + matched objective (id, wording, divisions) + already_yours. **Not** in the key: days_to_close, score, confidence, watch, ingested_at. Unchanged listing → no LLM call the next morning; an offline demo can replay real cards. Bump `PROMPT_VERSION` on any prompt change.
3. Call `core.llm.chat_json(schema=Card, reasoning_effort="low")` — strict JSON schema.
4. `finalize()` (code-owned): `opp_id` from code; the ranked `objective_id` stays on the card, but if the model named another one, a plain note is appended to `why_not_fit` ("Card writer thought objective 5.1 fits better than the ranked objective 1.5 — check which one applies.", or "…not in the DNCR plan" for an invented id); empty division → ranked division; empty `why_not_fit` → "not stated"; quotes trimmed + de-duplicated (never dropped — judging quotes is the Verifier's job).
5. **Figure check** (`unsupported_figures`): any dollar amount or date in the narrative that is not in the listing / award fields → one self-repair call with feedback → anything still unsupported is masked as "[see listing]".
6. On `LLMUnavailable`: **extractive offline card** — every quote is an exact sentence from the listing (passes the Verifier), money type from the rule hint, risks from code (forecast, unclear eligibility, cost share, weak match). Labelled `[offline placeholder]`.
7. Up to 5 calls in parallel. The graph sends rejected cards back once with Verifier feedback (`card_retry`); retries bypass the cache.

### Money-type hint (rules, never forced)
| Rule | Hint |
|---|---|
| Title says disaster / supplemental / hurricane / one-time | one-time disaster |
| Body mentions disaster or hurricane ≥ 3 times | one-time disaster |
| CFDA **and** name match a recurring program (`annual_programs.json`) | that program's type |
| Grant / cooperative agreement listing | competitive |
| otherwise | not stated |

Why so strict: single mentions gave false positives ("disaster resilience" in RARI, "chemical disasters" in NIH CCRP, "supplemental start-up packages" in NIH COBRE), and CFDA 15.916 is shared by the LWCF state program and the competitive ORLP. The model sees the hint and may overrule it; the eval reports agreement.

## 4. Procedure
- ✅ Prompt v1, `Card` schema (strict-compatible), parallel calls, placeholder, retry hook
- ✅ Prompt v2: quotable vs. FACTS blocks; quote length 6–30 words; one quote per key field
- ✅ Code-owned fields + figure check with one self-repair, then masking
- ✅ Cache keyed on listing + objective only (review fix: the first key hashed the whole prompt, which held days-left and score, so it never hit)
- ✅ Objective disagreement kept visible in why_not_fit (review fix)
- ✅ Extractive offline card (100% quote pass on the fixture top 14 with the real Verifier, offline)
- ✅ `scripts/eval_card_agent.py`: quote pass rate, invented figures, empty why_not_fit, money-type agreement, latency, consistency (`--repeat 2`), multi-deployment comparison
- ☐ First live run with `LLM_ENABLED=true` on the fixture top 15; read every card
- ☐ Model comparison on the same 10 items: `python scripts/eval_card_agent.py --deployments chat,chat-sol,chat-luna,chat-astra,chat-fallback`
- ☐ Prompt v3 from live failures (record v2 → v3 diff in GenAI log)
- ☐ Consistency check: `--repeat 2` (GPT-6 can't use temperature 0)
- ☐ T3 live: ESHPF-type disaster grant → money_type "one-time disaster", objective 5.1, SHPO
- ☐ Depends on P1: real objective wording + pages in `dncr_profile.json` (until then no plan quotes)

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
`tests/test_card_agent.py` — 28 tests (38 in the whole suite), all offline.

| Test | Expected |
|---|---|
| prompt contains listing + objective; retry feedback in prompt | ✅ |
| FACTS block holds computed numbers; listing block doesn't | ✅ |
| "TODO" plan text never offered for quoting | ✅ |
| T3 offline: EDA FY25 Disaster Supplemental → hint "one-time disaster" | ✅ |
| ORLP (CFDA 15.916, tagged already_yours) → still "competitive" | ✅ |
| RARI / NIH CCRP / NIH COBRE → not "one-time disaster" | ✅ |
| placeholder offline is a valid `Card`; model can't change `opp_id`; ranked objective wins; quotes cleaned | ✅ |
| supported figure ($75,000) passes; invented "$9 million" / "March 3, 2027" → 1 repair call → masked | ✅ |
| offline quotes are exact substrings (4 fixture items) | ✅ |
| offline card flags cost share | ✅ |
| cache: 2nd call skips LLM; feedback bypasses cache; LLM failure not cached | ✅ |
| cache hits the next day when only days left / score / confidence / watch change (review fix) | ✅ |
| cache misses when description, close date, award or objective change | ✅ |
| prompt carries no score and no days-left | ✅ |
| model picks another valid objective → ranked id kept, note in why_not_fit; invented id → note; same id → no note (review fix) | ✅ |
| `run()` returns one card per top item | ✅ |
| T3 live (manual) | ☐ needs Azure |

## 8. Quality assurance
- **Done when:** on 15 live cards, ≥ 90% of quotes verify, 0 invented dates/amounts (manual spot check of 5), every card has a non-empty `why_not_fit`.
- **Metrics:** quote verification rate, retries per run, Low-confidence share, avg latency, reasoning tokens.
- **Known risks:** GPT-6 rejects `temperature` (handled in gateway); outputs vary run to run → caching + consistency metric.
- **GenAI log:** prompt v1/v2, model comparison table, every hallucinated quote the Verifier caught.
