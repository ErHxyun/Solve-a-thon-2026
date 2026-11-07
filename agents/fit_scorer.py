"""Fit Scorer — implements "DNCR Grant Scoring Rules (Final)".  Owner: Criteria
Spec: docs/agents/fit_scorer.md   Origin: the team's dncr_grant_scoring.py (ported to the pipeline)

Per grant that passed the Gatekeeper:
  A  plan match = (2S + K) / 3 x research penalty            (is it ours?)        0-100
       S = TF-IDF similarity of title + first 1,500 chars to each of the 25 objectives,
           scaled between L (median NIH listing) and H (90th pct of DNCR-type funders)
       K = continuous keyword score: rarer plan phrases weigh more (IDF), title hits double,
           K = 100 x (1 - exp(-K_raw / tau)); each negative word halves K
  U  urgency    = urgency tier 0-3 of the matched objective (one-time words lift it to 2)
  W  workload   = simulated hands-on hours H (11 stages x triangular), 20 h = 100, 150 h = 0
  Final         = A x (0.70 + 0.15 x U/3 + 0.15 x W/100)     A is a multiplier: urgency and
                  low workload can adjust a good fit by up to 30% but cannot rescue a bad one.
  Suggested decision: Skip if A < 50; Route if another NC office should apply; Waitlist if
  forecast / no deadline; Chase if Final >= 60; otherwise "You decide". A person decides.

TF-IDF is the baseline semantic signal. TODO(Criteria): try Azure embeddings (core.llm.embed)
for S and compare precision on the labeled set before switching.
"""
from __future__ import annotations

import json
import re
from datetime import date

import numpy as np
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from core import config, storage
from core.reference import load_profile
from core.schemas import Objective, Opportunity, ScoreBreakdown, ScoredItem

# --------------------------------------------------------------------------- #
# Parameters (provisional values from the Final rules) — tune with eval/labels
# --------------------------------------------------------------------------- #
A_SKIP = 50                          # Skip if plan match A is below this line
FINAL_CHASE = 60                     # suggest Chase if Final score >= this line
WEIGHTS = {"base": 0.70, "U": 0.15, "W": 0.15}
H_EASY, H_HARD = 20, 150             # hours -> workload score
N_SIMS = 10_000
SYNOPSIS_CHARS = 1500
S_TOP_PCT = 90
RESEARCH_PENALTY = {"nih": 0.4, "other": 0.75}
# Calibration measured on the full live feed (1,459 listings, 2026-11-07). Used whenever the current
# batch is too small to calibrate itself (e.g. the 40-record test fixture). The original script fell
# back to L=0.20 / H=0.52, which are on a different scale from TF-IDF cosines and made every S = 0.
CALIBRATION_DEFAULT = {"L": 0.012, "H": 0.097, "tau": 23.65}
NEGATIVE_WORDS = ["clinical", "biomedical", "patient", "psychological resilience", "research infrastructure"]
ONE_TIME_WORDS = ["supplemental", "disaster", "emergency", "one-time", "recovery"]
STOPWORDS = list(ENGLISH_STOP_WORDS | {
    "program", "programs", "grant", "grants", "fy", "fiscal", "year", "award", "awards", "development", "community",
    "communities", "research", "center", "centers", "national", "support", "assistance", "technical", "partnership",
    "partnerships", "initiative", "project", "projects", "services", "opportunity", "notice", "funding", "cooperative",
    "agreement", "state", "states", "local", "recurring", "announcement", "new", "based", "access", "improve",
    "increase", "provide", "statewide", "department", "dncr", "sites", "site", "carolina", "north", "safety",
    "health", "public", "network", "innovation", "plan", "management", "systems", "action", "data"})

NIH = re.compile(r"National Institutes of Health|^HHS-NIH", re.I)
DNCR_TYPE_FUNDERS = re.compile(r"National Park Service|Endowment for the Arts|Endowment for the Humanities|Museum and Library|"
                               r"Forest Service|Fish and Wildlife|^DOI-NPS|^NEA|^NEH|^IMLS|^USDA-FS|^DOI-FWS", re.I)

