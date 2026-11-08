# Fit Scorer — Spec ("DNCR Grant Scoring Rules, Final")

| | |
|---|---|
| Owner | Criteria |
| Branch | `agent/fit-scorer` |
| Code | `agents/fit_scorer.py`, report `scripts/score_report.py` |
| Type | Code (TF-IDF + rules + simulation); embeddings optional later |
| Runs | Daily batch, after Gatekeeper |
| Status | **Final** rubric ported (A × (0.30 + 0.20P + 0.30V + 0.15U/3 + 0.05W/100)); parity with the team's `dncr_grant_scoring.py` checked on the 2026-08-18 export (`scripts/rubric_parity.py`); weights chosen by the rubric's testing, human labels pending |

## 1. Description
Ranks every opportunity that passed the gates against DNCR's 25 strategic-plan objectives and suggests a decision. Plan match is a **multiplier**: chance, value, urgency and workload scale a good fit but cannot rescue a grant that isn't DNCR's. Serves **R2** (short list), **R3** (objective, division, who applies), **R4** (meaning-based matching).

## 2. Input
| Source | Field |
|---|---|
| `state["opportunities"]`, `state["gates"]` | title, description (first 1,500 chars), agency name/code, close date, status, CFDA, applicant types, award floor/ceiling, **expected awards** (Grants.gov `numberOfAwards`) |
| `reference/dncr_profile.json` | 25 objectives: text, plan page, urgency tier 0–3, division, phrases |
| `reference/annual_programs.json` | recurring programs (already-yours check needs CFDA **and** title) |

## 3. Process
| Factor | Formula |
|---|---|
| **A** plan match | `max(0.5 S + 0.5 K) × research penalty` (NIH ×0.4; NSF/NASA/BAA ×0.75); **floor 70** when the listing names an agency type DNCR is (state arts agency, SHPO, state library, SHRAB, state archives/museum/parks, natural heritage program) |
| S meaning | TF-IDF cosine to each objective, scaled between L (median NIH) and H (90th pct of NPS/NEA/NEH/IMLS/USFS/FWS) |
| K keywords | IDF-weighted phrase hits (title ×2, partial = share × 0.5), `100 × (1 − e^(−K_raw/τ))`; each negative word halves K |
| **P** chance | 1 = formula / every-state wording (not after "in addition to", "unlike" …); only state governments may apply → expected awards ÷ 56 (0.50 if no count); published success rate (e.g. Museums for America 0.32); else 0.25 (estimate) |
| **V** value | typical award = √(floor × ceiling), or ceiling ÷ 3; log scale, $10k = 0 … $3M = 1; not stated → 0.5 |
| **U** urgency | tier 0–3 of the matched objective; one-time words lift to 2; A250 (3.2) drops to 1 after 2026 |
| **W** workload | median hours H of a 10,000-run, 11-stage triangular simulation for the title's profile (standard / simple / complex / pass-through / disaster); `W = 100 × (150 − H) / 130` |
| **Final** | `A × (0.30 + 0.20 P + 0.30 V + 0.15 U/3 + 0.05 W/100)`; weights chosen by testing (budget simulation objective, past-wins blind test guardrail, 1-SE rule) |
| Decision | Skip if A < 35 → Route if another NC office applies → Waitlist if forecast / no deadline → Chase if Final ≥ 38 → otherwise You decide |

Rounds of the same program are merged (latest deadline kept). `top` = items not suggested Skip, best first, max `TOP_N` — cards are written only for these.

**Calibration:** a batch with ≥ 20 NIH, ≥ 20 DNCR-type funder listings calibrates itself; smaller batches use the full-feed values measured on 2026-11-07 (L = 0.012, H = 0.097, τ = 23.65). The original script's small-batch fallback (L = 0.20, H = 0.52) was on the wrong scale and set every S to 0 — fixed here.

## 4. Procedure
- ✅ Rubric ported; route rules; decision rules; rationale text; Excel report
- ✅ Final rubric (P, V, agency-type floor, new lines and route rules) ported; `scripts/rubric_parity.py`: A/P/V identical and Final within rounding (< 0.1) on 1,012 grants, same objective 100%
- ☐ Label 25–50 items (two people who don't write scoring code) → precision@15 vs keyword baseline
- ☐ Tune `A_SKIP`, `FINAL_CHASE`, `WEIGHTS` on the labels; record before/after
- ☐ Workload: inputs are only the title profile, so most grants get the same H (79 h). Either describe it honestly as "estimated hours by application type" or add per-grant inputs (cost share, award size, partners)
- ☐ Unify the workload table with `reference/process_map.json` (Apply Assist prep days)
- ☐ Move `ROUTE_RULES` into shared reference data with the Route Agent's `program_owners.json`
- ☐ Try embeddings for S (`core.llm.embed`) and compare on the labels

## 5. Validation
Output validates as `ScoredItem`; score 0–100 and sorted; every item has a real objective, components A/S/K/U/W/H, a rationale and a suggested decision. Simulation is seeded (reproducible).

## 6. Output
`state["scored"]` (all passed items) and `state["top"]` (short list), as `ScoredItem` with `final`, `components`, `second_objective_id`, `route_to`, `suggested_decision`, `decision_reason`, `rationale`, `flags`. Calibration written to `scored/calibration.json`.

## 7. Testing module
`tests/test_fit_scorer.py` — ranking on the fixture (Collections Stewardship first), calibration fallback, formula, decision rules, route rules, research penalty, urgency lift / A250 expiry, workload order, round merging, already-yours, Excel report.

## 8. Quality assurance
- **Done when:** precision@15 beats keyword search on the labeled set, and weights are justified by the labels.
- **Pitch numbers:** funnel (all listings → pass gates → A ≥ 35 → Final ≥ 38), precision@15, an honest failure.
- **Parity:** rerun `python scripts/rubric_parity.py --orig dncr_grant_scoring.py --csv <export> --as-of <date>` whenever the rubric script or this file changes.
- **Known limits:** title-only workload profiles; TF-IDF misses synonyms; route rules are agency-name based (no CFDA rules yet).
