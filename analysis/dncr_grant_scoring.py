"""
DNCR Grant Scoring - implements "DNCR Grant Scoring Rules (Final)".

Steps per grant:
  1. Can we apply?   gates G1 applicant, G2 region, G3 status (pass / fail, not scored)
  2. Five factors chosen for what the client cares about (fixed):
       A plan match (is it ours?)      = max( (S + K)/2 x research penalty , 70 if the listing names an agency type DNCR is )
       P chance of winning (0-1)        = 1 formula / every-state; awards/56 if only states may apply; published rate; else 0.25
       V value (0-1)                    = log of the typical award, $10k = 0 ... $3M = 1
       U urgency (0-3)                  = plan urgency tier of the matched objective
       W workload (0-100)               = simulated hours, fewer hours = higher score
  3. Final score = A x (0.30 + 0.20 P + 0.30 V + 0.15 U/3 + 0.05 W/100)
     A's inner parameters: nested cross-validation on past wins. The five weights: chosen by testing
     (budget simulation as objective, past-wins blind test as guardrail, 1-SE rule; human annotation pending); see weights.md.
  4. Button = gate / route / forecast rules, then Skip line (A < 35) and Chase line

Input : either
  --live : the live federal opportunity feed (Grants.gov public API, no key needed): every forecasted or
           posted listing a state can apply for, with synopsis, applicant types, eligibility text and CFDA; or
  --input: a Grants.gov search export (.csv), or the team's listing table (.xlsx, sheet "All Grants (Stage 1)").
Output: Excel with the top-N grants by Final score, plus a rationale per grant.

Usage : python dncr_grant_scoring.py --live --out DNCR_Top50_Grants.xlsx            (live Grants.gov feed)
        python dncr_grant_scoring.py --input data/grants_source.xlsx --out DNCR_Top50_Grants.xlsx
"""
import argparse
import re
from datetime import date

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ----------------------------------------------------------------------------
# Parameters (config) - provisional values from the Final rules
# ----------------------------------------------------------------------------
AS_OF_DATE = date.today()          # "today" = run date, not download date
TOP_N = 50
A_SKIP = 35                        # Skip if plan match A is below this line (CV: knee of wins kept vs listings to review)
FINAL_CHASE = 38                   # Chase list sized to about one year of her hours (10 h/week = 520 h)
# Final = A x M  (fit x small chance nudge). U and hours are shown, not scored.
# M only nudges when we KNOW something about the chance of winning; otherwise M = 1 (no change):
#   1 formula / every-state program          -> M = 1 + BOOST            (near-certain money)
#   2 only state governments can apply       -> M = 1 + BOOST x (2 x awards/56 - 1), between 1-BOOST and 1+BOOST
#   3 anyone can apply / nothing known        -> M = 1
BOOST = 0.0                        # superseded: P now carries the formula signal inside the weights below
WEIGHTS = {"b": 0.30, "P": 0.20, "V": 0.30, "U": 0.15, "W": 0.05}   # chosen by testing (weights.md)
AWARD_LO, AWARD_HI = 1e4, 3e6      # V = 0 at $10k, 1 at $3M (log scale)
V_UNKNOWN = 0.5                    # no award amount in the listing: neutral value, neither advantage nor penalty
W_S = 0.50                         # CV-chosen: A = W_S x meaning S + (1 - W_S) x keywords K (was 2/3)
ENTITY_FLOOR = 70                  # CV-chosen: A is at least 70 when the listing names an agency type DNCR is
ENTITY = re.compile(r"state (and jurisdictional )?arts agenc|state historic preservation office|\bshpos?\b|state library administrative|"
                    r"state historical records advisory|state archiv|state museum|state park|natural heritage program", re.I)
N_STATES = 56
P_STATE_UNKNOWN = 0.50             # case 2 when the listing gives no award count (estimate)
P_DEFAULT = 0.25                   # case 3 when no success rate is published (estimate)
FORMULA_WORDS = re.compile(r"\bformula\b|apportion|non.?competitive|funds to the states|"
                           r"grants? to state (historic preservation offices|library|arts agenc)|"
                           r"\b(each|every|all \d* ?) ?(state|states and territories)\b.{0,40}(receive|allocat|distribut|eligible for an award)|"
                           r"in every state|awarded to the nation.s \d+ state", re.I)
# published success rates (program title regex -> (rate, source)); add more as you find them
PUBLISHED_RATE = [
    (r"museums for america", 0.32, "IMLS: 152 awards of 470 applications (Jul 2012 announcement)"),
]
H_EASY, H_HARD = 20, 150           # hours -> workload score: 20 h = 100, 150 h = 0
N_SIMS = 10_000
SYNOPSIS_COLUMNS = {"synopsis", "description", "synopsis description", "summary", "abstract"}
SYNOPSIS_CHARS = 1500              # title + first 1,500 characters of the synopsis are matched
S_TOP_PCT = 90                     # S = 100 at the 90th percentile of DNCR-type funders' similarity
RESEARCH_PENALTY = {"nih": 0.4, "other": 0.75}
NEGATIVE_WORDS = ["clinical", "biomedical", "patient", "psychological resilience",
                  "research infrastructure"]
ONE_TIME_WORDS = ["supplemental", "disaster", "emergency", "one-time", "recovery"]
RNG = np.random.default_rng(42)
# Generic grant words carry no meaning about DNCR fit; removed before similarity
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
STOPWORDS = list(ENGLISH_STOP_WORDS | {
    "program", "programs", "grant", "grants", "fy", "fiscal", "year", "award", "awards", "development", "community",
    "communities", "research", "center", "centers", "national", "support", "assistance", "technical", "partnership",
    "partnerships", "initiative", "project", "projects", "services", "opportunity", "notice", "funding", "cooperative",
    "agreement", "state", "states", "local", "recurring", "announcement", "new", "based", "access", "improve",
    "increase", "provide", "statewide", "department", "dncr", "sites", "site", "carolina", "north", "safety",
    "health", "public", "network", "innovation", "plan", "management", "systems", "action", "data"})