# Who should apply (agency regex, title regex or None, NC owner). CFDA-based rules are a TODO.
ROUTE_RULES = [
    (r"FEMA|Homeland Security", None, "NC Emergency Management (DPS)"),
    (r"National Institutes of Health|Centers for Disease|Health Resources|Children & Families|SAMHSA|"
     r"Food and Drug|Indian Health|Medicare|Community Living|Assistant Secretary for Health|Defense Health", None, "NC DHHS"),
    (r"Environmental Protection", None, "NC DEQ"),
    (r"Department of Education", None, "NC DPI"),
    (r"Justice|Violence Against Women|Juvenile Justice|Victims of Crime", None, "NC Dept of Public Safety (confirm)"),
    (r"Housing and Urban", None, "NC Commerce / Housing (confirm)"),
    (r"Employment and Training|Department of Labor", None, "NC Commerce"),
    (r"Fish and Wildlife", r"wildlife grant|sport fish|wildlife restoration|hunter", "NC Wildlife Resources Commission"),
    (r"Forest Service", r"fire|wildland", "NCDA&CS Forest Service"),
    (r"Natural Resources Conservation|Rural|Agricultural Marketing|Farm Service", None, "NCDA&CS (confirm)"),
    (r"Oceanic|NOAA", r"fisher|coastal zone", "NC DEQ (Coastal Mgmt / Marine Fisheries)"),
    (r"Economic Development Administration|\bEDA\b", None, "NC Commerce"),
    (r"Veterans|State Home", None, "NC Dept of Military & Veterans Affairs"),
    (r"Highway|Transit|Aviation|Railroad", r"^(?!.*recreational trails).*$", "NCDOT"),
    (r"Local Defense Community|Army|Air Force|Navy|Munitions|AFRL|Materiel", None, "NC Dept of Military & Veterans Affairs (confirm)"),
]
PASS_THROUGH = r"outdoor recreation legacy|recreational trails|land and water conservation|subgrant"

# Workload model: 11 stages, (min, likely, max) hands-on hours for the one grants person
STAGES = ["discover", "read NOFO", "go/no-go", "registration", "partners & match", "narrative",
          "budget", "documents", "OSBM notice", "review & submit", "follow-up"]
BASE = {"discover": (0.5, 1, 2), "read NOFO": (2, 3, 5), "go/no-go": (1, 2, 4), "registration": (0.5, 1, 2),
        "partners & match": (2, 4, 8), "narrative": (20, 30, 45), "budget": (6, 10, 16), "documents": (6, 10, 16),
        "OSBM notice": (1, 2, 3), "review & submit": (4, 6, 10), "follow-up": (2, 4, 8)}
ANCHORS = {
    "standard": {},
    "simple": {"narrative": (6, 10, 15), "budget": (3, 5, 8), "documents": (2, 4, 6),
               "partners & match": (0, 1, 2), "go/no-go": (0.5, 1, 2)},
    "complex": {"partners & match": (8, 15, 25), "narrative": (30, 45, 70), "budget": (15, 25, 40),
                "documents": (20, 35, 60), "go/no-go": (2, 4, 6), "review & submit": (6, 10, 16)},
    "pass-through": {"partners & match": (10, 20, 30), "documents": (10, 20, 30)},
    "disaster": {"narrative": (10, 15, 25), "go/no-go": (0.5, 1, 2), "partners & match": (1, 2, 4)},
}
_SIM_CACHE: dict[str, dict[str, float]] = {}


# --------------------------------------------------------------------------- #
# Plan match A
# --------------------------------------------------------------------------- #
def match_text(o: Opportunity) -> tuple[str, str, str]:
    title = re.sub(r"\s+", " ", o.title).strip()
    syn = re.sub(r"\s+", " ", o.description).strip()[:SYNOPSIS_CHARS]
    return title, syn, f"{title}. {syn}".strip(". ")


def research_penalty(o: Opportunity) -> tuple[float, str]:
    who = f"{o.agency_name} {o.agency_code}"
    if NIH.search(o.agency_name) or o.agency_code.upper().startswith("HHS-NIH"):
        return RESEARCH_PENALTY["nih"], "NIH research x0.4"
    if re.search(r"National Science Foundation|NASA|Aeronautics|\bBAA\b|Broad Agency Announcement|research announcement|"
                 r"\((?:R|U|K|P|T|F|UG|UH)\d{1,2}\b|Research Program|Cancer|Clinical", f"{who} {o.title}", re.I):
        return RESEARCH_PENALTY["other"], "research x0.75"
    return 1.0, ""


