# How the weights were chosen

**Factors (fixed, chosen for what the client cares about).** Five factors, never changed by testing:
- A, plan match
- P, chance of winning
- V, value of the award
- U, urgency
- W, workload

The testing decides only the weights:

> Final = A × (b + wP·P + wV·V + wU·U/3 + wW·W/100), where b + wP + wV + wU + wW = 1

**Grid.** 2,556 weight vectors in steps of 0.05, with b ≥ 0.30 and each factor weight ≤ 0.40.

## Three tests

| Test | Role | What it measures | Status |
|---|---|---|---|
| T1 Past-wins blind test | Guardrail | Average precision and recall@top 5% for 20 DNCR wins against 1,033 real listings, repeated 5-fold. Only text-derived values are used: no hand-filled deadlines, award counts or applicant types | Done. AP ranges 0.735 to 0.818 across the grid, with SE ≈ 0.04, so it barely separates weight vectors. Recall is 1.00 everywhere |
| T2 Budget simulation | Objective | Expected dollars won per year with 520 hours (10 h/week). 400 truth draws are shared by every vector: unknown competitive success rate (5–45%), true fit = A + noise, award × noise, hours × noise, deadline anywhere in 8–52 weeks | Done. It separates weight vectors clearly (0.50 to 0.86 $M/yr) |
| T3 Human annotation | Decider | NDCG@10 against 0–3 ratings of 40 listings, stratified by score and shuffled, with scores hidden | Sheet ready (human_annotation_sheet.xlsx). Run `t3_ndcg.py` once it is filled |

## Selection rule

1. Pass the T1 guardrail: AP within 1 SE of the best.
2. Stay within 1 SE of the best T2 result.
3. Keep every factor weight ≥ 0.05, because the factors are fixed.
4. Choose by T3 when the ratings exist. Until then, choose the candidate closest to equal weights (0.6 / 0.1 / 0.1 / 0.1 / 0.1). This is the regularization step.

## Current choice (before T3)

| b | wP | wV | wU | wW | T1 AP | T2 $M/yr |
|---|---|---|---|---|---|---|
| **0.30** | **0.20** | **0.30** | **0.15** | **0.05** | 0.800 | 0.853 (best 0.861, 1-SE line 0.837) |

**For reference:**

| Ranking | T1 AP | T2 $M/yr |
|---|---|---|
| A only | 0.778 | 0.636 |
| Old 0.70/0.15/0.15 | 0.788 | 0.642 |
| Her model + P (0.55/0.25/0.10/0.10) | 0.810 | 0.642 |

## What the grid says about each factor

Best T2 result within the guardrail, at each weight:

- **V (value):** the biggest lever. Expected $M/yr rises from 0.72 to 0.86 as wV goes from 0 to 0.40.
- **P (chance):** helps up to 0.10–0.25, then drops again at 0.35 and above. Its effect comes mostly from formula / every-state programs, because 22 of 23 fitting competitive listings share the default P = 0.25.
- **U (urgency):** flat until 0.20, then falls.
- **W (workload):** best near 0 to 0.05. Above 0.10 it pushes small, quick, low-value applications up the list.

## Caveats

- T2's "truth" is built on assumptions about success rates, hours and awards. It shows which weights are robust under those ranges; it does not prove real outcomes.
- T2's true fit is A plus noise, so T2 cannot test A itself. A was tested separately in model_selection.md.
- **Chase line = 38.** This sizes the Chase list to about one year of her hours: 7 grants, about 576 hours.
- **Skip line = 35.** Unchanged from the cross-validation result.
