"""Budget simulation: 520 hours/year (10 h/week). Each policy works down its own ranking; a grant is attempted only if
it can be finished before its deadline. Truth (win chance, award, hours) is drawn with wide uncertainty, NOT taken from the
model's own point estimates, so the test is not circular in hours or P."""
AWARD_CAP = 3e6                     # one state agency rarely gets more than ~$3M from one federal competition
import io, contextlib, numpy as np, pandas as pd, importlib.util, re
spec = importlib.util.spec_from_file_location("m", "dncr_grant_scoring.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
with contextlib.redirect_stdout(io.StringIO()):
    top, df, cal = m.run("data/grants-search-202611071427.csv", "/tmp/_x.xlsx")
raw = pd.read_csv("data/grants-search-202611071427.csv", low_memory=False).drop_duplicates("opportunity_number").set_index("opportunity_number")
d = df[df.gate_pass & df.dncr].copy()                                   # can apply, and DNCR (not another agency) applies
d["dk"] = d.title.str.lower().str.replace(r"fiscal year|\bfy\s?\d{2,4}|20\d\d|[^a-z ]", " ", regex=True).str.split().str.join(" ")
d = d.sort_values("deadline", ascending=False).drop_duplicates("dk").reset_index(drop=True)
for c in ["award_floor", "award_ceiling", "is_cost_sharing", "expected_number_of_awards"]:
    d[c] = d.opp.map(raw[c])
fl, ce = pd.to_numeric(d.award_floor, errors="coerce"), pd.to_numeric(d.award_ceiling, errors="coerce")
fl = fl.where(fl > 0); ce = ce.where(ce > 0)
fl = fl.where(fl >= 0.01 * ce)                                         # floors like $1 are placeholders
d["award_typ"] = np.where(fl.notna() & ce.notna(), np.sqrt(fl * ce), np.where(ce.notna(), ce / 3, np.nan))   # geometric middle
d["award_typ"] = np.minimum(d.award_typ, AWARD_CAP)
d["award_known"] = d.award_typ.notna()
today = pd.Timestamp(m.AS_OF_DATE)
d["weeks_left"] = (d.deadline - today).dt.days / 7
text = d.title + ". " + d.synopsis
BROAD = ["museum", "historic", "preservation", "park", "library", "archive", "arts", "humanities", "heritage", "conservation", "recreation", "trail", "cultural"]
d["kw"] = text.str.lower().apply(lambda s: sum(w in s for w in BROAD))
print(f"candidate pool: {len(d)} DNCR-applicable listings; award known for {d.award_known.mean():.0%}")

# ---------------- policies (each returns a ranking score; NaN = never chosen) ----------------
Pm = d.P.values; A = d.A.values; U = d.U.values; W = d.W.values; H = d.H.values
fit_ok = A >= m.A_SKIP
def bracket(b, wp, wu, ww): return np.where(fit_ok, A * (b + wp * Pm + wu * U / 3 + ww * W / 100), np.nan)
med_award = np.nanmedian(d.award_typ)
Vn = np.clip((np.log(np.where(d.award_known, d.award_typ, med_award)) - np.log(1e4)) / (np.log(AWARD_CAP) - np.log(1e4)), 0, 1)
POL = {
    "1 Her model + P (0.55/0.25/0.10/0.10)": bracket(.55, .25, .10, .10),
    "2 Old model, no P (0.70/0.15/0.15)":    bracket(.70, 0, .15, .15),
    "3 CV simple: A x formula boost":        np.where(fit_ok, A * (1 + .1 * d.formula_flag.values), np.nan) if "formula_flag" in d else np.where(fit_ok, A * d.M.values, np.nan),
    "4 Expected $ per hour (model's own)":   np.where(fit_ok, A / 100 * Pm * np.where(d.award_known, d.award_typ, med_award) / H, np.nan),
    "7 Her model + P + value V (0.45/0.20/0.10/0.10/0.15)": np.where(fit_ok, A * (.45 + .20 * Pm + .10 * U / 3 + .10 * W / 100 + .15 * Vn), np.nan),
    "5 Fit A only":                          np.where(fit_ok, A, np.nan),
    "6 Keyword search":                      np.where(d.kw > 0, d.kw + 1e-3 * A, np.nan),
}

# ---------------- truth draws ----------------
rng = np.random.default_rng(7)
case = d.P_case.astype(str).str[0].values
nA = d.expected_number_of_awards.astype(float).values
def draw_truth(scn):
    n = len(d)
    f = np.clip(A / 100 + rng.normal(0, .25, n), 0, 1)                      # true fit = A with heavy noise
    p_comp = rng.uniform(*scn["comp_rate"], n)                              # unknown competitive success rate
    base = np.where((case == "1"), rng.beta(19, 1, n),
           np.where((case == "2") & np.isfinite(nA) & (nA > 0) & (nA < 9999),
                    np.clip(rng.normal(np.minimum(1, nA / 56), .1), .02, 1), p_comp))
    win = np.where(f < .35, 0.0, np.clip(base * (.5 + f), 0, 1))             # off-topic = no chance; better fit = more competitive
    award = np.where(d.award_known, np.minimum(AWARD_CAP, d.award_typ * rng.lognormal(0, scn.get("award_sd", .5), n)), np.minimum(AWARD_CAP, rng.lognormal(np.log(med_award), .8, n)))
    hours = H * rng.lognormal(0, scn["hour_sd"], n)                          # true hours differ from our estimate
    wl = rng.uniform(8, 52, n)        # timing-neutral: most programs recur yearly, so the next usable deadline is anywhere in the year
    return win, award, hours, wl
def play(score, truth, budget=520, rate=10):
    win, award, hours, wl = truth
    order = [i for i in np.argsort(-np.nan_to_num(score, nan=-1e18)) if not np.isnan(score[i])]
    t = 0.0; ev = 0.0; n = 0; used = 0.0; tried = []
    for i in order:
        finish = t + hours[i] / rate
        if finish > wl[i] or used + hours[i] > budget: continue             # cannot finish before its deadline
        t, used = finish, used + hours[i]; ev += win[i] * award[i]; n += 1; tried.append(i)
        if used >= budget - 5: break
    return ev, n, used
SCN = {"base (competitive 10-40%, hours +-40%)": dict(comp_rate=(.10, .40), hour_sd=.4),
       "pessimistic (5-20%, hours +-60%)": dict(comp_rate=(.05, .20), hour_sd=.6),
       "optimistic (20-50%, hours +-25%)": dict(comp_rate=(.20, .50), hour_sd=.25),
       "listed award is a poor guide (award noise x2)": dict(comp_rate=(.10, .40), hour_sd=.4, award_sd=1.0)}
out = []
for sname, scn in SCN.items():
    R = {k: [] for k in POL}
    for _ in range(2000):
        tr = draw_truth(scn)
        for k, s in POL.items(): R[k].append(play(s, tr))
    ev = pd.DataFrame({k: [r[0] for r in v] for k, v in R.items()})
    mx = ev.values.max(1, keepdims=True); tie = (ev.values >= mx - 1e-9); share = tie / tie.sum(1, keepdims=True)
    for j, k in enumerate(POL):
        out.append(dict(scenario=sname, policy=k, mean_M=ev[k].mean() / 1e6, p10_M=ev[k].quantile(.1) / 1e6, p90_M=ev[k].quantile(.9) / 1e6,
                        win_share=share[:, j].mean(), median_M=ev[k].median() / 1e6, apps=np.mean([r[1] for r in R[k]]), hours=np.mean([r[2] for r in R[k]]),
                        vs_old=(ev[k] > ev["2 Old model, no P (0.70/0.15/0.15)"]).mean()))
O = pd.DataFrame(out); O.to_pickle("budget_results.pkl")
pd.set_option("display.width", 220)
print(O.round(3).to_string(index=False))