def plan_match(opps: list[Opportunity], objectives: list[Objective]):
    """Score every opportunity against every objective. Calibration (L, H, tau) uses the whole feed."""
    texts = [match_text(o) for o in opps]
    titles = np.array([t[0].lower() for t in texts], dtype=object)
    syns = np.array([t[1].lower() for t in texts], dtype=object)
    full = [f"{t} {s}" for t, s in zip(titles, syns, strict=True)]
    obj_docs = [f"{ob.text} {' '.join(ob.keywords)}" for ob in objectives]
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words=STOPWORDS, sublinear_tf=True).fit(obj_docs + [t[2] for t in texts])
    cos = cosine_similarity(vec.transform([t[2] for t in texts]), vec.transform(obj_docs))

    toks = [{w[:6] for w in re.findall(r"[a-z0-9]+", f)} for f in full]
    n_docs = len(opps)
    kraw = np.zeros_like(cos)
    hits = [[[] for _ in objectives] for _ in opps]
    for j, ob in enumerate(objectives):
        for ph in ob.keywords:
            ph_l = ph.lower()
            df_count = sum(ph_l in f for f in full)
            w_idf = np.log((n_docs + 1) / (df_count + 1)) + 1
            ph_toks = {w[:6] for w in re.findall(r"[a-z0-9]+", ph_l)}
            for i in range(n_docs):
                if ph_l in titles[i]:
                    strength, exact = 2.0, True
                elif ph_l in syns[i]:
                    strength, exact = 1.0, True
                else:
                    share = len(toks[i] & ph_toks) / max(len(ph_toks), 1)
                    strength, exact = (0.5 * share if share >= 0.5 else 0.0), False
                if strength:
                    kraw[i, j] += w_idf * strength
                    hits[i][j].append(ph + ("" if exact else "~"))

    best_cos = cos.max(1)
    nih = np.array([bool(NIH.search(o.agency_name) or o.agency_code.upper().startswith("HHS-NIH")) for o in opps])
    ref = np.array([bool(DNCR_TYPE_FUNDERS.search(o.agency_name) or DNCR_TYPE_FUNDERS.search(o.agency_code)) for o in opps])
    ref_k = kraw.max(1)[ref]
    if nih.sum() >= 20 and ref.sum() >= 20 and (ref_k > 0).sum() >= 20:   # big enough to calibrate itself
        L = float(np.median(best_cos[nih]))
        H = float(np.percentile(best_cos[ref], S_TOP_PCT))
        tau = float(np.median(ref_k[ref_k > 0]))
    else:
        L, H, tau = CALIBRATION_DEFAULT["L"], CALIBRATION_DEFAULT["H"], CALIBRATION_DEFAULT["tau"]
    if H <= L:
        L, H = CALIBRATION_DEFAULT["L"], CALIBRATION_DEFAULT["H"]
    S = np.clip((cos - L) / max(H - L, 1e-6), 0, 1) * 100
    K = 100 * (1 - np.exp(-kraw / tau))
    neg = np.array([sum(w in f for w in NEGATIVE_WORDS) for f in full])
    K = K * (0.5 ** neg[:, None])
    A_obj = (2 * S + K) / 3
    return S, K, A_obj, hits, neg, {"L": round(L, 3), "H": round(H, 3), "tau": round(tau, 2)}


# --------------------------------------------------------------------------- #
# Urgency U, workload W, final score, route, decision
# --------------------------------------------------------------------------- #
def urgency(ob: Objective, title: str, today: date) -> tuple[int, str]:
    u = ob.urgency_tier if ob.urgency_tier is not None else {"normal": 1, "elevated": 2, "high": 2, "highest": 3}[ob.urgency]
    if ob.objective_id == "3.2" and today > date(2026, 12, 31):
        u = 1  # A250 window has passed
    evidence = f"Objective {ob.objective_id} urgency tier (plan p. {ob.page})"
    words = [w for w in ONE_TIME_WORDS if w in title.lower()]
    if words and u < 2:
        u, evidence = 2, f"one-time window: title says '{words[0]}'"
    return u, evidence


def pick_anchor(title: str) -> tuple[str, str]:
    t = title.lower()
    if re.search(r"disaster|emergency|supplemental|hurricane|recovery", t):
        return "disaster", "title mentions disaster/emergency/recovery"
    if re.search(PASS_THROUGH, t):
        return "pass-through", "DNCR collects local applications"
    if re.search(r"acquisition|construction|land |capital|infrastructure|facilit|building", t):
        return "complex", "construction / land / capital work"
    if re.search(r"partnership agreement|grants to states|formula|apportion|indemnity", t):
        return "simple", "formula / renewal-type application"
    return "standard", "standard competitive application"