# ----------------------------------------------------------------------------
# DNCR profile: 25 objectives (DNCR Strategic Plan 2025-2029), urgency, phrases
# ----------------------------------------------------------------------------
OBJECTIVES = {
 "1.1": (6, 1, "Support and retain K-12 educators with professional development and year-round educator training",
         ["educator", "teacher", "professional development", "k-12", "stem education", "teacher training"]),
 "1.2": (6, 1, "Increase access to DNCR sites through field trip funds and support services for students in all 100 counties",
         ["field trip", "student access", "school visits"]),
 "1.3": (6, 1, "Increase classroom engagement through curriculum development, classroom visits, virtual field trips and extracurricular programs",
         ["curriculum", "classroom", "extracurricular", "k-12"]),
 "1.4": (7, 1, "Improve educational opportunities at DNCR sites through exhibit updates and site lesson plans",
         ["exhibit", "exhibition", "museum", "museums", "interpretation", "artifacts"]),
 "1.5": (7, 1, "Support lifelong learning by increasing digital access to collections and professional resources",
         ["digitization", "digital collections", "collections", "archives", "archival", "lifelong learning", "stewardship", "libraries"]),
 "2.1": (8, 1, "Increase web and social media traffic and site visitation and cultural tourism",
         ["tourism", "visitation", "visitors", "marketing"]),
 "2.2": (8, 1, "Establish local partnerships with tourism boards, planners, businesses and communities",
         ["cultural tourism", "heritage tourism", "main street"]),
 "2.3": (8, 2, "Foster local economies by supporting arts and music, history, libraries, sciences and outdoor recreation through grants in all 100 counties",
         ["arts", "music", "humanities", "cultural", "folk", "heritage", "artists", "artistic"]),
 "2.4": (9, 2, "Ensure all sites have internet and broadband access and expand digital skills statewide",
         ["broadband", "internet", "digital equity", "digital skills", "connectivity"]),
 "3.1": (10, 1, "Keep capital projects on schedule and in budget; facility construction and renovation",
         ["historic building", "renovation", "capital improvement"]),
 "3.2": (10, 3, "Lead research and storytelling for America's 250th anniversary (A250) through 2026",
         ["america 250", "america250", "semiquincentennial", "250th", "revolutionary", "american revolution"]),
 "3.3": (10, 1, "Study admission discounts for EBT, veteran and senior visitors to broaden access",
         ["admission", "discount", "veterans", "seniors", "access for"]),
 "3.4": (10, 1, "Expand digital resource offerings across divisions to make work more accessible",
         ["digital resources", "online resources", "digital humanities", "open access"]),
 "3.5": (10, 2, "Increase access to public green spaces like parks, trails and nature preserves within a 10-minute drive",
         ["park", "parks", "trail", "trails", "green space", "outdoor recreation", "recreation", "nature preserve", "public lands", "open space"]),
 "3.6": (11, 2, "Lead restoration and protection of natural and cultural resources: historic preservation, battlefields, habitat and land conservation",
         ["historic preservation", "preservation", "restoration", "conservation", "habitat", "battlefield", "historic", "archaeolog",
          "land acquisition", "natural heritage", "wildlife", "ecosystem", "invasive species", "forest legacy"]),
 "4.1": (12, 1, "Analyze responsible use of artificial intelligence and launch AI pilot programs",
         ["artificial intelligence"]),
 "4.2": (12, 0, "Identify and address agency-wide internal policy gaps", ["internal policy"]),
 "4.3": (12, 0, "Decrease staff vacancies, reduce deferred maintenance and improve department safety",
         ["deferred maintenance", "staff vacancy"]),
 "4.4": (12, 0, "Launch the DNCR rebrand and staff onboarding", ["rebrand", "branding"]),
 "4.5": (12, 0, "Improve internal communication and cross-division collaboration", ["internal communication"]),
 "4.6": (13, 1, "Provide public participation and engagement in planning initiatives",
         ["public participation", "community engagement", "public engagement"]),
 "5.1": (14, 3, "Recover DNCR sites impacted by Hurricane Helene to 80 percent operational by 2028",
         ["helene", "hurricane", "disaster recovery", "storm damage", "disaster"]),
 "5.2": (14, 3, "Support western North Carolina economic recovery for arts, libraries and outdoor tourism after Helene",
         ["western north carolina", "helene recovery", "outdoor tourism"]),
 "5.3": (14, 2, "Evaluate climate hazards and risks across DNCR sites and embed resilience in planning",
         ["climate resilience", "climate risk", "resilience", "flood mitigation", "climate adaptation"]),
 "5.4": (14, 2, "Manage climate mitigation, resilience and adaptation of North Carolina ecosystems; tree planting and conservation acreage",
         ["ecosystem resilience", "tree planting", "forest", "forestry", "wetland", "wetlands", "urban and community forestry", "conservation easement"]),
}
DIVISION = {"1": "DNCR museums / education", "2.1": "DNCR marketing", "2.2": "DNCR sites",
            "2.3": "Arts Council / State Library", "2.4": "DNCR IT", "3.1": "DNCR capital projects",
            "3.2": "Archives & History (A250)", "3.3": "DNCR sites", "3.4": "DNCR digital",
            "3.5": "State Parks", "3.6": "Natural Heritage / Historic Preservation Office",
            "4": "DNCR administration", "5": "Land & Water Stewardship / Helene recovery"}

