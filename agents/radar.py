"""Grant Topic Radar — where federal grant money is growing or fading, by policy area.  Owner: front end (Mia) + lead
Spec: docs/agents/radar.md   Origin: radar/build_radar.py (team workbook snapshot), now fed by the daily pipeline.

Each day:
  coming  = every posted / forecasted Grants.gov listing (all applicants; the Collector's state-eligible set is a subset)
  closed  = listings whose deadline passed in the last RADAR_CLOSED_DAYS days (default 365), newest first
  area    = transparent phrase rules on the title (+ agency fallback); one area per listing, first match wins
  fit     = Fit Scorer plan match A, computed on titles only so closed and coming listings are scored the same way
  growth  = an area's share of coming listings / the overall coming share (1.0x = average; needs >= 3 listings)
Output: radar/latest.json  {as_of, source, window_days, all: {...}, eligible: {...}}  (same shape as build_radar.py),
served at /radar and /api/radar. Network or parsing problems never break the pipeline: the previous radar stays.
"""
from __future__ import annotations

import html
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from agents import fit_scorer as fs
from agents.collector import STATE_ELIGIBLE, _post, SEARCH_URL
from agents.gatekeeper import NC_OK, OTHER_REGION
from core import config, storage
from core.reference import load_profile
from core.schemas import Opportunity

CLOSED_DAYS = int(os.getenv("RADAR_CLOSED_DAYS", "365"))
PAGE = 250
MAX_PAGES = 40
BUCKETS = ["Closed", "2026", "2027", "2028+", "Forecast"]

