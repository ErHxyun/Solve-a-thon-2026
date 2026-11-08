"""Choose the weights of the fixed factor set by testing.
Final = A x (b + wP*P + wV*V + wU*U/3 + wW*W/100), b + wP + wV + wU + wW = 1.
T1 blind test on past wins (guardrail: Recall@top5% and AP), T2 budget simulation (objective: expected $ per year),
T3 human annotation (NDCG@10; sheet produced here, plugged in when filled)."""
import io, contextlib, itertools, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
from sklearn.metrics import average_precision_score
from wins20 import m, nov, kn_df, score
AWARD_CAP = 3e6
raw = pd.read_csv("data/grants-search-202611071427.csv", low_memory=False).drop_duplicates("opportunity_number").set_index("opportunity_number")

# ---------- common table: pool (real) + 20 wins, factor values from text only ----------
kn = kn_df(); kn["state_only"] = False; kn["n_awards"] = np.nan          # no hand-filled fields in the test
with contextlib.redirect_stdout(io.StringIO()):
    d = score(pd.concat([nov, kn.drop(columns=["label", "evidence", "src"])], ignore_index=True))
d["U"] = [m.urgency(o, t)[0] for o, t in zip(d.obj, d.title)]
d["W"] = [m.workload_score(h) for h in d.H]
d["label"] = d.opp.str.startswith("WIN").astype(int)
d = d[d.gate_pass].copy()
d["dk"] = d.title.str.lower().str.replace(r"fiscal year|\bfy\s?\d{2,4}|20\d\d|[^a-z ]", " ", regex=True).str.split().str.join(" ")
d = pd.concat([d[d.label == 1], d[d.label == 0].sort_values("deadline", ascending=False).drop_duplicates("dk")])
d = d[~((d.label == 0) & d.title.str.contains("Museums for America|Museum Grants for African American History", case=False))]
fl = pd.to_numeric(d.opp.map(raw.award_floor), errors="coerce"); ce = pd.to_numeric(d.opp.map(raw.award_ceiling), errors="coerce")
ce = ce.where(ce > 0); fl = fl.where(fl >= .01 * ce)
aw = np.minimum(AWARD_CAP, np.where(fl.notna() & ce.notna(), np.sqrt(fl * ce), np.where(ce.notna(), ce / 3, np.nan)))
med = np.nanmedian(aw); d["award_known"] = ~np.isnan(aw); d["award_typ"] = np.where(np.isnan(aw), med, aw)   # wins: unknown -> median
d["V"] = np.clip((np.log(d.award_typ) - np.log(1e4)) / (np.log(AWARD_CAP) - np.log(1e4)), 0, 1)
d = d.reset_index(drop=True)
A, P, V, U, W, y = (d[c].values.astype(float) for c in ["A", "P", "V", "U", "W", "label"])
fit_ok = A >= m.A_SKIP

# ---------- weight grid (step 0.05, base b >= 0.30, each factor <= 0.40) ----------
G = [w for w in itertools.product(np.arange(0, .41, .05), repeat=4) if sum(w) <= .70 + 1e-9]
G = [(round(1 - sum(w), 2),) + tuple(round(x, 2) for x in w) for w in G]          # (b, wP, wV, wU, wW)
def final(w, idx=slice(None)):
    b, wp, wv, wu, ww = w
    return A[idx] * (b + wp * P[idx] + wv * V[idx] + wu * U[idx] / 3 + ww * W[idx] / 100)

# ---------- T1: blind test on past wins (repeated 5-fold, each fold scored separately) ----------
from sklearn.model_selection import StratifiedKFold
folds = [te for r in range(10) for _, te in StratifiedKFold(5, shuffle=True, random_state=r).split(A, y)]
def t1(w):
    s = final(w); ap, r5 = [], []
    for te in folds:
        st, yt = s[te], y[te]; top = np.quantile(st[yt == 0], .95)
        ap.append(average_precision_score(yt, st)); r5.append((st[yt == 1] > top).mean())
    return np.mean(ap), np.std(ap) / np.sqrt(len(ap) / 5), np.mean(r5)

# ---------- T2: budget simulation on the real DNCR-applicable pool ----------
pool = (y == 0) & d.dncr.values & fit_ok
idx = np.where(pool)[0]
rng = np.random.default_rng(11)
case = d.P_case.astype(str).str[0].values[idx]; nA = pd.to_numeric(d.opp.map(raw.expected_number_of_awards), errors="coerce").values[idx]
TR = []
for _ in range(400):                                                   # common random numbers for every weight vector
    n = len(idx); f = np.clip(A[idx] / 100 + rng.normal(0, .25, n), 0, 1)
    base = np.where(case == "1", rng.beta(19, 1, n), np.where((case == "2") & np.isfinite(nA) & (nA > 0) & (nA < 9999),
                    np.clip(rng.normal(np.minimum(1, nA / 56), .1), .02, 1), rng.uniform(.05, .45, n)))
    win = np.where(f < .35, 0, np.clip(base * (.5 + f), 0, 1))
    award = np.where(d.award_known.values[idx], np.minimum(AWARD_CAP, d.award_typ.values[idx] * rng.lognormal(0, .7, n)),
                     np.minimum(AWARD_CAP, rng.lognormal(np.log(med), .9, n)))
    TR.append((win * award, d.H.values[idx] * rng.lognormal(0, .45, n), rng.uniform(8, 52, n)))
def t2(w):
    s = final(w, idx); order = np.argsort(-s); out = []
    for val, hrs, wl in TR:
        t = used = ev = 0.0
        for i in order:
            fin = t + hrs[i] / 10
            if fin > wl[i] or used + hrs[i] > 520: continue
            t, used, ev = fin, used + hrs[i], ev + val[i]
        out.append(ev)
    out = np.array(out); return out.mean() / 1e6, out.std() / np.sqrt(len(out)) / 1e6, out

res = []
for k, w in enumerate(G):
    ap, ap_se, r5 = t1(w); ev, ev_se, _ = t2(w)
    res.append(dict(b=w[0], wP=w[1], wV=w[2], wU=w[3], wW=w[4], T1_AP=ap, T1_AP_se=ap_se, T1_R5=r5, T2_EV=ev, T2_se=ev_se))
R = pd.DataFrame(res); R.to_pickle("weights_grid.pkl")
print(len(G), "weight vectors tested")
