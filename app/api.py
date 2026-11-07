"""Staff-facing web API (MODE=app). Serves index.html and the JSON the page needs.  Owner: lead
Contract for the front end: docs/api.md   ·   Run locally:  uvicorn app.api:app --reload --port 8000

The daily job (pipeline.py) writes the results to storage; this API only READS them, records the
user's decisions, and runs the two on-click agents (Route, Apply Assist). It never submits,
sends or deletes anything outside our own decisions log.

Pilot scope: no sign-in (one user). Roadmap: Entra ID sign-in, per-user decisions.
"""
from __future__ import annotations

import os
import threading
from datetime import date
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core import config, storage
from core.reference import load_profile
from core.schemas import Card, Decision, Opportunity, RouteDraft, StarterPack

SCORED_PATH = f"scored/{config.AGENCY}/latest.json"
DECISIONS_PATH = f"decisions/{config.AGENCY}.json"
INDEX_HTML = config.ROOT / "index.html"
_decisions_lock = threading.Lock()  # one replica: a process lock is enough for the pilot

app = FastAPI(title="Federal Funds Matchmaker — DNCR", version="0.1.0",
              description="Reads the daily pipeline output, records decisions, runs on-click agents.")

# CORS only matters when the page is opened from another origin during development (e.g. VS Code Live Server)
_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])


# --------------------------------------------------------------------------- #
# Request / response models (the front-end contract)
# --------------------------------------------------------------------------- #
class DecisionIn(BaseModel):
    opp_id: str
    decision: Literal["Chase", "Waitlist", "Skip", "Route"]
    note: str = Field(default="", max_length=2000)
    user: str = "dncr-grants"


class ObjectiveOut(BaseModel):
    id: Optional[str]
    text: Optional[str] = None
    page: Optional[int] = None


class VerificationOut(BaseModel):
    confidence: Optional[str] = None
    verified_quotes: int = 0
    total_quotes: int = 0
    warnings: list[str] = []


class ShortlistItem(BaseModel):
    opp_id: str
    title: str
    funder: str
    url: str
    status: str                       # posted | forecasted
    close_date: Optional[str]         # ISO date; None = rolling / not stated
    days_left: Optional[int]          # recomputed from today, never taken from a card
    award_ceiling: Optional[float]
    score: float
    confidence: str                   # after verification when a card exists
    suggested_decision: Optional[str]
    decision_reason: Optional[str]
    objective: ObjectiveOut
    division: Optional[str]
    route_to: Optional[str]
    already_yours: bool
    watch: bool
    flags: list[str]
    card: Optional[dict[str, Any]]    # core.schemas.Card, or None if no card was written
    verification: VerificationOut
    my_decision: Optional[dict[str, Any]]  # latest Decision for this item, or None


class ShortlistOut(BaseModel):
    last_refreshed: Optional[str]
    run_status: Optional[str]
    counts: dict[str, int]
    digest: Optional[dict[str, Any]]
    items: list[ShortlistItem]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _rows() -> list[dict]:
    return storage.read_json(SCORED_PATH, default=[])


def _row(opp_id: str) -> dict:
    row = next((r for r in _rows() if r["opp_id"] == opp_id), None)
    if row is None:
        raise HTTPException(404, f"opportunity {opp_id} is not in today's results")
    return row


def _decisions() -> list[dict]:
    return storage.read_json(DECISIONS_PATH, default=[])


def _latest_decisions() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for d in _decisions():
        latest[d["opp_id"]] = d  # file order = time order
    return latest


def _days_left(close_date: Optional[str]) -> Optional[int]:
    if not close_date:
        return None
    try:
        return (date.fromisoformat(close_date[:10]) - date.today()).days
    except ValueError:
        return None


def _objectives() -> dict[str, Any]:
    return {o.objective_id: o for o in load_profile().objectives}


def _to_item(row: dict, objectives: dict, decided: dict[str, dict]) -> ShortlistItem:
    opp = row["opportunity"]
    checked = row.get("checked") or {}
    ob = objectives.get(row.get("objective_id") or "")
    return ShortlistItem(
        opp_id=row["opp_id"],
        title=opp["title"],
        funder=opp.get("agency_name") or opp.get("agency_code", ""),
        url=opp.get("url", ""),
        status=opp.get("status", "posted"),
        close_date=opp.get("close_date"),
        days_left=_days_left(opp.get("close_date")),
        award_ceiling=opp.get("award_ceiling"),
        score=row["score"],
        confidence=checked.get("confidence") or row["confidence"],
        suggested_decision=row.get("suggested_decision"),
        decision_reason=row.get("decision_reason"),
        objective=ObjectiveOut(id=row.get("objective_id"), text=ob.text if ob else None, page=ob.page if ob else None),
        division=row.get("division"),
        route_to=row.get("route_to"),
        already_yours=row.get("already_yours", False),
        watch=row.get("watch", False),
        flags=row.get("flags", []),
        card=checked.get("card"),
        verification=VerificationOut(confidence=checked.get("confidence"), verified_quotes=checked.get("verified_quotes", 0),
                                     total_quotes=checked.get("total_quotes", 0), warnings=checked.get("warnings", [])),
        my_decision=decided.get(row["opp_id"]),
    )


