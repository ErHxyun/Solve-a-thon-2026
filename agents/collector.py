"""Collector — pulls Grants.gov opportunities and normalizes them.  Owner: P2
Spec: docs/agents/collector.md

Working baseline included (fixture mode + live mode + normalize). P2 extends:
paging caps, broad no-keyword pull, raw snapshots in Blob, de-dup, run log counts.
"""
from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from typing import Any

import httpx

from core import config, storage
from core.schemas import ApplicantType, Opportunity

SEARCH_URL = "https://api.grants.gov/v1/api/search2"
FETCH_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
FIXTURE_FILE = config.FIXTURES_DIR / "grants_sample_raw.json"
DEFAULT_KEYWORDS = ["historic preservation", "outdoor recreation", "museum", "library", "arts", "trails", "conservation", "disaster recovery"]


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def search(keyword: str = "", rows: int = 100, start: int = 0) -> list[dict]:
    body = {"keyword": keyword, "oppStatuses": "forecasted|posted", "rows": rows, "startRecordNum": start}
    r = httpx.post(SEARCH_URL, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["data"]["oppHits"]


def fetch_detail(opp_id: str) -> dict:
    r = httpx.post(FETCH_URL, json={"opportunityId": int(opp_id)}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def collect_live(keywords: list[str] = DEFAULT_KEYWORDS, rows: int = 50) -> list[dict]:
    ids: dict[str, None] = {}
    for kw in keywords:
        for hit in search(kw, rows=rows):
            ids[hit["id"]] = None
    details = []
    for opp_id in ids:
        try:
            details.append(fetch_detail(opp_id))
        except Exception as exc:  # one bad record must not crash the run
            print(f"[collector] skip {opp_id}: {exc}")
    return details


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
    if config.COLLECTOR_MODE == "live":
        raw = collect_live()
        storage.write_json(f"raw/{date.today().isoformat()}/grants.json", raw)
    else:
        raw = json.loads(FIXTURE_FILE.read_text(encoding="utf-8"))

    opps = {}
    for detail in raw:
        try:
            opp = normalize(detail)
            opps[opp.number or opp.id] = opp  # de-dup by opportunity number
        except Exception as exc:
            print(f"[collector] normalize failed for {detail.get('id')}: {exc}")

    result = [o.model_dump() for o in opps.values()]
    storage.write_json("opportunities/latest.json", result)
    return {"opportunities": result}
