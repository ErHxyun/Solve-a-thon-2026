"""T3 human annotation: after raters fill human_annotation_sheet.xlsx (one copy per rater), run
   python t3_ndcg.py rater1.xlsx rater2.xlsx ...
It averages the ratings, computes NDCG@10 of every weight vector in weights_grid.pkl on the 40 items, and applies the
final rule: keep vectors within 1 SE of the best budget-simulation result (T2), pass the past-wins guardrail (T1),
then take the best NDCG@10; ties -> closest to equal weights."""
import sys, numpy as np, pandas as pd
key = pd.read_excel("human_annotation_KEY_do_not_share.xlsx")
col = [c for c in pd.read_excel(sys.argv[1], sheet_name="Rate these").columns if c.startswith("Rating")][0]
r = pd.concat([pd.read_excel(f, sheet_name="Rate these").set_index("Item")[col] for f in sys.argv[1:]], axis=1)
key["rating"] = key.Item.map(r.mean(1))
if len(sys.argv) > 2:
    from itertools import combinations
    a = [np.corrcoef(r.iloc[:, i], r.iloc[:, j])[0, 1] for i, j in combinations(range(r.shape[1]), 2)]
    print(f"inter-rater correlation (mean): {np.mean(a):.2f}")
def ndcg10(score):
    order = np.argsort(-score)[:10]; g = key.rating.values
    dcg = sum((2 ** g[i] - 1) / np.log2(k + 2) for k, i in enumerate(order))
    ideal = sum((2 ** v - 1) / np.log2(k + 2) for k, v in enumerate(sorted(g, reverse=True)[:10]))
    return dcg / ideal
R = pd.read_pickle("weights_grid.pkl")
R["T3_NDCG10"] = [ndcg10(key.A.values * (b + p * key.P + v * key.V + u * key.U / 3 + w * key.W / 100))
                  for b, p, v, u, w in R[["b", "wP", "wV", "wU", "wW"]].values]
best2 = R.loc[R.T2_EV.idxmax()]
ok = R[(R.T2_EV >= best2.T2_EV - best2.T2_se) & (R.T1_AP >= R.T1_AP.max() - R.loc[R.T1_AP.idxmax(), "T1_AP_se"])
       & (R[["wP", "wV", "wU", "wW"]].min(1) >= 0.05)].copy()
ok["dist"] = np.sqrt(((ok[["b", "wP", "wV", "wU", "wW"]].values - [.6, .1, .1, .1, .1]) ** 2).sum(1))
print(ok.sort_values(["T3_NDCG10", "dist"], ascending=[False, True]).head(10).round(3).to_string(index=False))
