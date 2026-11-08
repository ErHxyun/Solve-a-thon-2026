"""One year, two grants officers, same 10 h/week, same opportunities, same 'truth'.
A uses the system (features from the repo): daily pipeline + gates + Fit Scorer short list; weekly email digest and a
  same-day email when a fitting window is <=45 days; decision cards (0.25 h to read instead of a NOFO first read);
  Apply Assist starter pack on Chase (cuts application hours); tracker + closing-soon alerts.
B works as today: notices grants at random from email lists (familiar funders more often), 2 h/week browsing,
  triages each listing she notices (0.2 h), reads the NOFO of those that look relevant (1.5 h), keeps only those
  that truly fit (she is an expert), and works deadline-first.
A year of opportunities = resample the 11/7 DNCR-applicable pool so that ~1 fitting grant posts per week
  (Notifier spec: ~26 state-eligible listings a week, ~1 fits DNCR)."""
import numpy as np, pandas as pd, io, contextlib, sys
src = open("weights.py").read().split("# ---------- T2")[0]
with contextlib.redirect_stdout(io.StringIO()): exec(src)
pool = np.where((y == 0) & d.dncr.values)[0]
post = pd.to_datetime(d.opp.map(raw.post_date), errors="coerce").values
close = pd.to_datetime(d.deadline, errors="coerce").values
WIN = np.clip((close - post) / np.timedelta64(7, "D"), 2, 52); WIN = np.where(np.isnan(WIN), 8, WIN)
FAMall = d.agency.str.contains("National Park Service|Museum and Library|Endowment for the Humanities|Endowment for the Arts|Historical Publications", case=False).values
SC = final((.30, .20, .30, .15, .05)); CASE = d.P_case.astype(str).str[0].values
NA = pd.to_numeric(d.opp.map(raw.expected_number_of_awards), errors="coerce").values
fit_share = (A[pool] >= 35).mean(); N_YEAR = int(round(52 / fit_share))
print(f"pool {len(pool)}, fitting share {fit_share:.2f} -> {N_YEAR} DNCR-applicable listings/yr (~52 fitting)")

def year(seed, P):
    rng = np.random.default_rng(seed)
    ix = rng.choice(pool, N_YEAR, replace=True); n = N_YEAR
    Aa, H0 = A[ix], d.H.values[ix]
    f = np.clip(Aa / 100 + rng.normal(0, .25, n), 0, 1)
    c, nA = CASE[ix], NA[ix]
    base = np.where(c == "1", rng.beta(19, 1, n), np.where((c == "2") & np.isfinite(nA) & (nA > 0) & (nA < 9999),
                    np.clip(rng.normal(np.minimum(1, nA / 56), .1), .02, 1), rng.uniform(.05, .45, n)))
    win = np.where(f < .35, 0, np.clip(base * (.5 + f), 0, 1))
    award = np.where(d.award_known.values[ix], np.minimum(AWARD_CAP, d.award_typ.values[ix] * rng.lognormal(0, .7, n)),
                     np.minimum(AWARD_CAP, rng.lognormal(np.log(med), .9, n)))
    hrs = H0 * rng.lognormal(0, .45, n)
    t0 = rng.uniform(0, 52, n); w = WIN[ix]; dl = t0 + w
    fitting = Aa >= 35; real = f >= .35
    # A: weekly digest (0-1 wk delay) or same day if the window is short
    know_A = np.where(w <= 45 / 7, t0, t0 + rng.uniform(0, 1, n))
    haz = np.where(FAMall[ix], P["haz_fam"], P["haz_oth"])
    kB = t0 + rng.geometric(haz); know_B = np.where(kB < dl, kB, np.inf)
    res = {}
    for who in "AB":
        if who == "A":
            find_h = 52 * 0.5; screen_h = 0.25 * fitting.sum(); keep = fitting; know = know_A
            h_app = hrs * (1 - P["starter_cut"]); prio = -SC[ix]
        else:
            noticed = np.isfinite(know_B)
            find_h = 52 * P["search_h"]; screen_h = 0.2 * noticed.sum() + P.get("deep_h",1.5) * (noticed & real).sum()
            keep = noticed & real; know = know_B; h_app = hrs; prio = dl
        budget = 520 - find_h - screen_h; rate = budget / 52
        t = used = ev = nw = 0.0; apps = 0; done = np.zeros(n, bool)
        while t < 52:
            fin = t + h_app / rate
            ok = keep & ~done & (know <= t) & (fin <= dl - P["approval_wk"]) & (used + h_app <= budget)
            if not ok.any(): t += .5; continue
            i = np.where(ok)[0][np.argmin(prio[ok])]
            done[i] = True; t = fin[i]; used += h_app[i]; ev += win[i] * award[i]; nw += win[i]; apps += 1
        good = fitting & real
        res[who] = dict(find_screen_h=find_h + screen_h, apps=apps, grants_won=nw, money_M=ev / 1e6,
                        good_total=good.sum(), good_never_seen=(good & ~np.isfinite(know)).sum())
    return res
P0 = dict(haz_fam=.12, haz_oth=.04, search_h=2, starter_cut=.10, approval_wk=2)
SCN = {"base": P0, "B reads faster (0.75 h per NOFO)": dict(P0, deep_h=.75), "B reads faster + no starter saving": dict(P0, deep_h=.75, starter_cut=0),
       "no starter-pack saving": dict(P0, starter_cut=0),
       "B better (notices 2x, 1 h/wk)": dict(P0, haz_fam=.24, haz_oth=.08, search_h=1),
       "B worse (notices 0.5x, 3 h/wk)": dict(P0, haz_fam=.06, haz_oth=.02, search_h=3)}
rows = []
for name, p in SCN.items():
    R = [year(s, p) for s in range(300)]
    a = pd.DataFrame([r["A"] for r in R]); b = pd.DataFrame([r["B"] for r in R])
    rows.append(dict(scenario=name,
        A_hours_finding=a.find_screen_h.mean(), B_hours_finding=b.find_screen_h.mean(),
        A_apps=a.apps.mean(), B_apps=b.apps.mean(), A_won=a.grants_won.mean(), B_won=b.grants_won.mean(),
        A_money_M=a.money_M.mean(), B_money_M=b.money_M.mean(),
        good_per_yr=a.good_total.mean(), B_never_saw=b.good_never_seen.mean(),
        A_beats_B=(a.money_M > b.money_M).mean()))
T = pd.DataFrame(rows).round(2); pd.set_option("display.width", 250); print(T.T.to_string())
T.to_excel("outputs/two_person_simulation.xlsx", index=False)