def _card_for(row: dict) -> Card:
    checked = row.get("checked") or {}
    if checked.get("card"):
        return Card(**checked["card"])
    raise HTTPException(409, "No decision card was written for this item (it is not on today's short list)")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/", include_in_schema=False)
def index():
    if INDEX_HTML.exists():
        return FileResponse(INDEX_HTML)
    return JSONResponse({"message": "index.html not found; API docs at /docs"})


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "storage": config.STORAGE_MODE, "llm_enabled": config.LLM_ENABLED}


@app.get("/api/shortlist", response_model=ShortlistOut)
def shortlist(include_skipped: bool = Query(False, description="also return items the scorer suggests Skip"),
              include_low: bool = Query(False, description="also return Low-confidence items"),
              hide_decided_skip: bool = Query(True, description="hide items the user already Skipped"),
              limit: int = Query(15, ge=1, le=200)) -> ShortlistOut:
    """The morning short list, best first, plus last-refreshed time and alert digest."""
    run = storage.read_json("runs/latest.json", default={}) or {}
    objectives, decided = _objectives(), _latest_decisions()
    items = []
    for row in _rows():
        if not include_skipped and row.get("suggested_decision") == "Skip":
            continue
        item = _to_item(row, objectives, decided)
        if not include_low and item.confidence == "Low":
            continue
        if hide_decided_skip and (item.my_decision or {}).get("decision") == "Skip":
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return ShortlistOut(last_refreshed=run.get("finished_at"), run_status=run.get("status"), counts=run.get("counts", {}),
                        digest=storage.read_json("alerts/digest.json", default=None), items=items)


@app.get("/api/opportunities/{opp_id}")
def opportunity(opp_id: str) -> dict:
    """Everything about one item: listing, score breakdown and rationale, card, verification, decision history."""
    row = _row(opp_id)
    return {**row, "days_left": _days_left(row["opportunity"].get("close_date")),
            "decisions": [d for d in _decisions() if d["opp_id"] == opp_id]}


@app.get("/api/alerts")
def alerts() -> dict:
    return {"alerts": storage.read_json("alerts/latest.json", default=[]),
            "digest": storage.read_json("alerts/digest.json", default=None)}


@app.get("/api/run")
def run_status() -> dict:
    """Last pipeline run: time, status, counts (the funnel)."""
    return storage.read_json("runs/latest.json", default={}) or {}


@app.get("/api/decisions")
def list_decisions() -> list[dict]:
    return _decisions()


@app.post("/api/decisions", status_code=201)
def add_decision(body: DecisionIn) -> dict:
    """Record Chase / Waitlist / Skip / Route. Append-only log; the latest entry per item wins."""
    _row(body.opp_id)  # 404 if unknown
    d = Decision(opp_id=body.opp_id, decision=body.decision, note=body.note, user=body.user)
    with _decisions_lock:
        log = _decisions()
        log.append(d.model_dump())
        storage.write_json(DECISIONS_PATH, log)
    return d.model_dump()


@app.delete("/api/decisions/{opp_id}")
def undo_decision(opp_id: str) -> dict:
    """Undo: remove the most recent decision for this item (e.g. an accidental Skip)."""
    with _decisions_lock:
        log = _decisions()
        idx = max((i for i, d in enumerate(log) if d["opp_id"] == opp_id), default=None)
        if idx is None:
            raise HTTPException(404, "no decision to undo")
        removed = log.pop(idx)
        storage.write_json(DECISIONS_PATH, log)
    return {"undone": removed, "now": _latest_decisions().get(opp_id)}


@app.post("/api/opportunities/{opp_id}/route", response_model=RouteDraft)
def route_draft(opp_id: str) -> RouteDraft:
    """Route Agent: draft a forwarding note. Nothing is sent."""
    from agents import route_agent

    row = _row(opp_id)
    return route_agent.run(Opportunity(**row["opportunity"]), _card_for(row))


@app.post("/api/opportunities/{opp_id}/apply-assist", response_model=StarterPack)
def apply_assist(opp_id: str) -> StarterPack:
    """Apply Assist: starter pack after Chase. Scaffolding only; nothing is submitted."""
    from agents import apply_assist as aa

    row = _row(opp_id)
    return aa.run(Opportunity(**row["opportunity"]), _card_for(row))
