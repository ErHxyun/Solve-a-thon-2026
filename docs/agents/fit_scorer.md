# Fit Scorer — Spec

| | |
|---|---|
| Owner | lead |
| Branch | `agent/fit-scorer` |
| Code | `agents/fit_scorer.py` |
| Type | Code + embeddings (`core.llm.embed`, deployment `embed` = text-embedding-3-large) |
| Runs | Daily batch, after Gatekeeper |
| Status | Baseline uses offline TF-IDF; needs embeddings + calibration |

## 1. Description
Ranks every opportunity that passed the gates against DNCR's strategic-plan objectives and picks the top N (default 15) for the short list. Finds the single best-matching objective and division for each. Replaces "decide which of these is worth reading." Serves **R2** (short list), **R3** (objective + division), **R4** (meaning-based matching catches what keywords miss).

## 2. Input
| Source | Field | Notes |
|---|---|---|
| `state["opportunities"]`, `state["gates"]` | passed items + `research_penalty` | |
| `reference/dncr_profile.json` | objectives (text, page, urgency, divisions, keywords), plan keywords, known funders | P1 fills real plan text |
| `reference/annual_programs.json` | CFDA → division | "already yours" tagging |

## 3. Process
1. Semantic similarity of each opportunity (title + description) to each objective (text + keywords); keep the best objective.
2. Six signals, each 0–1: semantic 40%, keyword 20%, eligibility clarity 15%, objective urgency 10%, timing 10%, known funder 5%.
3. Score = weighted sum × 100 × research penalty.
4. Confidence: High ≥ 70, Medium 50–69, Low < 50; forecasts capped at Medium.
5. Tag `already_yours` and `watch`; sort; top N.

## 4. Procedure
- ✅ Baseline TF-IDF semantic + all six signals + confidence + already_yours
- ☐ Embeddings: `core.llm.embed()` for objectives (once, cached) and opportunities (cache by `opp.id` + `lastUpdatedDate` in storage `cache/embeddings/`); fall back to TF-IDF on `LLMUnavailable`
- ☐ Calibrate the semantic signal (cosines from embeddings are not 0–1 "fit"; min-max or a threshold mapping on the labeled set)
- ☐ Fix **already_yours false positive**: ORLP and RARI share CFDA 15.916 with LWCF State Assistance but are separate competitive programs. Match on CFDA **and** title/program name
- ☐ Known failures from Field Notes: BJS literal match to Obj 4.3 (false positive); NEA Partnership ranked too low (should be tagged already_yours)
- ☐ Ablation script for evaluation: drop one signal at a time → precision@15
- ☐ Expose `WEIGHTS` so the UI weight sliders can re-rank

## 5. Validation
- Output validates as `ScoredItem`; score within 0–100; list sorted descending.
- Every top-N item has an `objective_id` that exists in the profile.
- If embeddings fail, log and fall back to TF-IDF (never crash the run).

## 6. Output
| State key / file | Shape |
|---|---|
| `state["scored"]` | all passed items as `ScoredItem`, sorted |
| `state["top"]` | first `TOP_N` of `scored` |

Baseline result on the 40-record fixture (2026-11-07, TF-IDF): #1 NEH Collections Stewardship (81.6, Obj 1.5), #2 ORLP (64.8, Obj 3.5), #3 RARI (58.7, Obj 3.6). Matches the Field Notes worked examples; ORLP/RARI are wrongly tagged already_yours.

## 7. Testing module
`tests/test_fit_scorer.py` (create it)

| Test | Input | Expected |
|---|---|---|
| sorted + bounded | fixture | descending, 0–100 (covered in smoke test) |
| known good | Collections Stewardship | in top 3, objective 1.5 |
| T4 NEA Partnership | record with CFDA 45.025 | `already_yours = True` |
| ORLP not already_yours | record 362715 | `already_yours = False` (after fix) |
| forecast cap | forecast scoring ≥ 70 | confidence Medium |
| embeddings fallback | `LLM_ENABLED=false` | still returns scores |

## 8. Quality assurance
- **Done when:** precision@15 on the labeled set beats the keyword baseline, and the Field Notes worked examples rank as expected.
- **Metrics:** precision@15, recall@30, ablation table, "relevant items missed by keyword search."
- **GenAI log:** where embeddings matched literally similar but wrong objectives (BJS case).
