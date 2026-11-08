"""Feature table for model selection: 20 DNCR wins (label 1) + real 11/7 pool (label 0, unlabeled).
Only text-derived features; nothing that we filled in by hand for the wins (deadline, award count, applicant type)."""
import re, numpy as np, pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from wins20 import m, nov, kn_df

ENTITY = re.compile(r"state (and jurisdictional )?arts agenc|state historic preservation office|\bshpos?\b|state library administrative|"
                    r"state historical records advisory|state archiv|state museum|state park|natural heritage program", re.I)
DNCR_FUNDER = re.compile(r"National Park Service|Endowment for the Arts|Endowment for the Humanities|Museum and Library|"
                         r"Historical Publications and Records|Forest Service|Fish and Wildlife|NOAA|Highway Administration", re.I)
BROAD_KW = ["museum", "historic", "preservation", "park", "library", "archive", "arts", "humanities", "heritage",
            "conservation", "recreation", "trail", "cultural"]

def matrices(df):
    codes = list(m.OBJECTIVES)
    obj_docs = [m.OBJECTIVES[c][2] + " " + " ".join(m.OBJECTIVES[c][3]) for c in codes]
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words=m.STOPWORDS, sublinear_tf=True)
    vec.fit(obj_docs + df["match_text"].tolist())
    cos = cosine_similarity(vec.transform(df["match_text"]), vec.transform(obj_docs))
    low, syn = df["title"].str.lower(), df["synopsis"].str.lower()
    both = low + " " + syn
    toks = both.str.findall(r"[a-z0-9]+").apply(lambda ws: {w[:6] for w in ws})
    n = len(df); kraw = np.zeros_like(cos)
    for j, c in enumerate(codes):
        for ph in m.OBJECTIVES[c][3]:
            w = np.log((n + 1) / (both.str.contains(re.escape(ph)).sum() + 1)) + 1
            pt = {x[:6] for x in re.findall(r"[a-z0-9]+", ph)}
            it, isn = low.str.contains(re.escape(ph)).values, syn.str.contains(re.escape(ph)).values
            sh = toks.apply(lambda s: len(s & pt) / len(pt)).values
            kraw[:, j] += w * np.where(it, 2.0, np.where(isn, 1.0, np.where(sh >= .5, .5 * sh, 0)))
    best = cos.max(1)
    nih = df.agency.str.contains("National Institutes of Health").values
    ref = df.agency.str.contains("National Park Service|Endowment for the Arts|Endowment for the Humanities|Museum and Library|Forest Service|Fish and Wildlife").values
    L, H = np.median(best[nih]), np.percentile(best[ref], m.S_TOP_PCT)
    rk = kraw.max(1)[ref]; tau = np.median(rk[rk > 0])
    S = np.clip((cos - L) / (H - L), 0, 1) * 100
    neg = np.array([sum(w in t for w in m.NEGATIVE_WORDS) for t in both])
    K = 100 * (1 - np.exp(-kraw / tau)) * (0.5 ** neg[:, None])
    pen = np.array([m.research_penalty(a, t)[0] for a, t in zip(df.agency, df.title)])
    return S, K, pen, neg

def formula_flag(t):
    for h in m.FORMULA_WORDS.finditer(t):
        if re.search(r"in addition to|separate from|unlike|rather than|not (a |part of )?", t[max(0, h.start() - 80):h.start()], re.I):
            continue
        return 1
    return 0

def build():
    kn = kn_df()
    pool = nov.copy()
    g = pool.apply(m.gates, axis=1, result_type="expand"); pool = pool[g[0].values]
    pool["dk"] = pool.title.str.lower().str.replace(r"fiscal year|\bfy\s?\d{2,4}|20\d\d|[^a-z ]", " ", regex=True).str.split().str.join(" ")
    pool = pool.sort_values("deadline", ascending=False).drop_duplicates("dk")
    pool = pool[~pool.title.str.contains("Museums for America|Museum Grants for African American History", case=False)]  # same programs as two wins
    pool["label"] = 0; kn["label"] = 1
    df = pd.concat([pool, kn], ignore_index=True)
    S, K, pen, neg = matrices(df)
    text = df.title + ". " + df.synopsis
    F = pd.DataFrame({
        "S_max": S.max(1), "K_max": K.max(1),
        "breadth": (S > 50).sum(1),                       # how many plan objectives it fits well
        "research_pen": (pen < 1).astype(int),
        "formula": text.map(formula_flag),
        "entity": text.str.contains(ENTITY).astype(int),
        "dncr_funder": df.agency.str.contains(DNCR_FUNDER).astype(int),
        "neg_words": neg,
        "U": [m.urgency(m.OBJECTIVES and list(m.OBJECTIVES)[int(np.argmax((2*S[i]+K[i])/3))], t)[0] for i, t in enumerate(df.title)],
        "log_hours": [np.log(m.simulate_hours(m.pick_anchor(t)[0])["H"]) for t in df.title],
        "kw_count": text.str.lower().apply(lambda s: sum(w in s for w in BROAD_KW)),
    })
    F["label"], F["title"], F["agency"] = df.label.values, df.title.values, df.agency.values
    return F, S, K, pen

if __name__ == "__main__":
    F, S, K, pen = build()
    print(F.label.value_counts()); print(F.groupby("label").mean(numeric_only=True).round(2).T)
    np.savez("cv_mats.npz", S=S, K=K, pen=pen); F.to_pickle("cv_feats.pkl")
