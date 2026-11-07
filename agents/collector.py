"""Collector — pulls Grants.gov opportunities and normalizes them.  Owner: lead
Spec: docs/agents/collector.md

Live mode pulls EVERY posted / forecasted listing a state can apply for (applicant types
00 State governments | 25 Others | 99 Unrestricted), paged 250 at a time, then fetches each
record's details in parallel with retries. Details are cached (default 20 h) so a rerun the same
day is fast. Also records a keyword-search baseline for the "missed by keyword search" metric.
Fixture mode reads tests/fixtures/grants_sample_raw.json (offline).
Counts go to runs/collector_latest.json; raw records to raw/{date}/grants.json.
"""
from __future__ import annotations

import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from core import config, storage
from core.schemas import ApplicantType, Opportunity

SEARCH_URL = "https://api.grants.gov/v1/api/search2"
FETCH_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
FIXTURE_FILE = config.FIXTURES_DIR / "grants_sample_raw.json"
DEFAULT_KEYWORDS = ["historic preservation", "outdoor recreation", "museum", "library", "arts", "trails", "conservation", "disaster recovery"]


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
STATE_ELIGIBLE = "00|25|99"   # Grants.gov applicant types: State governments | Others (see text) | Unrestricted
PAGE_ROWS = 250
CAP = int(os.getenv("COLLECTOR_CAP", "5000"))            # safety cap on listings per run
WORKERS = int(os.getenv("COLLECTOR_WORKERS", "8"))       # parallel detail fetches (be polite to the API)
CACHE_HOURS = float(os.getenv("COLLECTOR_CACHE_HOURS", "20"))  # reuse details fetched within this window
CACHE_DIR = "cache/grants"


class GrantsGovError(RuntimeError):
    pass


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 500, 502, 503, 504)


@retry(retry=retry_if_exception(_is_transient), wait=wait_exponential(multiplier=1.5, min=1, max=20),
       stop=stop_after_attempt(4), reraise=True)
def _post(url: str, payload: dict) -> dict:
    r = httpx.post(url, json=payload, timeout=40)
    r.raise_for_status()
    body = r.json()
    if body.get("errorcode", 0) != 0:
        raise GrantsGovError(f"{url}: errorcode {body.get('errorcode')} {body.get('msg')}")
    return body.get("data") or {}


def search_page(keyword: str = "", start: int = 0, rows: int = PAGE_ROWS, eligibilities: str = "") -> dict:
    payload = {"keyword": keyword, "oppStatuses": "forecasted|posted", "rows": rows, "startRecordNum": start}
    if eligibilities:
        payload["eligibilities"] = eligibilities
    return _post(SEARCH_URL, payload)


def search(keyword: str = "", rows: int = 100, start: int = 0) -> list[dict]:
    """One page of hits (kept for scripts/fetch_fixture.py)."""
    return search_page(keyword, start, rows).get("oppHits") or []


def search_all(keyword: str = "", eligibilities: str = STATE_ELIGIBLE, cap: int = CAP) -> tuple[list[dict], int]:
    """Page through every posted/forecasted listing matching the filters. Returns (hits, hitCount)."""
    hits: list[dict] = []
    start, total = 0, None
    while start < cap:
        data = search_page(keyword, start, eligibilities=eligibilities)
        page = data.get("oppHits") or []
        total = int(data.get("hitCount") or 0) if total is None else total
        hits += page
        start += len(page)
        if not page or start >= total:
            break
    unique = list({h["id"]: h for h in hits}.values())  # pages can overlap if the feed changes mid-run
    return unique, total or 0


def fetch_detail(opp_id: str) -> dict:
    return _post(FETCH_URL, {"opportunityId": int(opp_id)})


def _cached_detail(opp_id: str) -> dict | None:
    entry = storage.read_json(f"{CACHE_DIR}/{opp_id}.json", default=None)
    if not entry:
        return None
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(entry["fetched_at"])).total_seconds() / 3600
    return entry["detail"] if age_h <= CACHE_HOURS else None


def fetch_details(ids: list[str], workers: int = WORKERS) -> tuple[list[dict], dict[str, int]]:
    """Details for every id: cache first, then parallel fetches. One bad record never stops the run."""
    stats = {"cached": 0, "fetched": 0, "failed": 0}
    details: dict[str, dict] = {}
    todo = []
    for opp_id in ids:
        cached = _cached_detail(opp_id)
        if cached is not None:
            details[opp_id] = cached
            stats["cached"] += 1
        else:
            todo.append(opp_id)

    def one(opp_id: str):
        try:
            return opp_id, fetch_detail(opp_id), None
        except Exception as exc:  # noqa: BLE001
            return opp_id, None, exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for opp_id, detail, exc in pool.map(one, todo):
            if detail:
                details[opp_id] = detail
                stats["fetched"] += 1
                storage.write_json(f"{CACHE_DIR}/{opp_id}.json",
                                   {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "detail": detail})
            else:
                stats["failed"] += 1
                print(f"[collector] detail failed for {opp_id}: {exc}")
    return [details[i] for i in ids if i in details], stats


