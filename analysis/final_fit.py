import itertools, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import average_precision_score
exec(open('ncv.py').read().split("def metrics")[0])          # data, A_w, grids
GRID = list(itertools.product(GRID_W, [0, 60, 70, 80], [0, .05, .1, .2]))
def sc(p, i): return np.maximum(A_w(p[0], i), p[1] * F.entity.values[i]) * (1 + p[2] * F.formula.values[i])
res = {p: [] for p in GRID}
for rep in range(20):
    for tr, te in StratifiedKFold(5, shuffle=True, random_state=500 + rep).split(X, y):
        for p in GRID: res[p].append(average_precision_score(y[te], sc(p, te)))
T = pd.DataFrame([dict(w=p[0], floor=p[1], boost=p[2], AP=np.mean(v), SE=np.std(np.array(v).reshape(20, 5).mean(1)) / np.sqrt(20)) for p, v in res.items()])
best = T.loc[T.AP.idxmax()]; line = best.AP - best.SE
print("best", best.to_dict(), "| 1-SE line", round(line, 4))
ok = T[T.AP >= line].copy()
ok["complexity"] = (ok.boost > 0).astype(int) + (ok.floor > 0).astype(int) + (abs(ok.w - 2/3) > .01).astype(int)
ok = ok.sort_values(["complexity", "boost", "floor", "AP"], ascending=[True, True, True, False])
print("within 1 SE (simplest first):"); print(ok.head(12).round(4).to_string(index=False))
print("\nAP by single knob (others at their best):")
for k in ["w", "floor", "boost"]: print(T.groupby(k).AP.max().round(4).to_dict())
T.to_pickle("final_grid.pkl")
