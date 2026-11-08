"""Nested cross-validation: outer 5-fold x 20 repeats (stratified), inner 4-fold grid search for every tuned model."""
import warnings, itertools, numpy as np, pandas as pd
from sklearn.model_selection import StratifiedKFold
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.metrics import roc_auc_score, average_precision_score
warnings.filterwarnings("ignore")
F = pd.read_pickle("cv_feats.pkl"); M = np.load("cv_mats.npz"); S, K, pen = M["S"], M["K"], M["pen"]
y = F.label.values; N = len(y)
FEATS = ["S_max", "K_max", "breadth", "research_pen", "formula", "entity", "dncr_funder", "neg_words", "U", "log_hours", "kw_count"]
X = F[FEATS].values.astype(float)

def A_w(w, idx):            # plan match with weight w on meaning S, 1-w on keywords K, max over objectives, x research penalty
    return (w * S[idx] + (1 - w) * K[idx]).max(1) * pen[idx]

# ---- models: fit(train_idx) -> params ; score(params, idx) -> scores ----
def fixed(fn): return dict(grid=[None], score=lambda p, i: fn(i), k=0)
GRID_W = [0, .25, .5, 2/3, .75, 1]
MODELS = {
  "B1 keyword count":        dict(fixed(lambda i: F.kw_count.values[i] + 1e-3 * F.S_max.values[i]), k=0),
  "B2 meaning S only":       fixed(lambda i: F.S_max.values[i]),
  "B3 keywords K only":      fixed(lambda i: F.K_max.values[i]),
  "H0 current A (w=2/3)":    fixed(lambda i: A_w(2/3, i)),
  "H1 A, w tuned":           dict(grid=[(w,) for w in GRID_W], score=lambda p, i: A_w(p[0], i), k=1),
  "H2 A + entity floor":     dict(grid=list(itertools.product(GRID_W, [0, 60, 70, 80]))
                                 , score=lambda p, i: np.maximum(A_w(p[0], i), p[1] * F.entity.values[i]), k=2),
  "H3 A + entity + formula": dict(grid=list(itertools.product(GRID_W, [0, 60, 70, 80], [0, .05, .1, .2])),
                                 score=lambda p, i: np.maximum(A_w(p[0], i), p[1] * F.entity.values[i]) * (1 + p[2] * F.formula.values[i]), k=3),
}
def lr_model(cols, penalty):
    ci = [FEATS.index(c) for c in cols]
    grid = [(C, l1) for C in [0.01, 0.03, 0.1, 0.3, 1] for l1 in ([0, .5, 1] if penalty == "en" else [0])]
    def fit_score(p, tr, te):
        clf = make_pipeline(StandardScaler(), LogisticRegression(solver="saga", tol=1e-3, C=p[0], l1_ratio=p[1],
                                                                 class_weight="balanced", max_iter=5000))
        clf.fit(X[tr][:, ci], y[tr]); return clf.decision_function(X[te][:, ci])
    return dict(grid=grid, fit_score=fit_score, k=len(cols))
MODELS["L1 logistic, elastic net, all features"] = lr_model(FEATS, "en")
MODELS["L2 logistic, elastic net, no funder flag"] = lr_model([c for c in FEATS if c != "dncr_funder"], "en")

def metrics(s, yt):
    neg = s[yt == 0]; pos = s[yt == 1]
    top = np.quantile(neg, 0.95)                       # top 5% of the unlabeled pool ~ top 50 of 1,033
    return dict(AUC=roc_auc_score(yt, s), AP=average_precision_score(yt, s), R5=(pos > top).mean(),
                skip=(pos < np.quantile(neg, 0.80)).mean())     # wins ranked below the top 20% of the pool
def run_model(md, tr, te):
    def sc(p, a, b): return md["fit_score"](p, a, b) if "fit_score" in md else md["score"](p, b)
    if len(md["grid"]) == 1: return md["grid"][0], sc(md["grid"][0], tr, te)
    inner = StratifiedKFold(3, shuffle=True, random_state=0)
    best, bp = -1, None
    for p in md["grid"]:
        v = np.mean([roc_auc_score(y[tr][b], sc(p, tr[a], tr[b])) for a, b in inner.split(tr, y[tr])])
        if v > best + 1e-9: best, bp = v, p
    return bp, sc(bp, tr, te)

import sys
NREP = int(sys.argv[1]) if len(sys.argv) > 1 else 20
from joblib import Parallel, delayed
def one_rep(rep):
    out = []
    outer = StratifiedKFold(5, shuffle=True, random_state=100 + rep)
    for f, (tr, te) in enumerate(outer.split(X, y)):
        for name, md in MODELS.items():
            p, s = run_model(md, tr, te)
            out.append(dict(model=name, rep=rep, fold=f, params=p, **metrics(s, y[te])))
    return out
rows = [r for rr in Parallel(n_jobs=-1)(delayed(one_rep)(r) for r in range(20)) for r in rr]
chosen = {k: [r["params"] for r in rows if r["model"] == k] for k in MODELS}
R = pd.DataFrame(rows); R.to_pickle("ncv_results.pkl")
pd.to_pickle(chosen, "ncv_chosen.pkl")
summ = R.groupby("model")[["AUC", "AP", "R5", "skip"]].agg(["mean", "std"])
se = R.groupby("model").AUC.std() / np.sqrt(R.groupby("model").AUC.count() / 5 * 1)   # SE across repeats-folds (approx.)
print(summ.round(3).to_string())
print("\nSE(AUC):", se.round(4).to_dict())
from collections import Counter
for k in MODELS:
    if len(MODELS[k]["grid"]) > 1: print(k, Counter(chosen[k]).most_common(3))