# ----------------------------------------------------------------------------
# Route table (CFDA is not in this extract, so agency / title rules stand in)
# ----------------------------------------------------------------------------
ROUTE_RULES = [  # (agency regex, title regex or None, NC owner)
    (r"FEMA|Homeland Security", None, "NC Emergency Management (DPS)"),
    (r"National Institutes of Health|Centers for Disease|Health Resources|Children & Families|SAMHSA|"
     r"Food and Drug|Indian Health|Medicare|Community Living|Assistant Secretary for Health|Defense Health", None, "NC DHHS"),
    (r"Environmental Protection", None, "NC DEQ"),
    (r"Department of Education", None, "NC DPI"),
    (r"Justice|Violence Against Women|Juvenile Justice|Victims of Crime", None, "NC Dept of Public Safety (confirm)"),
    (r"Housing and Urban", None, "NC Commerce / Housing (confirm)"),
    (r"Employment and Training|Department of Labor", None, "NC Commerce"),
    (r"Fish and Wildlife", r"wildlife grant|sport fish|wildlife restoration|hunter|cooperative endangered species|section 6",
     "NC Wildlife Resources Commission (confirm; plants: NCDA&CS Plant Conservation)"),
    (r"Forest Service", r"fire|wildland|landscape scale|forest legacy|forest stewardship|urban and community forest", "NCDA&CS Forest Service"),
    (r"National Park Service", r"internship|business plan", "Universities / student program (not DNCR)"),
    (r"Natural Resources Conservation|Rural|Agricultural Marketing|Farm Service", None, "NCDA&CS (confirm)"),
    (r"Oceanic|NOAA", r"fisher|coastal zone|darrp|damage assessment", "NC DEQ (Coastal Mgmt / Marine Fisheries / NRDA trustee)"),
    (r"Economic Development Administration|EDA", None, "NC Commerce"),
    (r"Veterans|State Home", None, "NC Dept of Military & Veterans Affairs"),
    (r"Highway|Transit|Aviation|Railroad", r"^(?!.*recreational trails).*$", "NCDOT"),
    (r"Local Defense Community|Army|Air Force|Navy|Munitions|AFRL|Materiel", None, "NC Dept of Military & Veterans Affairs (confirm)"),
]
PASS_THROUGH = r"outdoor recreation legacy|recreational trails|land and water conservation|subgrant"


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def load(path):
    df = pd.read_excel(path, sheet_name="All Grants (Stage 1)")
    df = df.rename(columns={"Opportunity #": "opp", "Title": "title", "Agency": "agency",
                            "Deadline": "deadline", "Eligibility decision": "elig", "Reason": "elig_reason"})
    df["title"] = df["title"].map(norm)
    df["deadline"] = pd.to_datetime(df["deadline"], errors="coerce")
    # optional synopsis column (Grants.gov "synopsis" / "description"); matching uses title + first 1,500 chars
    syn = next((c for c in df.columns if str(c).strip().lower() in SYNOPSIS_COLUMNS), None)
    df["synopsis"] = df[syn].map(norm).str[:SYNOPSIS_CHARS] if syn else ""
    df["match_text"] = (df["title"] + ". " + df["synopsis"]).str.strip(". ")
    return df[["opp", "title", "agency", "deadline", "elig", "elig_reason", "synopsis", "match_text"]]


# ----------------------------------------------------------------------------
# Live federal opportunity feed: Grants.gov public API (search2 + fetchOpportunity, no key)
# ----------------------------------------------------------------------------
GG_SEARCH = "https://api.grants.gov/v1/api/search2"
GG_FETCH = "https://api.grants.gov/v1/api/fetchOpportunity"
STATE_CODES = "00|25|99"           # State governments | Others (see text) | Unrestricted
OTHER_REGION = re.compile(r"\b(gulf (coast|of mexico|states)|great lakes|chesapeake|pacific islands?|western states|"
                          r"alaska|hawai.?i|puerto rico|guam|u\.?s\.? virgin islands|delta regional|colorado river|"
                          r"northern border|mekong|appropriate to the (state|region) of|restore act)\b", re.I)
NC_OK = re.compile(r"north carolina|southeast|appalachia|atlantic|all (50 )?states|nationwide|alaska native|"
                   r"american indian|native hawaiian", re.I)


def _gg_post(url, payload, retries=3):
    import time, requests
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=40)
            if r.status_code == 200 and r.json().get("errorcode", 0) == 0:
                return r.json().get("data") or {}
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def _clean_html(t):
    return norm(re.sub(r"<[^>]+>", " ", str(t or "")))


def live_row(hit, detail):
    d = detail or {}
    body = d.get("synopsis") or d.get("forecast") or {}
    types = [str(a.get("description", "")) for a in (body.get("applicantTypes") or [])]
    elig_txt = _clean_html(body.get("applicantEligibilityDesc") or body.get("applicantEligibilityDescription"))
    synopsis = _clean_html(body.get("synopsisDesc") or body.get("forecastDesc") or body.get("description"))
    tl = " ".join(types).lower()
    if "state governments" in tl:
        reason = "state government is eligible"
    elif "unrestricted" in tl:
        reason = "state government is eligible (unrestricted)"
    elif "other" in tl and re.search(r"\bstates?\b|state agenc|state government", elig_txt, re.I):
        reason = "eligible only as applicant type 'other' (text mentions states)"
    elif not types:
        reason = "applicant types not stated ('other')"
    else:
        reason = "state government is not an eligible applicant"
    scope = f"{hit.get('title', '')} {elig_txt}"
    if OTHER_REGION.search(scope) and not NC_OK.search(scope):
        reason = "Geographically restricted: " + OTHER_REGION.search(scope).group(0)
    close = hit.get("closeDate") or body.get("responseDate") or body.get("estApplicationResponseDate")
    cfda = [c.get("cfdaNumber") for c in (d.get("cfdas") or []) if isinstance(c, dict)] or hit.get("alnist") or []
    return {"opp": hit.get("number"), "title": norm(hit.get("title")),
            "agency": norm(body.get("agencyName") or hit.get("agency") or hit.get("agencyCode")),
            "deadline": pd.to_datetime(close, errors="coerce"), "elig": reason, "elig_reason": reason,
            "synopsis": synopsis[:SYNOPSIS_CHARS], "cfda": ", ".join(map(str, cfda)),
            "status": hit.get("oppStatus"), "url": f"https://www.grants.gov/search-results-detail/{hit.get('id')}"}


