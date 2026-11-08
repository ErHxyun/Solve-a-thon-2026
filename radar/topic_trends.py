"""
Grant topic trends for DNCR: BERTopic on grant titles, then momentum and DNCR fit per topic.

- Embeddings: TF-IDF (1-2 grams) -> TruncatedSVD (100 dims). A sentence-transformer model would be better,
  but this environment cannot download one; swap `embed()` for SentenceTransformer("all-MiniLM-L6-v2") locally.
- Time: this is a snapshot of currently listed opportunities, so "trend" = where deadlines fall
  (closed <=2025, 2026, 2027, 2028+, forecast). Momentum = topic's future share / overall future share.
- DNCR fit: average plan-match A from dncr_grant_scoring.py.
Writes topic_data.json for the Topic Radar page and topic_trends.xlsx.
"""
import json
import re
import sys

import numpy as np
import pandas as pd
from bertopic import BERTopic
from hdbscan import HDBSCAN
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.preprocessing import normalize
from umap import UMAP

sys.path.insert(0, ".")
import dncr_grant_scoring as G

GENERIC = {"fy", "fiscal", "year", "program", "programs", "grant", "grants", "award", "awards", "notice", "funding",
           "opportunity", "nofo", "announcement", "cooperative", "agreement", "agreements", "support", "project",
           "projects", "initiative", "new", "applications", "application", "phase", "pilot", "competition", "solicitation",
           "recurring", "notice", "year", "years", "fy26", "fy27", "fy2026", "fy2027", "national", "office", "center",
           "centers", "assistance", "fund", "open", "general", "limited", "competitive", "request", "proposals", "rfa",
           "par", "pa", "notice", "u01", "r01", "r21", "u54", "u18", "ug3", "uh3", "r61", "r33", "k99", "r00", "p50",
           "u24", "r34", "u19", "r25", "u2c", "ot2", "k01", "k08", "k23", "r03", "r13", "r15", "r24", "r35", "r50",
           "clinical", "trial", "required", "allowed", "optional", "independent", "basic", "experimental", "studies"}
STOP = list(G.ENGLISH_STOP_WORDS | GENERIC)


def clean(t):
    t = re.sub(r"\((?:[A-Z]{1,2}\d{1,2}[A-Z]?|Clinical Trial[^)]*)\)", " ", t)       # NIH mechanism codes
    t = re.sub(r"\b(FY|Fiscal Year)\s?\d{2,4}\b|\b20\d\d\b|\d+", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def bucket(d):
    if pd.isna(d):
        return "Forecast"
    y = d.year
    if y <= 2025 or d.date() < G.AS_OF_DATE:
        return "Closed"
    return "2026" if y == 2026 else "2027" if y == 2027 else "2028+"


ZS_MIN = 0.30
AREAS = {
 "Biomedical research": "biomedical research disease therapeutic clinical trials genomic molecular biology neuroscience brain cancer biology",
 "Cancer & military medicine": "cancer research prostate ovarian lung breast cancer dow defense medical research award investigator idea impact",
 "Public health & prevention": "public health prevention surveillance infectious disease vaccination maternal child health tribal health",
 "Mental health & substance use": "mental health substance use opioid alcohol suicide treatment recovery behavioral health",
 "Education & STEM": "education stem students teachers k-12 undergraduate graduate fellowship training scholarship science education",
 "Workforce & small business": "workforce small business economic development jobs manufacturing entrepreneurship trade",
 "Justice & public safety": "justice law enforcement police crime victims violence courts corrections public safety",
 "Foreign diplomacy & exchange": "embassy mission public diplomacy international exchange annual program statement foreign alumni",
 "Disaster & emergency management": "disaster emergency hurricane flood mitigation preparedness response recovery resilience fema",
 "Climate, ocean & environment": "climate ocean coastal marine atmosphere weather environmental water quality pollution",
 "Conservation & wildlife": "conservation wildlife habitat fish species forest restoration ecosystem invasive land protection",
 "Parks & outdoor recreation": "parks outdoor recreation trails green space public lands recreation access",
 "Historic preservation & heritage": "historic preservation heritage battlefield historic sites cultural resources archaeology",
 "Arts, humanities & museums": "arts humanities museums libraries collections archives cultural heritage artists",
 "Agriculture & food": "agriculture farmers food nutrition rural crops livestock plant animal",
 "Transportation & infrastructure": "transportation highway transit rail port bridge infrastructure safety",
 "Housing & community development": "housing homeless community development urban lead hazard healthy homes",
 "Energy, technology & AI": "energy technology artificial intelligence cybersecurity computing innovation advanced manufacturing",
}

BUCKETS = ["Closed", "2026", "2027", "2028+", "Forecast"]
FUTURE = ["2027", "2028+", "Forecast"]


def main():
    df = G.load("data/grants_source.xlsx")
    g = df.apply(G.gates, axis=1, result_type="expand")
    df["eligible"] = [("not an eligible" not in r) and ("Geographically" not in r) for r in df["elig_reason"]]
    pm, _ = G.plan_match(df)
    df = pd.concat([df.reset_index(drop=True), pm[["A", "obj"]]], axis=1)
    df["doc"] = df["title"].map(clean)
    df["bucket"] = df["deadline"].map(bucket)

    # Zero-shot BERTopic: named policy areas (described in plain words); everything else is clustered freely.
    names = list(AREAS)
    from sklearn.pipeline import make_pipeline
    embedder = make_pipeline(TfidfVectorizer(ngram_range=(1, 2), stop_words=STOP, min_df=1, sublinear_tf=True),
                             TruncatedSVD(120, random_state=42))
    embedder.fit(df["doc"].tolist() + list(AREAS.values()))          # area descriptions share the vector space
    vec = CountVectorizer(stop_words=STOP, ngram_range=(1, 2), min_df=2)
    model = BERTopic(
        embedding_model=embedder,
        umap_model=UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", random_state=42),
        hdbscan_model=HDBSCAN(min_cluster_size=15, min_samples=5, metric="euclidean", prediction_data=True),
        vectorizer_model=vec, zeroshot_topic_list=list(AREAS.values()), zeroshot_min_similarity=ZS_MIN,
        top_n_words=10, calculate_probabilities=False, verbose=False)
    docs = df["doc"].tolist()
    emb = embedder.transform(docs)
    topics, _ = model.fit_transform(docs, embeddings=emb)
    labels = model.get_topic_info().set_index("Topic")["Name"].to_dict()
    desc2name = {v: k for k, v in AREAS.items()}
    df["topic"] = topics
    df["area"] = [desc2name.get(model.topic_labels_.get(t, ""), None) if t != -1 else None for t in topics]
    # zero-shot topics carry the description as their label; discovered clusters keep keyword labels
    lab = {}
    for t in set(topics):
        nm = model.custom_labels_[t] if getattr(model, "custom_labels_", None) else None
        lab[t] = nm
    df["label"] = [model.topic_labels_.get(t, str(t)) for t in topics]
    df["topic"] = topics
    return df, model


if __name__ == "__main__":
    df, model = main()
    info = model.get_topic_info()
    for _, r in info.iterrows():
        sub = df[df.topic == r.Topic]
        print(r.Topic, r.Count, str(sub.area.iloc[0])[:30], "|", ", ".join(w for w, _ in model.get_topic(r.Topic)[:7]),
              "| e.g.", "; ".join(sub.title.sample(min(3, len(sub)), random_state=1).str[:50]))
    df.to_pickle("/tmp/claude-0/topics_df.pkl")
    import pickle; pickle.dump({t: model.get_topic(t) for t in info.Topic}, open("/tmp/claude-0/topic_words.pkl", "wb"))