AREAS = [  # (name, title regex, agency regex fallback) — ordered most specific (DNCR areas) first
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
_AREA_RX = [(n, re.compile(t), re.compile(a, re.I) if a else None) for n, t, a in AREAS]

GENERIC = {"fy", "fiscal", "year", "program", "programs", "grant", "grants", "award", "awards", "notice", "funding",
           "opportunity", "nofo", "announcement", "cooperative", "agreement", "agreements", "support", "project",
           "projects", "initiative", "new", "applications", "application", "phase", "pilot", "competition", "solicitation",
           "recurring", "years", "national", "office", "center", "centers", "assistance", "fund", "open", "general",
           "limited", "competitive", "request", "proposals", "rfa", "par", "pa", "clinical", "trial", "required",
           "allowed", "optional", "independent", "basic", "experimental", "studies"}
TERM_STOP = set(ENGLISH_STOP_WORDS) | GENERIC | {
    "research", "development", "state", "states", "dow", "trials", "u.s.", "us", "publish", "forecast", "intent", "agency",
    "broad", "parent", "statement", "bureau", "wide", "cesu", "unit", "data", "health", "science", "management", "affiliated",
    "partner", "joint", "nifc", "jfsp", "great", "base", "opportunities", "sciences", "advance", "stimulating", "recording",
    "service", "space", "annual"}
TERM_STOP_BIGRAMS = {"limited competition", "broad agency", "bureau wide", "bureau land", "affiliated partner", "joint jfsp",
                     "nifc joint", "wildland service", "annual statement", "notice intent", "intent publish", "forecast publish"}


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
def assign(title: str, agency: str) -> str:
    t = title.lower()
    for name, rx, _ in _AREA_RX:          # first match wins
        if rx.search(t):
            return name
    for name, _, arx in _AREA_RX:
        if arx and arx.search(agency or ""):
            return name
    return "Other"


def clean(t: str) -> str:
    t = re.sub(r"\((?:[A-Z]{1,2}\d{1,2}[A-Z]?|Clinical Trial[^)]*)\)", " ", t)       # NIH mechanism codes
    t = re.sub(r"\b(FY|Fiscal Year)\s?\d{2,4}\b|\b20\d\d\b|\d+", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def terms(title: str) -> set[str]:
    w = [x for x in re.findall(r"[a-z][a-z\-]{2,}", clean(title).lower()) if x not in TERM_STOP]
    bi = {f"{a} {b}" for a, b in zip(w, w[1:])} - TERM_STOP_BIGRAMS
    return set(w) | bi


def bucket(close: Optional[str], today: date) -> str:
    if not close:
        return "Forecast"
    d = date.fromisoformat(close)
    if d < today:
        return "Closed"
    return "2026" if d.year <= 2026 else "2027" if d.year == 2027 else "2028+"


def open_to_states(title: str, state_types: bool) -> bool:
    """Same idea as the Gatekeeper's G1 + G2, on what a search hit carries: applicant type and title."""
    return state_types and not (OTHER_REGION.search(title) and not NC_OK.search(title))


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def _iso(mmddyyyy: Optional[str]) -> Optional[str]:
    if not mmddyyyy:
        return None
    try:
        return datetime.strptime(mmddyyyy, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _hits(statuses: str, eligibilities: str = "", stop_before: Optional[date] = None) -> list[dict]:
    """Page through search2. With stop_before, results are newest-closing first and paging stops at the cutoff."""
    out, start = [], 0
    for _ in range(MAX_PAGES):
        payload: dict[str, Any] = {"oppStatuses": statuses, "rows": PAGE, "startRecordNum": start}
        if eligibilities:
            payload["eligibilities"] = eligibilities
        if stop_before:
            payload["sortBy"] = "closeDate|desc"
        data = _post(SEARCH_URL, payload)
        page = data.get("oppHits") or []
        out += page
        start += len(page)
        if not page or start >= int(data.get("hitCount") or 0):
            break
        last = _iso(page[-1].get("closeDate"))
        if stop_before and last and date.fromisoformat(last) < stop_before:
            break
    return out


def collect_rows(today: date) -> list[dict]:
    """One row per listing: id, title, agency, close date, bucket, open-to-states flag."""
    cutoff = today - timedelta(days=CLOSED_DAYS)
    closed_all = _hits("closed", stop_before=cutoff)
    closed_states = {str(h["id"]) for h in _hits("closed", STATE_ELIGIBLE, stop_before=cutoff)}
    coming_all = _hits("forecasted|posted")
    coming_states = {str(h["id"]) for h in _hits("forecasted|posted", STATE_ELIGIBLE)}
    rows, seen = [], set()
    for hits, states in ((coming_all, coming_states), (closed_all, closed_states)):
        for h in hits:
            hid = str(h["id"])
            close = _iso(h.get("closeDate"))
            if hid in seen or (h.get("oppStatus") == "closed" and (not close or date.fromisoformat(close) < cutoff)):
                continue
            seen.add(hid)
            title = html.unescape(h.get("title") or "")
            rows.append({"id": hid, "title": title, "agency": h.get("agency") or h.get("agencyCode") or "", "close": close,
                         "bucket": bucket(close, today), "eligible": open_to_states(title, hid in states)})
    return rows


def rows_from_state(state: dict, today: date) -> list[dict]:
    """Offline / fixture mode: the Collector's listings only (no closed history, so growth is not computed)."""
    return [{"id": o["id"], "title": o["title"], "agency": o.get("agency_name", ""), "close": o.get("close_date"),
             "bucket": bucket(o.get("close_date"), today), "eligible": open_to_states(o["title"], True)}
            for o in state.get("opportunities", [])]


def add_fit(rows: list[dict]) -> None:
    """Fit Scorer plan match A on titles only (closed listings have no synopsis here, so coming ones don't use it either)."""
    if not rows:
        return
    opps = [Opportunity(id=r["id"], number=r["id"], title=r["title"], agency_name=r["agency"]) for r in rows]
    _, _, a_obj, _, _, _ = fs.plan_match(opps, load_profile().objectives)
    best = a_obj.max(axis=1)
    for r, o, a in zip(rows, opps, best):
        pen, _ = fs.research_penalty(o)
        r["A"] = float(max(a * pen, fs.ENTITY_FLOOR if fs.ENTITY.search(o.title) else 0.0))
        r["area"] = assign(r["title"], r["agency"])


# --------------------------------------------------------------------------- #
# Summary (same JSON shape as radar/build_radar.py, so the page template plugs in unchanged)
# --------------------------------------------------------------------------- #
def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    coming_n = sum(r["bucket"] != "Closed" for r in rows)
    closed_n = n - coming_n
    overall = coming_n / n if n else 0.0
    has_history = closed_n > 0

    def mom(sub: list[dict]) -> Optional[float]:
        if len(sub) < 3 or not has_history or not overall:
            return None
        return round(sum(r["bucket"] != "Closed" for r in sub) / len(sub) / overall, 2)

    areas = []
    for name in [a[0] for a in AREAS] + ["Other"]:
        sub = [r for r in rows if r["area"] == name]
        if not sub:
            continue
        kw = Counter(t for r in sub for t in terms(r["title"]))
        top = sorted(sub, key=lambda r: -r["A"])[:4]
        areas.append(dict(
            name=name, dncr=name in DNCR_AREAS, n=len(sub),
            closed=sum(r["bucket"] == "Closed" for r in sub), coming=sum(r["bucket"] != "Closed" for r in sub),
            buckets={b: sum(r["bucket"] == b for r in sub) for b in BUCKETS},
            momentum=mom(sub), fit=round(float(np.mean([r["A"] for r in sub])), 1), strong=sum(r["A"] >= fs.A_SKIP for r in sub),
            keywords=[k for k, _ in kw.most_common(8)],
            examples=[dict(title=r["title"], agency=r["agency"], A=round(r["A"]), bucket=r["bucket"], deadline=r["close"]) for r in top]))

    def word_list(sub: list[dict], min_n: int, top: int) -> list[dict]:
        cnt, cm, area_of = Counter(), Counter(), {}
        for r in sub:
            for t in terms(r["title"]):
                cnt[t] += 1
                cm[t] += r["bucket"] != "Closed"
                area_of.setdefault(t, Counter())[r["area"]] += 1
        out = []
        for t, c in cnt.most_common(800):
            if c < min_n or (" " not in t and len(t) < 4):
                continue
            out.append(dict(text=t, n=c, coming=cm[t], momentum=round(cm[t] / c / overall, 2) if has_history and overall else None,
                            area=area_of[t].most_common(1)[0][0]))
            if len(out) >= top:
                break
        return out

    dn = [r for r in rows if r["area"] in DNCR_AREAS]
    other = [r for r in rows if r["area"] not in DNCR_AREAS]
    return dict(n=n, closed=closed_n, coming=coming_n, overall_coming=round(overall, 3),
                buckets={b: sum(r["bucket"] == b for r in rows) for b in BUCKETS}, areas=areas,
                words=word_list(rows, 6, 90), words_dncr=word_list(dn, 2, 60), words_other=word_list(other, 6, 90))


def build(rows: list[dict], today: date, source: str) -> dict:
    add_fit(rows)
    return dict(as_of=today.isoformat(), source=source, window_days=CLOSED_DAYS,
                generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                all=summarize(rows), eligible=summarize([r for r in rows if r["eligible"]]))


# --------------------------------------------------------------------------- #
# Agent entry point
# --------------------------------------------------------------------------- #
def run(state: dict) -> dict:
    today = date.today()
    try:
        if config.COLLECTOR_MODE == "live":
            rows = collect_rows(today)
            source = f"Grants.gov, daily pipeline: open and forecast listings plus listings closed in the last {CLOSED_DAYS} days"
        else:
            rows = rows_from_state(state, today)
            source = "Offline fixture (40 listings; no closed-listing pull, so growth is only indicative)"
        out = build(rows, today, source)
        storage.write_json("radar/latest.json", out)
        print(f"[radar] {out['all']['n']} listings ({out['all']['closed']} closed, {out['all']['coming']} coming); "
              f"{out['eligible']['n']} open to states")
        return {"radar": {"n": out["all"]["n"], "status": "ok"}}
    except Exception as exc:  # noqa: BLE001 — the radar is a side view; never fail the morning run over it
        print(f"[radar] skipped: {exc}")
        return {"radar": {"status": "error", "error": str(exc)[:200]}}