def load_live(cap=3000, workers=8, cache_path="grants_live_cache.json"):
    """Pull every forecasted/posted listing a state can apply for, then fetch each record's details."""
    import json, os
    from concurrent.futures import ThreadPoolExecutor
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    hits, start = [], 0
    while start < cap:
        data = _gg_post(GG_SEARCH, {"rows": 250, "startRecordNum": start, "oppStatuses": "forecasted|posted",
                                    "eligibilities": STATE_CODES})
        if data is None:
            raise SystemExit("Grants.gov search failed: check the network allows api.grants.gov")
        page = data.get("oppHits") or []
        hits += page
        start += len(page)
        if not page or start >= int(data.get("hitCount") or 0):
            break
    todo = [h for h in hits if str(h["id"]) not in cache]
    with ThreadPoolExecutor(workers) as ex:
        for h, det in zip(todo, ex.map(lambda h: _gg_post(GG_FETCH, {"opportunityId": int(h["id"])}, 2), todo)):
            if det is not None:
                cache[str(h["id"])] = det
    json.dump(cache, open(cache_path, "w"))
    df = pd.DataFrame([live_row(h, cache.get(str(h["id"]))) for h in hits])
    df["match_text"] = (df["title"] + ". " + df["synopsis"]).str.strip(". ")
    print(f"live feed: {len(hits)} listings, {len(todo)} new detail records fetched (cache: {cache_path})")
    return df


def load_csv(path):
    """Grants.gov search export (CSV from grants.gov / simpler.grants.gov 'Export results')."""
    raw = pd.read_csv(path, low_memory=False)
    rows = []
    for _, r in raw.iterrows():
        types = [t.strip() for t in str(r.get("applicant_types") or "").split(";") if t.strip() and t.strip() != "nan"]
        elig_txt = _clean_html(r.get("applicant_eligibility_description"))
        if "state_governments" in types:
            reason = "state government is eligible"
        elif "unrestricted" in types:
            reason = "state government is eligible (unrestricted)"
        elif "other" in types and re.search(r"\bstates?\b|state agenc|state government", elig_txt, re.I):
            reason = "eligible only as applicant type 'other' (text mentions states)"
        elif not types or types == ["other"]:
            reason = "applicant types not stated ('other'): check the NOFO"
            if len(elig_txt) > 60 and not re.search(r"see (the )?(nofo|notice|section)", elig_txt[:120], re.I):
                reason = "state government is not an eligible applicant (eligibility text names other applicants)"
        else:
            reason = "state government is not an eligible applicant"
        scope = f"{r.get('opportunity_title', '')} {elig_txt}"
        if OTHER_REGION.search(scope) and not NC_OK.search(scope):
            reason = "Geographically restricted: " + OTHER_REGION.search(scope).group(0)
        if re.search(r"\bstates?\b[^.]{0,60}(will not be considered|are not eligible|ineligible|not eligible)", elig_txt, re.I):
            reason = "state government is not an eligible applicant (eligibility text excludes states)"
        if str(r.get("opportunity_status")).lower() == "archived":
            reason = "Archived"
        close = r.get("close_date") if pd.notna(r.get("close_date")) else r.get("forecasted_close_date")
        rows.append({"opp": r.get("opportunity_number"), "title": norm(r.get("opportunity_title")),
                     "agency": norm(r.get("agency_name")), "deadline": pd.to_datetime(close, errors="coerce"),
                     "elig": reason, "elig_reason": reason,
                     "synopsis": _clean_html(r.get("summary_description"))[:SYNOPSIS_CHARS],
                     "cfda": str(r.get("opportunity_assistance_listings") or ""), "url": r.get("url"),
                     "state_only": types == ["state_governments"],
                     "n_awards": pd.to_numeric(r.get("expected_number_of_awards"), errors="coerce"),
                     "award_floor": pd.to_numeric(r.get("award_floor"), errors="coerce"),
                     "award_ceiling": pd.to_numeric(r.get("award_ceiling"), errors="coerce")})
    df = pd.DataFrame(rows)
    df["match_text"] = (df["title"] + ". " + df["synopsis"]).str.strip(". ")
    return df


# ---------------- Step 1: gates ----------------
def gates(r):
    reason = r["elig_reason"] or ""
    if "not an eligible applicant" in reason:
        return False, "G1 Applicant: state government is not an eligible applicant", False
    if "Geographically restricted" in reason:
        return False, "G2 Region: program is limited to another region", False
    if re.search(r"U\.S\. Mission to|Embassy|Bureau of Democracy", r["agency"]):
        return False, "G2 Region: program serves another country", False
    if "Archived" in reason:
        return False, "G3 Status: archived, not accepting applications", False
    forecast = pd.isna(r["deadline"])
    if not forecast and r["deadline"].date() < AS_OF_DATE:
        return False, f"G3 Status: deadline {r['deadline'].date()} has passed", False
    unclear = "'other'" in reason
    flags = []
    if unclear: flags.append("eligibility unclear: applicant type 'other'")
    yrs = [int(y) for y in re.findall(r"20[0-3]\d", r["title"] + " " + str(r["opp"]))]
    if forecast and yrs and max(yrs) <= AS_OF_DATE.year - 2:
        return False, f"G3 Status: stale forecast (newest year mentioned is {max(yrs)})", False
    if not forecast and r["deadline"].year > AS_OF_DATE.year + 5:
        flags.append(f"deadline {r['deadline'].date()} looks wrong: check the listing")
    return True, "Pass" + (f" ({'; '.join(flags)})" if flags else ""), forecast


