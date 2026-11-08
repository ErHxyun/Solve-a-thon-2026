"""
Build the data behind the Grant Topic Radar page.

Areas: transparent phrase rules on titles (+ agency fallbacks), one area per listing.
Time:  recently closed listings (deadline already passed, mostly 2025-2026) vs coming listings (open now or forecast).
       Momentum = an area's coming share / the overall coming share (1.0 = average; above 1 = growing pipeline).
Fit:   DNCR plan match A from dncr_grant_scoring.py.
Writes radar_data.json.
"""
import json
import re
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
import dncr_grant_scoring as G
from topic_trends import BUCKETS, FUTURE, STOP, bucket, clean

AREAS = [  # (name, title regex, agency regex fallback)
    ("Parks & outdoor recreation", r"\bparks?\b|recreation|\btrails?\b|green ?space|outdoor|public lands", None),
    ("Historic preservation & heritage", r"\bhistoric\b(?!ally)|(?<!housing )preservation(?! of (?:rural )?housing)|heritage|battlefield|archaeolog|cultural (?:resource|property)|treasures", None),
    ("Arts, humanities & museums", r"\barts?\b|humanities|museum|librar|collections|archiv|artifact|artist|scholarly editions", r"Endowment for the (?:Arts|Humanities)|Museum and Library"),
    ("Conservation & wildlife", r"conservation|wildlife|habitat|species|invasive|forest|restoration|pollinator|fish(?:eries)?\b|wetland|watershed|salmon|ecosystem", r"Fish and Wildlife|Forest Service|Natural Resources Conservation"),
    ("Climate, ocean & environment", r"climate|ocean|coastal|marine|atmospher|weather|water quality|pollution|environmental|desalination|earth science", r"Oceanic|Environmental Protection"),
    ("Disaster & emergency", r"disaster|emergency|hurricane|flood|mitigation|preparedness|resilien|wildland fire|wildfire|dam safety", r"FEMA|Homeland Security"),
    ("Agriculture & food", r"agricultur|farm|food|nutrition|crop|livestock|animal|plant|swine|1890|snap\b|rural", r"Agricultur|Food Safety|Rural"),
    ("Transportation & infrastructure", r"transport|highway|transit|rail|port\b|bridge|infrastructure|drone|marine highway|aviation|pedestrian", r"Transportation|Highway|Transit|Aviation|Railroad|Maritime"),
    ("Housing & community", r"housing|homeless|community development|block grant|lead hazard|healthy homes|neighborhood", r"Housing and Urban"),
    ("Education & STEM", r"education|students?|teachers?|k-12|undergraduate|graduate|scholarship|stem\b|school|youth|training", r"Department of Education"),
    ("Workforce & small business", r"workforce|small business|business|economic|manufacturing|trade|jobs|entrepreneur|labor|employment", r"Economic Development|Small Business|Labor|Commerce"),
    ("Justice & public safety", r"justice|law enforcement|police|policing|crime|victim|violence|court|reentry|corrections|trafficking|body-worn", r"Justice|Violence Against Women|Victims of Crime|Juvenile"),
    ("Energy, tech & AI", r"energy|artificial intelligence|\bai\b|cyber|computing|software|quantum|semiconductor|advanced manufacturing|technolog", r"Energy|ARPA"),
    ("Foreign diplomacy & exchange", r"embassy|mission|diplomacy|annual program statement|exchange|alumni|international visitor|u\.s\.-", r"U\.S\. Mission|Embassy|Bureau of (?:Democracy|Educational|International|Population)|State Department"),
    ("Public health & prevention", r"public health|prevention|surveillance|maternal|infant|child health|vaccin|injury|tribal health|family planning|title x|home visiting", r"Disease Control|Health Resources|Indian Health|Children & Families|Community Living|Assistant Secretary for Health"),
    ("Mental health & substance use", r"mental health|substance|opioid|alcohol|suicide|addiction|overdose|behavioral health", r"Substance Abuse|SAMHSA"),
    ("Cancer & military medicine", r"cancer|oncolog|tumor|dow\b|military|burn|traumatic|neurofibromatosis|lupus|autism", r"Defense Health"),
    ("Biomedical & science research", r"research|clinical|trial|biolog|genom|molecular|brain|disease|therap|drug|biomedical|science|scientist|investigator|engineering|chemistry|physics|mathemat", r"Institutes of Health|Science Foundation|NASA|Aeronautics|Army|Navy|Air Force|Food and Drug"),
]
DNCR_AREAS = {"Parks & outdoor recreation", "Historic preservation & heritage", "Arts, humanities & museums",
              "Conservation & wildlife", "Climate, ocean & environment", "Disaster & emergency"}


def assign(title, agency):
    t = title.lower()
    for name, rx, _ in AREAS:            # first match wins: list is ordered most-specific (DNCR areas) first
        if re.search(rx, t):
            return name
    for name, _, arx in AREAS:
        if arx and re.search(arx, agency or "", re.I):
            return name
    return "Other"


TERM_STOP = set(STOP) | {"grants", "program", "research", "development", "fy", "notice", "award", "state", "states",
                         "competition", "nofo", "dow", "funding", "opportunity", "phase", "trials", "trial", "limited",
                         "clinical", "allowed", "required", "optional", "u.s.", "us", "fiscal", "year", "limited competition",
                         "publish", "forecast", "intent", "agency", "broad", "broad agency", "parent", "announcement", "statement",
                         "bureau", "wide", "bureau wide", "bureau land", "cesu", "unit", "data", "health", "science", "management",
                         "affiliated", "affiliated partner", "partner", "joint", "nifc", "jfsp", "joint jfsp", "nifc joint", "great", "base",
                         "opportunities", "sciences", "advance", "stimulating", "recording", "wildland service", "service", "space",
                         "annual", "annual statement", "notice intent", "intent publish", "forecast publish"}