def simulate_hours(anchor: str) -> dict[str, float]:
    """10,000-run simulation of total hands-on hours (sum of 11 triangular stages). Seeded: reproducible."""
    if anchor not in _SIM_CACHE:
        rng = np.random.default_rng(42)
        st = {**BASE, **ANCHORS[anchor]}
        hours = np.sum([rng.triangular(a, m, b if b > a else a + 1e-9, N_SIMS) for a, m, b in (st[s] for s in STAGES)], axis=0)
        _SIM_CACHE[anchor] = {"H": float(np.median(hours)), "H10": float(np.percentile(hours, 10)), "H90": float(np.percentile(hours, 90))}
    return _SIM_CACHE[anchor]


def workload_score(h: float) -> float:
    return float(np.clip((H_HARD - h) / (H_HARD - H_EASY), 0, 1) * 100)


def final_score(A: float, U: int, W: float) -> float:
    return A * (WEIGHTS["base"] + WEIGHTS["U"] * U / 3 + WEIGHTS["W"] * W / 100)


def route(o: Opportunity, ob: Objective) -> tuple[str, bool]:
    if re.search(PASS_THROUGH, o.title, re.I):
        return "DNCR leads (pass-through to localities)", True
    agency = f"{o.agency_name} {o.agency_code}"
    for ag, ti, owner in ROUTE_RULES:
        if re.search(ag, agency, re.I) and (ti is None or re.search(ti, o.title, re.I)):
            return owner, False
    return f"DNCR - {ob.divisions[0] if ob.divisions else 'DNCR'}", True


def decide(A: float, final: float, dncr: bool, forecast: bool, route_to: str) -> tuple[str, str]:
    if A < A_SKIP:
        return "Skip", f"plan match A = {A:.0f} is below the Skip line {A_SKIP}"
    if not dncr:
        return "Route", f"{route_to} should apply, not DNCR"
    if forecast:
        return "Waitlist", "no deadline posted (forecast or rolling): watch in Early Warning"
    if final >= FINAL_CHASE:
        return "Chase", f"Final score {final:.0f} is at or above the Chase line {FINAL_CHASE}"
    return "You decide", f"fits DNCR, but Final score {final:.0f} is below the Chase line {FINAL_CHASE}"


def confidence(A: float, final: float, forecast: bool) -> str:
    level = "High" if A >= A_SKIP and final >= FINAL_CHASE else "Medium" if A >= A_SKIP else "Low"
    return "Medium" if forecast and level == "High" else level


def _timing(o: Opportunity) -> float:
    if o.status == "forecasted":
        return 0.5
    if o.days_to_close is None:
        return 0.8
    return 1.0 if o.days_to_close >= 30 else 0.6 if o.days_to_close >= 14 else 0.3


def _annual_programs() -> list[dict]:
    path = config.REFERENCE_DIR / "annual_programs.json"
    return json.loads(path.read_text(encoding="utf-8"))["programs"] if path.exists() else []


def _already_yours(o: Opportunity) -> bool:
    """CFDA alone is not enough (15.916 = LWCF state program AND the competitive ORLP)."""
    for p in _annual_programs():
        if p["cfda"] in o.cfda and fuzz.token_set_ratio(p["name"].lower(), o.title.lower()) >= 60:
            return True
    return False


def _dedup_key(title: str) -> str:
    t = re.sub(r"fiscal year|\bfy\s?\d{2,4}|20\d\d|[^a-z ]", " ", title.lower())
    return " ".join(t.split())


def rationale(row: dict, ob: Objective) -> str:
    parts = [f"Final score {row['final']:.0f} = plan match {row['A']:.0f} x ({WEIGHTS['base']} + {WEIGHTS['U']} x urgency "
             f"{row['U']}/3 + {WEIGHTS['W']} x workload {row['W']:.0f}/100).",
             f"Plan match: Objective {ob.objective_id} ({ob.text[:70]}...), plan p. {ob.page}; meaning {row['S']:.0f}, "
             f"keywords {row['K']:.0f}" + (f" (matched '{row['phrases']}')" if row["phrases"] else "")
             + (f"; {row['penalty']}" if row["penalty"] else "") + ".",
             f"Urgency U = {row['U']} ({row['U_evidence']}).",
             f"Workload: about {row['H']:.0f} hours ({row['H10']:.0f}-{row['H90']:.0f}), {row['anchor']} profile ({row['anchor_why']}).",
             f"Who applies: {row['route']}."]
    if row["rounds"] > 1:
        parts.append(f"{row['rounds']} rounds of this program were merged into one row.")
    parts.append(f"Suggested: {row['decision']} because {row['decision_why']}.")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Agent entry point