# ---------------- Step 2: plan match A ----------------
def research_penalty(agency, title):
    if "National Institutes of Health" in agency:
        return RESEARCH_PENALTY["nih"], "NIH research x0.4"
    if re.search(r"National Science Foundation|NASA|Aeronautics|\bBAA\b|Broad Agency Announcement|research announcement|"
                 r"\((?:R|U|K|P|T|F|UG|UH)\d{1,2}\b|Research Program|Cancer|Clinical", agency + " " + title, re.I):
        return RESEARCH_PENALTY["other"], "research x0.75"
    return 1.0, ""


def plan_match(df):
    codes = list(OBJECTIVES)
    obj_docs = [OBJECTIVES[c][2] + " " + " ".join(OBJECTIVES[c][3]) for c in codes]
    vec = TfidfVectorizer(ngram_range=(1, 2), stop_words=STOPWORDS, sublinear_tf=True)
    vec.fit(obj_docs + df["match_text"].tolist())
    cos = cosine_similarity(vec.transform(df["match_text"]), vec.transform(obj_docs))   # grants x 25

    # keyword score K: continuous, not 0/50/100.
    #   each phrase is weighted by how rare it is across all titles (IDF);
    #   an exact phrase counts 1.0, a partial match counts the share of its words found x 0.5;
    #   per objective, K_raw = sum(weight x match strength); K = 100 x (1 - exp(-K_raw / tau)),
    #   tau = median K_raw of DNCR-type funders' listings (so a typical DNCR listing scores ~63).
    low = df["title"].str.lower()                 # title hits count double
    low_syn = df["synopsis"].str.lower()
    toks = (low + " " + low_syn).str.findall(r"[a-z0-9]+").apply(lambda ws: {w[:6] for w in ws})
    n_docs = len(df)
    def phrase_df(ph):
        return int((low + " " + low_syn).str.contains(re.escape(ph), regex=True).sum())
    kraw = np.zeros_like(cos)
    hits = [[[] for _ in codes] for _ in range(len(df))]
    for j, c in enumerate(codes):
        for ph in OBJECTIVES[c][3]:
            w_idf = np.log((n_docs + 1) / (phrase_df(ph) + 1)) + 1
            ph_toks = {w[:6] for w in re.findall(r"[a-z0-9]+", ph)}
            in_title = low.str.contains(re.escape(ph), regex=True).values
            in_syn = low_syn.str.contains(re.escape(ph), regex=True).values
            exact = in_title | in_syn
            share = toks.apply(lambda s: len(s & ph_toks) / len(ph_toks)).values
            strength = np.where(in_title, 2.0, np.where(in_syn, 1.0, np.where(share >= 0.5, 0.5 * share, 0.0)))
            kraw[:, j] += w_idf * strength
            for i in np.where(strength > 0)[0]:
                hits[i][j].append(ph.strip() + ("" if exact[i] else "~"))

    # calibrate L, H: L = median best-cos of NIH listings; H = 90th percentile of DNCR-type funders' listings
    best_cos = cos.max(1)
    nih = df["agency"].str.contains("National Institutes of Health").values
    ref = df["agency"].str.contains("National Park Service|Endowment for the Arts|Endowment for the Humanities|"
                                    "Museum and Library|Forest Service|Fish and Wildlife").values
    L = float(np.median(best_cos[nih])) if nih.sum() >= 20 else 0.20
    H = float(np.percentile(best_cos[ref], S_TOP_PCT)) if ref.sum() >= 5 else 0.52
    ref_k = kraw.max(1)[ref]
    tau = float(np.median(ref_k[ref_k > 0])) if (ref_k > 0).sum() >= 5 else 3.0
    S = np.clip((cos - L) / max(H - L, 1e-6), 0, 1) * 100
    K = 100 * (1 - np.exp(-kraw / tau))
    neg = np.array([sum(w in t for w in NEGATIVE_WORDS) for t in (low + " " + low_syn)])
    K = K * (0.5 ** neg[:, None])          # each negative word halves K
    A_obj = W_S * S + (1 - W_S) * K

    out = []
    for i, r in df.reset_index(drop=True).iterrows():
        order = np.argsort(-A_obj[i])
        j, j2 = order[0], order[1]
        pen, pen_txt = research_penalty(r["agency"], r["title"])
        out.append(dict(obj=codes[j], obj2=codes[j2], S=round(S[i, j], 1), K=round(K[i, j], 1),
                        A=round(max(A_obj[i, j] * pen, ENTITY_FLOOR if ENTITY.search(f"{r['title']}. {r['synopsis']}") else 0), 1),
                        entity=bool(ENTITY.search(f"{r['title']}. {r['synopsis']}")), penalty=pen_txt, phrases=", ".join(hits[i][j]),
                        neg_words=int(neg[i])))
    return pd.DataFrame(out), dict(L=round(L, 3), H=round(H, 3), tau=round(tau, 2))


# ---------------- Factor U: urgency ----------------
def urgency(obj, title):
    page, u, _, _ = OBJECTIVES[obj]
    if obj == "3.2" and AS_OF_DATE > date(2026, 12, 31):
        u = 1
    evidence = f"Objective {obj} urgency tier (plan p. {page})"
    words = [w for w in ONE_TIME_WORDS if w in title.lower()]
    if words and u < 2:
        u, evidence = 2, f"one-time window: title says '{words[0]}'"
    return u, evidence


# ---------------- Factor W: workload (simulated hours only) ----------------
# 11 stages, each (min, likely, max) hands-on hours for the one grants person
STAGES = ["discover", "read NOFO", "go/no-go", "registration", "partners & match", "narrative",
          "budget", "documents", "OSBM notice", "review & submit", "follow-up"]
