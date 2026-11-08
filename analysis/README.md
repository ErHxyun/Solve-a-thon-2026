# analysis/ — scoring rubric, model selection and simulations

Research code behind the Fit Scorer and the evidence slides. It runs offline on a Grants.gov export and
does not touch the pipeline in `agents/`.

## Branches

The work is split by function. Each branch builds on the one above it, so merge them in this order.

| Branch | Adds | Main files |
| --- | --- | --- |
| `analysis/scoring-rubric` | The DNCR scoring rubric and the past-wins tests | `dncr_grant_scoring.py`, `wins20.py`, `known.py`, `blind40.py` |
| `analysis/model-selection-cv` | Nested cross-validation that chose the Fit (A) parameters | `feats.py`, `ncv.py`, `final_fit.py`, `model_selection.md` |
| `analysis/weight-tuning` | The 2,556-weight search, budget simulation, current-way baseline, human-annotation test | `weights.py`, `budget_sim.py`, `baseline.py`, `t3_ndcg.py`, `weights.md` |
| `analysis/one-year-simulation` | One-year workflow simulation: tool user vs current way | `twoperson2.py`, `ONE_YEAR_SIMULATION.md` |
| `docs/frontend-feedback` | Usability test notes and requested changes for the web app | `docs/frontend_feedback.md` |

## Data

Raw exports are not committed (`analysis/data/` is git-ignored). Put these files in `analysis/data/`:

| File | What | Used by |
| --- | --- | --- |
| `grants-search-202611071427.csv` | Grants.gov search export, 11/7/2026 | everything |
| `grants_snapshot_2026-08-18.csv` | Grants.gov snapshot, 8/18/2026 (qualifier data) | `wins20.py`, `known.py` (past-wins texts) |

Run every script from inside `analysis/` (they import each other by file name). Results are written to `analysis/outputs/`.

```bash
cd analysis
pip install numpy pandas scikit-learn scipy openpyxl joblib
python dncr_grant_scoring.py --input data/grants-search-202611071427.csv --out outputs/DNCR_Top50_Grants.xlsx
```

## The rubric in one formula

```
Gates first: state government eligible, open to NC, still open; Route if another NC agency should apply.

A (Fit, 0-100) = max over DNCR's 25 plan objectives of (0.5 x meaning + 0.5 x keywords) x research penalty,
                 at least 70 if the listing names an agency type DNCR is
P (Chance)     = 1 formula / every-state program; awards / 56 if only states compete; published rate; else 0.25
V (Value)      = log of typical award, $10k -> 0 ... $3M -> 1   (unknown award -> 0.5)
U (Urgency)    = urgency tier 0-3 of the matched plan objective
W (Effort)     = simulated hours, 20 h -> 100 ... 150 h -> 0

Final = A x (0.30 + 0.20 P + 0.30 V + 0.15 U/3 + 0.05 W/100)
Skip if A < 35 · Chase if Final >= 38 (sized to ~520 hours a year) · otherwise You decide
```

Factors are fixed by what the client said matters; the weights come from testing (see `weights.md` on the
`analysis/weight-tuning` branch). A's inner parameters come from nested cross-validation (`model_selection.md`).

## Headline evidence

| Test | Result |
| --- | --- |
| Fit on unseen past DNCR wins (nested CV) | 98% ranked in the top 5% (AP 0.71 for the final Fit) |
| Weight search, past-wins guardrail | AP 0.80 vs best 0.82 (within 1 SE) |
| Ranking test on 11/7 grants (expected $/yr) | $839K vs $340K for the current way, 2.4x |
| One-year workflow simulation | ~39 vs ~296 hours finding grants; 6.5 vs 3.6 applications; 2.1-2.7x expected funding |

All dollar figures are simulation estimates built from the 11/7 grants and our own assumptions; they lean
optimistic and are meant to be checked in the pilot.