def terms(title):
    w = [x for x in re.findall(r"[a-z][a-z\-]{2,}", clean(title).lower()) if x not in TERM_STOP]
    bi = [f"{a} {b}" for a, b in zip(w, w[1:])]
    return set(w) | set(bi)


def summarize(df):
    coming = df.bucket != "Closed"
    overall = coming.mean()
    def mom(mask):
        k = int(mask.sum())
        return round(float(coming[mask].mean() / overall), 2) if k >= 3 else None
    areas = []
    for name in [a[0] for a in AREAS] + ["Other"]:
        m = df.area == name
        sub = df[m]
        if len(sub) == 0:
            continue
        kw = Counter(t for ti in sub.title for t in terms(ti))
        top = sub.sort_values("A", ascending=False).head(4)
        areas.append(dict(
            name=name, dncr=name in DNCR_AREAS, n=int(len(sub)),
            closed=int((sub.bucket == "Closed").sum()), coming=int((sub.bucket != "Closed").sum()),
            buckets={b: int((sub.bucket == b).sum()) for b in BUCKETS},
            momentum=mom(m), fit=round(float(sub.A.mean()), 1), strong=int((sub.A >= G.A_SKIP).sum()),
            keywords=[k for k, _ in kw.most_common(8)],
            examples=[dict(title=r.title, agency=r.agency, A=round(float(r.A)), bucket=r.bucket,
                           deadline=None if pd.isna(r.deadline) else str(r.deadline.date())) for r in top.itertuples()]))
    def word_list(sub, min_n, top):
        cnt = Counter(); cm = Counter(); area_of = {}
        for r in sub.itertuples():
            for t in terms(r.title):
                cnt[t] += 1
                cm[t] += r.bucket != "Closed"
                area_of.setdefault(t, Counter())[r.area] += 1
        out = []
        for t, c in cnt.most_common(800):
            if c < min_n or (" " not in t and len(t) < 4):
                continue
            out.append(dict(text=t, n=c, coming=cm[t], momentum=round(cm[t] / c / overall, 2),
                            area=area_of[t].most_common(1)[0][0]))
            if len(out) >= top:
                break
        return out
    dn = df.area.isin(DNCR_AREAS)
    words = word_list(df, 6, 90)
    words_dncr = word_list(df[dn], 2, 60)
    words_other = word_list(df[~dn], 6, 90)
    return dict(n=int(len(df)), closed=int((~coming).sum()), coming=int(coming.sum()), overall_coming=round(float(overall), 3),
                buckets={b: int((df.bucket == b).sum()) for b in BUCKETS}, areas=areas, words=words,
                words_dncr=words_dncr, words_other=words_other)


def main():
    df = G.load("data/grants_source.xlsx")
    pm, _ = G.plan_match(df)
    df = pd.concat([df.reset_index(drop=True), pm[["A", "obj"]]], axis=1)
    df["eligible"] = [("not an eligible" not in r) and ("Geographically" not in r) for r in df["elig_reason"]]
    df["bucket"] = df["deadline"].map(bucket)
    df["area"] = [assign(t, a) for t, a in zip(df.title, df.agency)]
    out = dict(as_of=str(G.AS_OF_DATE), source="Grants.gov listings snapshot (team workbook, 1,662 opportunities)",
               all=summarize(df), eligible=summarize(df[df.eligible]))
    json.dump(out, open("radar_data.json", "w"), indent=1)
    render_page(out)
    return df, out


def render_page(out, template="radar/radar_template.html", dest="radar/grant_topic_radar.html"):
    """Fill the page template with the data and write a standalone HTML file you can open in any browser."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    tpl = template if os.path.exists(template) else os.path.join(here, "radar_template.html")
    dst = dest if os.path.isdir(os.path.dirname(dest) or ".") else os.path.join(here, "grant_topic_radar.html")
    for k in ("all", "eligible"):
        for a in out[k]["areas"]:
            for e in a["examples"]:
                e["title"] = e["title"].replace("<", "&lt;")
    body = open(tpl, encoding="utf-8").read().replace("__DATA__", json.dumps(out, separators=(",", ":")))
    page = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
    open(dst, "w", encoding="utf-8").write(page)
    print("wrote", dst)


if __name__ == "__main__":
    df, out = main()
    for k in ("all", "eligible"):
        print("==", k, out[k]["n"], out[k]["buckets"], "coming share", out[k]["overall_coming"])
        for a in out[k]["areas"]:
            print(f"  {a['name'][:32]:32} n={a['n']:4} closed={a['closed']:4} coming={a['coming']:4} mom={a['momentum']} fit={a['fit']:5} strong={a['strong']} | {', '.join(a['keywords'][:5])}")
    print(df[df.area == "Other"].title.head(15).tolist())
    ws = out['all']['words']; print(len(ws)); print(sorted([(w['momentum'], w['text'], w['n']) for w in ws])[:10]); print(sorted([(w['momentum'], w['text'], w['n']) for w in ws])[-12:])
    print(df[df.area=="Historic preservation & heritage"].title.tolist())