BASE = {  # standard competitive application (anchor: NEH collections care)
    "discover": (0.5, 1, 2), "read NOFO": (2, 3, 5), "go/no-go": (1, 2, 4), "registration": (0.5, 1, 2),
    "partners & match": (2, 4, 8), "narrative": (20, 30, 45), "budget": (6, 10, 16), "documents": (6, 10, 16),
    "OSBM notice": (1, 2, 3), "review & submit": (4, 6, 10), "follow-up": (2, 4, 8),
}
ANCHORS = {
    "standard": {},
    "simple": {"narrative": (6, 10, 15), "budget": (3, 5, 8), "documents": (2, 4, 6),
               "partners & match": (0, 1, 2), "go/no-go": (0.5, 1, 2)},
    "complex": {"partners & match": (8, 15, 25), "narrative": (30, 45, 70), "budget": (15, 25, 40),
                "documents": (20, 35, 60), "go/no-go": (2, 4, 6), "review & submit": (6, 10, 16)},
    "pass-through": {"partners & match": (10, 20, 30), "documents": (10, 20, 30)},
    "disaster": {"narrative": (10, 15, 25), "go/no-go": (0.5, 1, 2), "partners & match": (1, 2, 4)},
}


def pick_anchor(title):
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


_SIM_CACHE = {}
def simulate_hours(anchor):
    """10,000-run simulation of total hands-on hours (sum of 11 triangular stages)."""
    if anchor not in _SIM_CACHE:
        st = {**BASE, **ANCHORS[anchor]}
        tri = lambda a: RNG.triangular(a[0], a[1], a[2] if a[2] > a[0] else a[0] + 1e-9, N_SIMS)
        hours = np.sum([tri(st[s]) for s in STAGES], axis=0)
        _SIM_CACHE[anchor] = dict(H=float(np.median(hours)), H10=float(np.percentile(hours, 10)),
                                  H90=float(np.percentile(hours, 90)))
    return _SIM_CACHE[anchor]


def workload_score(h):
    return float(np.clip((H_HARD - h) / (H_HARD - H_EASY), 0, 1) * 100)


# ---------------- Final score, route and button ----------------
def win_probability(r):
    """P = chance DNCR wins if it applies. Returns (P, case, source)."""
    text = f"{r['title']}. {r.get('synopsis', '')}"
    for hit in FORMULA_WORDS.finditer(text):      # skip "in addition to ... formula grants" and the like
        if re.search(r"in addition to|separate from|unlike|rather than|not (a |part of )?", text[max(0, hit.start() - 80):hit.start()], re.I):
            continue
        return 1.0, "1 formula", f"formula / every-state wording: '{hit.group(0)[:50]}'"
    n = r.get("n_awards")
    n = None if n is None or pd.isna(n) or n <= 0 or n >= 9999 else float(n)
    if r.get("state_only") is True:
        if n:
            return min(1.0, n / N_STATES), "2 states only", f"{n:.0f} expected awards / {N_STATES} states (listing)"
        return P_STATE_UNKNOWN, "2 states only", "no award count in listing: estimate"
    for pat, rate, src in PUBLISHED_RATE:
        if re.search(pat, r["title"], re.I):
            return rate, "3 open", src
    return P_DEFAULT, "3 open", "no published success rate: estimate"


def chance_nudge(P, case):
    """M = 1 + BOOST for formula / every-state programs, else 1 (the state-only nudge was not validated, so it is dropped)."""
    if case.startswith("1"):
        return 1 + BOOST
    return 1.0


def typical_award(r):
    fl, ce = r.get("award_floor"), r.get("award_ceiling")
    ce = ce if ce is not None and pd.notna(ce) and ce > 0 else None
    fl = fl if fl is not None and pd.notna(fl) and ce and fl >= 0.01 * ce else None
    if ce and fl: return float(np.sqrt(fl * ce)), "listing floor-ceiling midpoint"
    if ce: return ce / 3, "listing ceiling / 3"
    return None, "no award amount in listing"


def value_score(award, median_award):
    a = min(AWARD_HI, award)
    return float(np.clip((np.log(a) - np.log(AWARD_LO)) / (np.log(AWARD_HI) - np.log(AWARD_LO)), 0, 1))


def final_score(A, P, V, U, W):
    w = WEIGHTS
    return A * (w["b"] + w["P"] * P + w["V"] * V + w["U"] * U / 3 + w["W"] * W / 100)


def route(agency, title, obj):
    if re.search(PASS_THROUGH, title, re.I):
        return "DNCR leads (pass-through to localities)", True
    for ag, ti, owner in ROUTE_RULES:
        if re.search(ag, agency, re.I) and (ti is None or re.search(ti, title, re.I)):
            return owner, False
    div = DIVISION.get(obj, DIVISION.get(obj.split(".")[0], "DNCR"))
    return f"DNCR - {div}", True


def decide(r):
    if not r["gate_pass"]:
        return "Skip", r["gate_reason"]
    if r["A"] < A_SKIP:
        return "Skip", f"plan match A = {r['A']:.0f} is below the Skip line {A_SKIP}"
    if not r["dncr"]:
        return "Route", f"{r['route']} should apply, not DNCR"
    if r["forecast"]:
        return "Waitlist", "no deadline posted (forecast or rolling): watch in Early Warning"
    if r["final"] >= FINAL_CHASE:
        return "Chase", f"Final score {r['final']:.0f} is at or above the Chase line {FINAL_CHASE}"
    return "You decide", f"fits DNCR, but Final score {r['final']:.0f} is below the Chase line {FINAL_CHASE}"


def flags(r):
    f = []
    if "(" in r["gate_reason"]: f.append(r["gate_reason"][r["gate_reason"].index("(") + 1:-1])
    if r.get("rounds", 1) > 1: f.append(f"{int(r['rounds'])} rounds merged; latest deadline shown")
    if r["forecast"]: f.append("forecast/rolling: no deadline")
    return "; ".join(f)


