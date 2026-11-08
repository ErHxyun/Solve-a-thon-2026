# DNCR grant ranking: model selection by nested cross-validation

**Data.** 20 federal grants DNCR has won (label 1). Every one of the 1,033 real Grants.gov listings from 11/7/2026 that passes the gates, deduplicated (label 0; unlabeled pool). Only text-derived features are used. The deadline, award count and applicant type were filled in by hand for the wins, so they are excluded to avoid leakage.

**Protocol.**
- Outer loop: stratified 5-fold × 20 repeats, so 100 test folds.
- Inner loop: stratified 3-fold grid search on the training part only.
- Primary metric: **Recall@top 5%**, the share of test wins that rank above the 95th percentile of the test-fold pool (about the top 50 of 1,033).
- Secondary metrics: Average Precision (AP) and AUC.
- Model choice: paired comparison on identical splits, then the **one-standard-error rule**: among models within 1 SE of the best, pick the simplest.

## Candidates and out-of-fold results (mean of 100 test folds)

| Model | Tuned parameters | Recall@5% | AP | AUC |
|---|---|---|---|---|
| B1 keyword count | none | 0.942 | 0.631 | 0.984 |
| B2 meaning S only | none | 0.900 | 0.607 | 0.982 |
| B3 keywords K only | none | 0.832 | 0.631 | 0.973 |
| H0 previous A = (2S+K)/3 | none | 0.982 | 0.679 | 0.988 |
| H1 A, weight w tuned | w | 0.965 | 0.678 | 0.987 |
| H2 A + entity floor | w, floor | 0.982 | 0.705 | 0.989 |
| **H3 A + entity floor + formula boost** | w, floor, boost | **0.982** | **0.730** | 0.989 |
| L1 logistic, elastic net, all features | C, l1_ratio | 1.000 | 0.771 | 0.993 |
| L2 logistic, elastic net, without funder flag | C, l1_ratio | 0.958 | 0.713 | 0.989 |

Paired difference in AP versus H0, with a 95% bootstrap CI over repeats:

| Model | ΔAP | 95% CI |
|---|---|---|
| H2 | +0.026 | [+0.014, +0.038] |
| H3 | +0.051 | [+0.037, +0.065] |
| L2 | +0.034 | [+0.017, +0.051] |

## Decision

- **L1 is excluded for leakage.** Its "DNCR-type funder" flag lists agencies taken from the wins themselves, so it separates the positives by construction.
- **L2 is the fair regularized model.** Inner CV picked the strongest ridge penalty (C = 0.01, l1_ratio = 0). It does not beat H3: Recall 0.958 vs 0.982, AP 0.713 vs 0.730.
- **H3 is chosen.** It has the best AP among the fair models, ties for the best recall, and has only 3 parameters.
- **Final parameters** come from refitting on all data (5-fold × 20) with the 1-SE rule, which acts as regularization: within 1 SE, take equal weights and the smaller boost.
  - Best grid AP = 0.762, SE = 0.006.
  - Chosen point: **w = 0.5**, **entity floor = 70**, **formula boost = 0.10**, with AP = 0.756, inside the 1-SE band.
  - The weight curve is flat between 0.25 and 0.5 (AP 0.760 / 0.762). The boost is weakly identified: 0.05, 0.10 and 0.20 all fall within 1 SE.
- **Skip line 35** is the knee of the trade-off curve:

  | Skip line | Wins kept | Pool listings to review |
  |---|---|---|
  | 30 | 19/20 | 47 |
  | 35 | 19/20 | 35 |
  | 40 | 17/20 | 32 |
  | 50 | 15/20 | 19 |

  It was chosen on all 20 wins, so it is in-sample.
- **Dropped:** urgency U and hours H. They added nothing in the earlier ablation. The state-only chance nudge was also dropped: it relies on fields filled in by hand, so it cannot be validated.

## Final model

```
A     = max( 0.5·S + 0.5·K ) over the 25 plan objectives × research penalty
A     = max( A , 70 )   if the listing names an agency type DNCR is (state arts agency, SHPO, state library, SHRAB, state archives, state museum, state parks, natural heritage program)
Final = min( 100 , A × (1.10 if formula / every-state program else 1) )
Skip if A < 35 · Chase if Final ≥ 60 · Route / Waitlist rules unchanged
```

## Limits

1. **Only 20 positives.** Recall moves by about 5 points per win. The CIs above cover split noise, not sampling of new programs.
2. **The pool is unlabeled, not negative.** Some real listings, such as Collections Stewardship, are probably good DNCR targets. This pushes AP down for every model alike.
3. **16 of the 20 wins use descriptions written from official program pages, not real listings.** The entity and formula rules fire mostly on those texts, so part of H3's gain may reflect how they were written.
4. **In-sample figures are not test error.** Under the final model, 14 Chase / 4 You decide / 2 Skip of the 20 wins is training performance. The test error is the out-of-fold row for H3 above.
5. **Next step:** add other states' wins of the same programs as positives (IMLS / NEH / NPS award databases) and real "applied but lost" cases from DNCR, then rerun this file unchanged.
