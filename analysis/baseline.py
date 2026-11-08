"""Current DNCR mode vs our ranking, same truth draws as the weight test (T2).
Current mode (assumptions from Discovery: one person, ~10 h/week, no time, finds grants by email lists / programs she knows):
  - she only SEES a share of the relevant listings: familiar funders (NPS, IMLS, NEH, NEA, NHPRC) more often than others;
  - she spends part of her 10 h/week searching and reading, so fewer hours are left for applications;
  - she works deadline-first (whatever is due next), not by value."""
import numpy as np, pandas as pd, re
src = open("weights.py").read().split("res = []")[0]
exec(src)                                                     # same pool, factors and 400 truth draws (TR) as T2
FAM = d.agency.str.contains("National Park Service|Museum and Library|Endowment for the Humanities|Endowment for the Arts|Historical Publications", case=False).values[idx]
def play(order, val, hrs, wl, budget):
    t = used = ev = 0.0; n = 0
    for i in order:
        fin = t + hrs[i] / (budget / 52)
        if fin > wl[i] or used + hrs[i] > budget: continue
        t, used, ev, n = fin, used + hrs[i], ev + val[i], n + 1
    return ev, n
ours = final((.30, .20, .30, .15, .05), idx); ours_order = np.argsort(-ours)
rng2 = np.random.default_rng(5)
def manual(search_h_week, see_fam, see_other):
    out = []
    for val, hrs, wl in TR:
        seen = np.where(FAM, rng2.random(len(idx)) < see_fam, rng2.random(len(idx)) < see_other)
        cand = np.where(seen)[0]; order = cand[np.argsort(wl[cand])]          # deadline-first among what she saw
        out.append(play(order, val, hrs, wl, 520 - 52 * search_h_week))
    return np.array(out)
def ours_run(review_h_week=0.5):
    return np.array([play(ours_order, v, h, w, 520 - 52 * review_h_week) for v, h, w in TR])
o = ours_run()
rows = [dict(mode="Our ranking (0.5 h/week to review the list)", mean=o[:, 0].mean() / 1e6, p10=np.percentile(o[:, 0], 10) / 1e6,
             p90=np.percentile(o[:, 0], 90) / 1e6, apps=o[:, 1].mean())]
for name, args in [("Current mode - base (2 h/wk search, sees 60% familiar / 20% other)", (2, .6, .2)),
                   ("Current mode - better (1 h/wk, 80% / 40%)", (1, .8, .4)),
                   ("Current mode - worse (3 h/wk, 40% / 10%)", (3, .4, .1))]:
    x = manual(*args)
    rows.append(dict(mode=name, mean=x[:, 0].mean() / 1e6, p10=np.percentile(x[:, 0], 10) / 1e6, p90=np.percentile(x[:, 0], 90) / 1e6,
                     apps=x[:, 1].mean(), ours_beats=(o[:, 0] > x[:, 0]).mean()))
R = pd.DataFrame(rows); print(R.round(3).to_string(index=False)); R.to_excel("outputs/current_mode_vs_ours.xlsx", index=False)