def rationale(r):
    w = WEIGHTS
    parts = [f"Final {r['final']:.0f} = plan match {r['A']:.0f} x ({w['b']} + {w['P']} x chance {r['P']:.2f} + {w['V']} x value {r['V']:.2f} "
             f"+ {w['U']} x urgency {r['U']}/3 + {w['W']} x workload {r['W']:.0f}/100). Chance: case {r['P_case']} ({r['P_source']}). "
             f"Value: {'$' + format(round(r['award']), ',') if r['award'] and pd.notna(r['award']) else 'median award used'} ({r['award_src']})."]
    parts.append(f"Plan match: Objective {r['obj']} ({OBJECTIVES[r['obj']][2][:70]}...), plan p. {OBJECTIVES[r['obj']][0]}; "
                 f"meaning {r['S']:.0f}, keywords {r['K']:.0f}"
                 + (f" (matched '{r['phrases']}')" if r["phrases"] else "")
                 + (f"; {r['penalty']}" if r["penalty"] else "") + ".")
    parts.append(f"Urgency U = {r['U']} ({r['U_evidence']}).")
    parts.append(f"Workload: about {r['H']:.0f} hours ({r['H10']:.0f}-{r['H90']:.0f}), {r['anchor']} profile "
                 f"({r['anchor_why']}).")
    parts.append(f"Who applies: {r['route']}.")
    if r.get("rounds", 1) > 1:
        parts.append(f"{int(r['rounds'])} rounds of this program were merged into one row.")
    if "stale" in r["gate_reason"]:
        parts.append("Check first: the notice looks out of date.")
    parts.append(f"Button: {r['button']} because {r['button_why']}.")
    return " ".join(parts)


def run(inp, out, live=False):
    if live:
        df = load_live()
    elif str(inp).lower().endswith(".csv"):
        df = load_csv(inp)
    else:
        df = load(inp)
    g = df.apply(gates, axis=1, result_type="expand")
    df["gate_pass"], df["gate_reason"], df["forecast"] = g[0], g[1], g[2]
    pm, cal = plan_match(df)
    df = pd.concat([df.reset_index(drop=True), pm], axis=1)

    rows = []
    for _, r in df.iterrows():
        r = r.to_dict()
        r["U"], r["U_evidence"] = urgency(r["obj"], r["title"])
        r["route"], r["dncr"] = route(r["agency"], r["title"], r["obj"])
        r["anchor"], r["anchor_why"] = pick_anchor(r["title"])
        r.update(simulate_hours(r["anchor"]))
        r["W"] = round(workload_score(r["H"]), 1)
        r["P"], r["P_case"], r["P_source"] = win_probability(r)
        r["award"], r["award_src"] = typical_award(r)
        rows.append(r)
    df = pd.DataFrame(rows)
    df["V"] = [round(value_score(a, None), 3) if a and pd.notna(a) else V_UNKNOWN for a in df["award"]]
    df["final"] = [round(final_score(a, p, v, u, w), 1) for a, p, v, u, w in zip(df.A, df.P, df.V, df.U, df.W)]
    b = df.apply(decide, axis=1, result_type="expand")
    df["button"], df["button_why"] = b[0], b[1]

    eligible = df[df["gate_pass"]].copy()
    eligible["dedup_key"] = eligible["title"].str.lower().str.replace(r"fiscal year|\bfy\s?\d{2,4}|20\d\d|[^a-z ]", " ", regex=True)\
        .str.split().str.join(" ")
    eligible["rounds"] = eligible.groupby("dedup_key")["opp"].transform("count")
    eligible = eligible.sort_values("deadline", ascending=False).drop_duplicates("dedup_key")
    top = eligible.sort_values(["final", "A"], ascending=False).head(TOP_N).copy()
    top["rationale"] = top.apply(rationale, axis=1)
    top["flags"] = top.apply(flags, axis=1)
    top.insert(0, "rank", range(1, len(top) + 1))
    write_excel(top, df, cal, out)
    return top, df, cal