# --------------------------------------------------------------------------- #
def score_all(opps: list[Opportunity], passed: set[str], today: date | None = None) -> tuple[list[ScoredItem], dict]:
    today = today or date.today()
    profile = load_profile()
    objectives = profile.objectives
    S, K, A_obj, hits, neg, cal = plan_match(opps, objectives)

    rows = []
    for i, o in enumerate(opps):
        if o.id not in passed:
            continue
        order = np.argsort(-A_obj[i])
        j, j2 = int(order[0]), int(order[1])
        ob = objectives[j]
        pen, pen_txt = research_penalty(o)
        A = float(A_obj[i, j] * pen)
        U, U_ev = urgency(ob, o.title, today)
        anchor, anchor_why = pick_anchor(o.title)
        h = simulate_hours(anchor)
        W = workload_score(h["H"])
        final = final_score(A, U, W)
        route_to, dncr = route(o, ob)
        forecast = o.status == "forecasted" or o.close_date is None
        decision, why = decide(A, final, dncr, forecast, route_to)
        rows.append({"o": o, "ob": ob, "ob2": objectives[j2], "A": A, "S": float(S[i, j]), "K": float(K[i, j]), "U": U,
                     "U_evidence": U_ev, "W": W, **h, "anchor": anchor, "anchor_why": anchor_why, "final": final,
                     "route": route_to, "dncr": dncr, "forecast": forecast, "decision": decision, "decision_why": why,
                     "penalty": pen_txt, "phrases": ", ".join(hits[i][j]), "neg": int(neg[i]), "key": _dedup_key(o.title)})

    # merge rounds of the same program: keep the one with the latest deadline
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["key"], []).append(r)
    kept = []
    for grp in groups.values():
        grp.sort(key=lambda r: r["o"].close_date or "9999-12-31", reverse=True)
        grp[0]["rounds"] = len(grp)
        kept.append(grp[0])

    items = []
    for r in kept:
        o, ob = r["o"], r["ob"]
        flags = []
        if r["rounds"] > 1:
            flags.append(f"{r['rounds']} rounds merged; latest deadline shown")
        if r["forecast"]:
            flags.append("forecast/rolling: no deadline")
        codes = {a.id for a in o.applicant_types}
        items.append(ScoredItem(
            opp_id=o.id,
            score=round(r["final"], 1),
            final=round(r["final"], 1),
            confidence=confidence(r["A"], r["final"], r["forecast"]),
            breakdown=ScoreBreakdown(semantic=round(r["S"] / 100, 3), keyword=round(r["K"] / 100, 3),
                                     eligibility=1.0 if codes & {"00", "99"} else 0.5, urgency=round(r["U"] / 3, 3),
                                     timing=_timing(o), funder=1.0 if DNCR_TYPE_FUNDERS.search(f"{o.agency_name} {o.agency_code}") else 0.0),
            components={k: round(float(r[k]), 1) for k in ("A", "S", "K", "U", "W", "H", "H10", "H90")}
                       | {"penalty": research_penalty(o)[0], "negative_words": r["neg"], "rounds": r["rounds"]},
            objective_id=ob.objective_id,
            second_objective_id=r["ob2"].objective_id,
            division=ob.divisions[0] if ob.divisions else None,
            already_yours=_already_yours(o),
            watch=r["forecast"],
            route_to=r["route"],
            suggested_decision=r["decision"],
            decision_reason=r["decision_why"],
            rationale=rationale(r, ob),
            flags=flags,
        ))
    items.sort(key=lambda s: (s.score, s.components.get("A", 0)), reverse=True)
    return items, cal


def run(state: dict) -> dict:
    opps = [Opportunity(**o) for o in state["opportunities"]]
    passed = {g["opp_id"] for g in state["gates"] if g["passed"]}
    if not passed:
        return {"scored": [], "top": []}
    items, cal = score_all(opps, passed)
    storage.write_json("scored/calibration.json", cal)
    scored = [s.model_dump() for s in items]
    # the short list (cards are written only for these): everything not suggested Skip, best first
    top = [s for s in scored if s["suggested_decision"] != "Skip"][: config.TOP_N]
    return {"scored": scored, "top": top}