def keyword_baseline(keywords: list[str] = DEFAULT_KEYWORDS, rows: int = 100) -> dict[str, list[str]]:
    """What a person typing plan keywords into Grants.gov would find (no eligibility filter).
    Used for the 'found by us, missed by keyword search' metric."""
    found: dict[str, list[str]] = {}
    for kw in keywords:
        try:
            found[kw] = [h["id"] for h in search_page(kw, rows=rows).get("oppHits") or []]
        except Exception as exc:  # noqa: BLE001
            print(f"[collector] keyword baseline '{kw}' failed: {exc}")
    return found


def collect_live() -> tuple[list[dict], dict[str, Any]]:
    hits, total = search_all()
    ids = [str(h["id"]) for h in hits]
    details, stats = fetch_details(ids)
    stats.update(searched=total, listed=len(ids))
    if not details:
        raise GrantsGovError("collected zero records — check network access to api.grants.gov")
    return details, stats


# --------------------------------------------------------------------------- #
# Normalizing
# --------------------------------------------------------------------------- #
def _strip_html(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _iso(date_str: str | None) -> str | None:
    """Grants.gov '...Str' fields look like 2030-11-01-00-00-00."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def normalize(detail: dict[str, Any], today: date | None = None) -> Opportunity:
    today = today or date.today()
    is_forecast = detail.get("docType") == "forecast" or "forecast" in detail and "synopsis" not in detail
    body = detail.get("forecast" if is_forecast else "synopsis") or {}

    close = _iso(body.get("estApplicationResponseDateStr" if is_forecast else "responseDateStr"))
    days = (date.fromisoformat(close) - today).days if close else None
    top = (detail.get("topAgencyDetails") or {}).get("agencyCode", "")

    opp = Opportunity(
        id=str(detail["id"]),
        number=detail.get("opportunityNumber", ""),
        title=html.unescape(detail.get("opportunityTitle", "")),
        agency_code=detail.get("owningAgencyCode", ""),
        agency_name=(detail.get("agencyDetails") or {}).get("agencyName", ""),
        top_agency_code=top,
        status="forecasted" if is_forecast else "posted",
        open_date=_iso(body.get("postingDateStr")),
        close_date=close,
        days_to_close=days,
        applicant_types=[ApplicantType(id=a["id"], description=a["description"]) for a in body.get("applicantTypes", [])],
        eligibility_text=_strip_html(body.get("applicantEligibilityDesc")),
        funding_instruments=[f.get("description", "") for f in body.get("fundingInstruments", [])],
        categories=[c.get("description", "") for c in body.get("fundingActivityCategories", [])],
        award_floor=_num(body.get("awardFloor")),
        award_ceiling=_num(body.get("awardCeiling")),
        cost_sharing=body.get("costSharing"),
        description=_strip_html(body.get("forecastDesc") if is_forecast else body.get("synopsisDesc")),
        cfda=[c.get("cfdaNumber", "") for c in detail.get("cfdas", [])],
        url=f"https://www.grants.gov/search-results-detail/{detail['id']}",
    )
    opp.low_quality = not (opp.title and opp.description)
    return opp


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "none") else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Agent entry point
# --------------------------------------------------------------------------- #
def run(state: dict) -> dict:
    started = datetime.now(timezone.utc)
    if config.COLLECTOR_MODE == "live":
        raw, stats = collect_live()
        today = date.today().isoformat()
        storage.write_json(f"raw/{today}/grants.json", raw)
        storage.write_json(f"baseline/{today}/keyword_hits.json", keyword_baseline())
    else:
        raw = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))
        stats = {"searched": len(raw), "listed": len(raw), "cached": 0, "fetched": 0, "failed": 0}

    opps: dict[str, Opportunity] = {}
    bad = 0
    for detail in raw:
        try:
            opp = normalize(detail)
            opps[opp.number or opp.id] = opp  # de-dup by opportunity number
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print(f"[collector] normalize failed for {detail.get('id')}: {exc}")

    result = [o.model_dump() for o in opps.values()]
    stats.update(normalized=len(result), normalize_failed=bad, low_quality=sum(o.low_quality for o in opps.values()),
                 seconds=round((datetime.now(timezone.utc) - started).total_seconds(), 1), mode=config.COLLECTOR_MODE)
    storage.write_json("opportunities/latest.json", result)
    storage.write_json("runs/collector_latest.json", stats)
    print(f"[collector] {stats}")
    return {"opportunities": result}