def write_excel(top, df, cal, out):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    cols = [("rank", "Rank", 6), ("opp", "Opportunity #", 18), ("title", "Title", 48), ("agency", "Agency", 26),
            ("deadline", "Deadline", 11), ("final", "Final score", 9), ("A", "Plan match A", 10),
            ("P", "Chance P", 8), ("V", "Value V", 8), ("award", "Typical award $", 12), ("P_case", "Chance case", 12), ("P_source", "Chance source", 34),
            ("S", "Meaning S", 9), ("K", "Keywords K", 9), ("obj", "Objective", 9), ("obj2", "2nd objective", 9),
            ("U", "Urgency U (0-3)", 9), ("W", "Workload score W", 10), ("H", "Hours H", 8),
            ("route", "Who applies", 30), ("button", "Suggested button", 12), ("flags", "Flags", 30),
            ("rationale", "Rationale", 110), ("cfda", "CFDA", 20), ("url", "Link", 40)]
    wb = Workbook()
    ws = wb.active; ws.title = "Top 50"
    ws["A1"] = f"DNCR - Top {len(top)} grants by Final score (as of {AS_OF_DATE})"; ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = ("Only grants that pass all three gates (state eligible, open to NC, still open) are ranked. "
                "Sorted by Final = A x (0.30 + 0.20 chance + 0.30 value + 0.15 urgency + 0.05 workload); factors fixed by client priorities, weights chosen by testing. The button is a suggestion; a person decides.")
    hdr = 4
    fill = PatternFill("solid", fgColor="1F3864")
    btn_fill = {"Chase": "C6EFCE", "You decide": "FFF2CC", "Waitlist": "FCE4D6", "Route": "DDEBF7", "Skip": "EDEDED"}
    for j, (_, h, w) in enumerate(cols, 1):
        c = ws.cell(hdr, j, h); c.font = Font(bold=True, color="FFFFFF"); c.fill = fill
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[get_column_letter(j)].width = w
    for i, (_, r) in enumerate(top.iterrows(), hdr + 1):
        for j, (k, _, _) in enumerate(cols, 1):
            v = r.get(k, "")
            if isinstance(v, float) and pd.isna(v) and k in ("cfda", "url"): v = ""
            if k == "deadline": v = "forecast/rolling" if pd.isna(v) else v.date()
            elif k == "H": v = None if pd.isna(v) else round(v)
            c = ws.cell(i, j, v); c.alignment = Alignment(wrap_text=True, vertical="top")
            if k == "button": c.fill = PatternFill("solid", fgColor=btn_fill.get(v, "FFFFFF"))
    ws.freeze_panes = ws.cell(hdr + 1, 4)
    ws.auto_filter.ref = f"A{hdr}:{get_column_letter(len(cols))}{hdr + len(top)}"

    # How it was scored
    ws2 = wb.create_sheet("How scored")
    lines = [
        ("Step", "Rule used"),
        ("1 Can we apply?", "G1 state eligible (applicant type 'other' with state text = pass, flagged); G2 not limited to another region; "
                            f"G3 deadline on/after {AS_OF_DATE} or no deadline (forecast/rolling). Pass/fail only, not scored."),
        ("2a Plan match A", f"A = max({W_S} S + {1-W_S:.2f} K) x research penalty, floored at {ENTITY_FLOOR} when the listing names an agency type DNCR is (state arts agency, SHPO, state library, SHRAB, state archives, state museum, state parks, natural heritage program). S = TF-IDF similarity of the title + first {SYNOPSIS_CHARS} characters of the "
                            f"synopsis (when the input has one) to each of DNCR's 25 objectives, scaled with L = {cal['L']} (median NIH) and "
                            f"H = {cal['H']} ({S_TOP_PCT}th percentile of NPS/NEA/NEH/IMLS/USFS/FWS listings). Phrase hits in the title count double. "
                            f"K = continuous keyword score: rarer plan phrases weigh more (IDF), exact phrase = 1, partial = share of words x 0.5, "
                            f"K = 100 x (1 - exp(-K_raw / {cal['tau']})); each negative word halves K. NIH x0.4; NSF/NASA/BAA x0.75."),
        ("2b Chance P", "P = 1 formula / every-state program; expected awards / 56 if only states may apply; published success rate; else 0.25 (estimate)."),
        ("2c Value V", f"V = log of the typical award (floor-ceiling midpoint, or ceiling/3), {AWARD_LO:,.0f} = 0, {AWARD_HI:,.0f} = 1; missing = median listing."),
        ("2d Urgency U / Workload W", "U = plan urgency tier 0-3 of the matched objective. W = 100 x (150 - simulated hours)/(150 - 20), clipped 0-100."),
        ("3 Final score", f"Final = A x ({WEIGHTS['b']} + {WEIGHTS['P']} P + {WEIGHTS['V']} V + {WEIGHTS['U']} U/3 + {WEIGHTS['W']} W/100). "
                          "Factors are fixed by what the client cares about; weights chosen by testing: budget simulation (objective), "
                          "past-wins blind test (guardrail), one-standard-error rule toward equal weights; human annotation pending."),
        ("4 Which button?", f"Skip: gate fails or A < {A_SKIP}. Route: another agency should apply. Waitlist: forecast or rolling. "
                            f"Chase: Final >= {FINAL_CHASE}. Otherwise You decide."),
        ("Data limits", "Matching uses the synopsis only if the input has a synopsis/description column; otherwise titles only. "
                        "No CFDA in the input, so route uses agency rules instead of CFDA."),
    ]
    for i, (a, b) in enumerate(lines, 1):
        ws2.cell(i, 1, a).font = Font(bold=True)
        ws2.cell(i, 2, b).alignment = Alignment(wrap_text=True, vertical="top")
    ws2.column_dimensions["A"].width = 20; ws2.column_dimensions["B"].width = 140

    # Summary counts
    ws3 = wb.create_sheet("Funnel")
    funnel = [("All listings", len(df)), ("Pass all gates", int(df["gate_pass"].sum())),
              (f"Pass gates and A >= {A_SKIP}", int((df["gate_pass"] & (df["A"] >= A_SKIP)).sum())),
              (f"... and Final >= {FINAL_CHASE}", int((df["gate_pass"] & (df["A"] >= A_SKIP) & (df["final"] >= FINAL_CHASE)).sum()))]
    funnel += [(f"Button: {k}", int(v)) for k, v in df["button"].value_counts().items()]
    for i, (a, b) in enumerate([("Stage", "Count")] + funnel, 1):
        ws3.cell(i, 1, a); ws3.cell(i, 2, b)
    ws3["A1"].font = ws3["B1"].font = Font(bold=True); ws3.column_dimensions["A"].width = 32

    # Objectives reference
    ws4 = wb.create_sheet("DNCR objectives")
    for j, h in enumerate(["Objective", "Plan page", "Urgency U", "Objective (summary)", "Phrases used"], 1):
        ws4.cell(1, j, h).font = Font(bold=True)
    for i, (code, (pg, u, txt, ph)) in enumerate(OBJECTIVES.items(), 2):
        for j, v in enumerate([code, pg, u, txt, ", ".join(ph)], 1):
            ws4.cell(i, j, v).alignment = Alignment(wrap_text=True, vertical="top")
    for col, w in zip("ABCDE", (10, 10, 10, 70, 60)):
        ws4.column_dimensions[col].width = w
    wb.save(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="pull the live Grants.gov feed instead of a file")
    ap.add_argument("--input", default="data/grants_source.xlsx")
    ap.add_argument("--out", default="DNCR_Top50_Grants.xlsx")
    a = ap.parse_args()
    top, df, cal = run(a.input, a.out, live=a.live)
    print("calibration:", cal)
    print(df["button"].value_counts().to_string())
    print(top[["rank", "opp", "title", "agency", "final", "A", "K", "U", "H", "button"]].head(50).to_string())
