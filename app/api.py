"""Staff-facing web API (MODE=app). Serves index.html and the JSON the page needs.  Owner: lead
Contract for the front end: docs/api.md   ·   Run locally:  uvicorn app.api:app --reload --port 8000

The daily job (pipeline.py) writes the results to storage; this API only READS them, records the
user's decisions, and runs the two on-click agents (Route, Apply Assist). It never submits,
sends or deletes anything outside our own decisions log.

Pilot scope: no sign-in (one user). Roadmap: Entra ID sign-in, per-user decisions.
"""
from __future__ import annotations

import os
import re
import threading
from datetime import date
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from agents.fit_scorer import win_probability
from core import config, storage
from core.reference import load_profile
from core.schemas import Card, Decision, Opportunity, RouteDraft, StarterPack

SCORED_PATH = f"scored/{config.AGENCY}/latest.json"
DECISIONS_PATH = f"decisions/{config.AGENCY}.json"
TRACKER_PATH = f"tracker/{config.AGENCY}.json"          # opp_id -> the user's application record
ON_DEMAND_PATH = f"cards/on_demand/{config.AGENCY}.json"  # opp_id -> checked card written on click
STAGES = ["saved", "reviewing", "preparing", "internal_approval", "submitted", "awarded", "declined", "closed"]
ACTIVE_STAGES = {"reviewing", "preparing", "internal_approval", "submitted"}
_tracker_lock = threading.Lock()
_card_lock = threading.Lock()
INDEX_HTML = config.ROOT / "index.html"
RADAR_TEMPLATE = config.ROOT / "radar" / "radar_template.html"
_decisions_lock = threading.Lock()  # one replica: a process lock is enough for the pilot

app = FastAPI(title="Federal Funds Matchmaker — DNCR", version="0.1.0",
              description="Reads the daily pipeline output, records decisions, runs on-click agents.")

# CORS only matters when the page is opened from another origin during development (e.g. VS Code Live Server)
_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])


# --------------------------------------------------------------------------- #
# Request / response models (the front-end contract)
# --------------------------------------------------------------------------- #
class DecisionIn(BaseModel):
    opp_id: str
    decision: Literal["Chase", "Waitlist", "Skip", "Route"]
    note: str = Field(default="", max_length=2000)
    user: str = "dncr-grants"


class TrackerIn(BaseModel):
    """Partial update of the user's record; only the fields sent are changed. Nothing is submitted anywhere."""
    stage: Optional[Literal["saved", "reviewing", "preparing", "internal_approval", "submitted", "awarded", "declined", "closed"]] = None
    owner: Optional[str] = Field(default=None, max_length=200)
    next_action: Optional[str] = Field(default=None, max_length=500)
    next_action_due: Optional[str] = Field(default=None, max_length=10)   # ISO date
    notes: Optional[str] = Field(default=None, max_length=5000)
    my_decision: Optional[Literal["pursue", "monitor", "decline", "undecided"]] = None
    eligibility: Optional[Literal["verified", "ineligible", "pending"]] = None   # the person's check, not the model's
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
    number: str = ""                       # Grants.gov opportunity number (for search)
    plan_match: Optional[float] = None     # Fit Scorer A, 0-100 (not the Final score)
    money_category: str = "Unknown"        # Formula | State-only competition | Open competition | Unknown
    tags: list[str] = []                   # e.g. One-time
    funding_mechanism: str = "Unknown"     # Formula | Competitive | Unknown
    competition_scope: str = "Unknown"     # State-only | Open to multiple applicant types | Unknown
    eligibility: str = "unknown"           # verified (a person checked) | pending | ineligible | unknown (no card)
    factors: dict[str, Any] = {}           # Fit Scorer components: A, P, V, U, W, H, H10, H90, award, P_case
    tracker: Optional[dict[str, Any]] = None   # the user's application record, if any
    card_on_demand: bool = False           # card was written on click (not in the morning top 15)


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
    rows = storage.read_json(SCORED_PATH, default=[])
    extra = storage.read_json(ON_DEMAND_PATH, default={}) or {}
    if extra:
        for r in rows:
            if not r.get("checked") and r["opp_id"] in extra:
                r["checked"], r["card_on_demand"] = extra[r["opp_id"]], True
    return rows


def _tracker() -> dict[str, dict]:
    return storage.read_json(TRACKER_PATH, default={}) or {}


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


ONE_TIME = re.compile(r"\b(disaster|supplemental|emergency|one-time|hurricane)\b", re.I)


def money_category(row: dict) -> str:
    """How the money is split, from the Fit Scorer's chance case (deterministic, no LLM).
    Each item gets exactly one: Formula | State-only competition | Open competition | Unknown."""
    case = _case(row)
    types = {a.get("id") for a in row["opportunity"].get("applicant_types") or []}
    if case.startswith("1"):
        return "Formula"
    if case.startswith("2"):
        return "State-only competition"
    if case.startswith("3") and types - {"00", "25"}:   # other applicant types besides states / "others"
        return "Open competition"
    return "Unknown"


def _case(row: dict) -> str:
    case = str((row.get("components") or {}).get("P_case") or "")
    if not case:
        try:
            case = win_probability(Opportunity(**row["opportunity"]))[1]
        except Exception:  # noqa: BLE001
            case = ""
    return case


def funding_mechanism(row: dict) -> str:
    case = _case(row)
    return "Formula" if case.startswith("1") else "Competitive" if case[:1] in ("2", "3") else "Unknown"


def competition_scope(row: dict) -> str:
    types = {a.get("id") for a in row["opportunity"].get("applicant_types") or []}
    if types == {"00"}:
        return "State-only"
    if types - {"00", "25"}:
        return "Open to multiple applicant types"
    return "Unknown"


def eligibility_state(row: dict, record: Optional[dict]) -> str:
    """verified only when a person marked it; the model can only say pending / ineligible."""
    if record and record.get("eligibility") in ("verified", "ineligible"):
        return record["eligibility"]
    card = (row.get("checked") or {}).get("card") or {}
    if not card:
        return "unknown"
    can = re.sub(r"^\[[^\]]*\]\s*", "", str(card.get("can_dncr_apply") or ""))
    return "ineligible" if re.match(r"\s*no\b", can, re.I) else "pending"


def _tags(row: dict) -> list[str]:
    opp = row["opportunity"]
    card = (row.get("checked") or {}).get("card") or {}
    one_time = ONE_TIME.search(opp.get("title", "")) or "one-time" in str(card.get("money_type", ""))
    return ["One-time"] if one_time else []


FACTOR_KEYS = ("A", "S", "K", "P", "V", "U", "W", "H", "H10", "H90", "award", "P_case", "penalty", "entity_floor")


def _to_item(row: dict, objectives: dict, decided: dict[str, dict], tracker: Optional[dict] = None) -> ShortlistItem:
    opp = row["opportunity"]
    checked = row.get("checked") or {}
    ob = objectives.get(row.get("objective_id") or "")
    record = (tracker or {}).get(row["opp_id"])
    comps = row.get("components") or {}
    return ShortlistItem(
        funding_mechanism=funding_mechanism(row),
        competition_scope=competition_scope(row),
        eligibility=eligibility_state(row, record),
        factors={k: comps[k] for k in FACTOR_KEYS if k in comps},
        tracker=record,
        card_on_demand=bool(row.get("card_on_demand")),
        number=opp.get("number", ""),
        plan_match=(row.get("components") or {}).get("A"),
        money_category=money_category(row),
        tags=_tags(row),
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
        return FileResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})  # always revalidate after a deploy
    return JSONResponse({"message": "index.html not found; API docs at /docs"})


RADAR_BAR = """<style>
.ffm-bar{background:#053131;color:#fff;font-family:'Source Sans 3',system-ui,sans-serif}
.ffm-bar .in{max-width:1320px;margin:0 auto;padding:10px 40px;display:flex;gap:10px 30px;align-items:center;flex-wrap:wrap}
.ffm-bar a{color:#fff;text-decoration:none;font-weight:700;font-size:16px;padding:8px 0;border-bottom:3px solid transparent}
.ffm-bar a.brand{font-size:18px;margin-right:6px}.ffm-bar a[aria-current]{border-bottom-color:#F57600}
.ffm-bar a:hover{border-bottom-color:#777}.ffm-bar .note{margin-left:auto;font:12px ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;color:#BDBDBD}
@media(max-width:640px){.ffm-bar .in{padding:8px 16px}.ffm-bar .note{margin-left:0}}
</style>
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;700&display=swap" rel="stylesheet">
<div class="ffm-bar"><nav class="in" aria-label="Main"><a class="brand" href="/#today">DNCR Federal Funds Matchmaker</a>
<a href="/#today">Today</a><a href="/#match">Find Funding</a><a href="/#board">My Tracker</a><a href="/radar" aria-current="page">Radar</a><a href="/#how">How It Works</a>
<span class="note">NC DNCR · Student prototype</span></nav></div>"""


def _radar_page_data(out: dict) -> str:
    """Template inserts titles with innerHTML: escape text fields; keep </script> from closing the data block."""
    import copy
    import html as _html
    import json as _json

    out = copy.deepcopy(out)
    for k in ("all", "eligible"):
        for a in (out.get(k) or {}).get("areas", []):
            for e in a.get("examples", []):
                e["title"], e["agency"] = _html.escape(e.get("title") or ""), _html.escape(e.get("agency") or "")
    return _json.dumps(out, separators=(",", ":")).replace("</", "<\\/")


@app.get("/radar", include_in_schema=False, response_class=HTMLResponse)
def radar_page() -> HTMLResponse:
    """Grant Topic Radar, filled with the latest pipeline data (radar/latest.json)."""
    out = storage.read_json("radar/latest.json", default=None)
    if not out or not RADAR_TEMPLATE.exists():
        body = ('<main style="max-width:720px;margin:60px auto;font-family:system-ui;padding:0 16px"><h1>Grant Topic Radar</h1>'
                '<p>The radar appears after the next daily pipeline run.</p><p><a href="/#today">Back to today</a></p></main>')
    else:
        body = RADAR_TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", _radar_page_data(out))
    page = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '</head>\n<body style="margin:0">\n' + RADAR_BAR + body + '\n</body>\n</html>\n')
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@app.get("/api/radar")
def radar_data() -> dict:
    """Where federal grant money is growing or fading, by policy area (built by the daily pipeline)."""
    out = storage.read_json("radar/latest.json", default=None)
    if not out:
        raise HTTPException(404, "no radar yet (it appears after the next pipeline run)")
    return out


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
    objectives, decided, tracker = _objectives(), _latest_decisions(), _tracker()
    items = []
    for row in _rows():
        if not include_skipped and row.get("suggested_decision") == "Skip":
            continue
        item = _to_item(row, objectives, decided, tracker)
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
            "decisions": [d for d in _decisions() if d["opp_id"] == opp_id], "tracker": _tracker().get(opp_id)}


@app.get("/api/alerts")
def alerts() -> dict:
    return {"alerts": storage.read_json("alerts/latest.json", default=[]),
            "digest": storage.read_json("alerts/digest.json", default=None)}


@app.get("/api/run")
def run_status() -> dict:
    """Last pipeline run: time, status, counts (the funnel)."""
    return storage.read_json("runs/latest.json", default={}) or {}


@app.get("/api/email/preview", response_class=HTMLResponse)
def email_preview(kind: Literal["weekly", "urgent"] = "weekly") -> HTMLResponse:
    """The email the notifier rendered on the last run (nothing is sent from here)."""
    preview = storage.read_json(f"notify/preview/{kind}.json", default=None)
    if not preview:
        raise HTTPException(404, f"no {kind} email preview yet (it appears after the next pipeline run)")
    return HTMLResponse(preview["html"])


@app.get("/api/email/status")
def email_status() -> dict:
    """What the notifier did on the last run: kind, status, recipients count (never addresses or keys)."""
    return storage.read_json("notify/latest.json", default={"status": "no run yet"})


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
    # Save starts a tracker record at "saved"; Start tracking moves it to "reviewing" (never backwards)
    if body.decision in ("Waitlist", "Chase"):
        current = (_tracker().get(body.opp_id) or {}).get("stage")
        if body.decision == "Waitlist" and current is None:
            _update_tracker(body.opp_id, TrackerIn(stage="saved", user=body.user), sync_decision=False)
        elif body.decision == "Chase" and current in (None, "saved"):
            _update_tracker(body.opp_id, TrackerIn(stage="reviewing", user=body.user), sync_decision=False)
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


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _update_tracker(opp_id: str, body: TrackerIn, sync_decision: bool = True) -> dict:
    changes = body.model_dump(exclude_none=True, exclude={"user"})
    with _tracker_lock:
        tracker = _tracker()
        rec = tracker.get(opp_id) or {"opp_id": opp_id, "stage": "saved", "created_at": _now(), "history": []}
        for field, value in changes.items():
            if rec.get(field) != value:
                rec["history"].append({"at": _now(), "user": body.user, "field": field, "from": rec.get(field), "to": value})
                rec[field] = value
        rec["updated_at"] = _now()
        tracker[opp_id] = rec
        storage.write_json(TRACKER_PATH, tracker)
    # keep the decision log (read by Alert / Notifier) in step with the stage
    if sync_decision and "stage" in changes:
        want = "Waitlist" if changes["stage"] == "saved" else "Chase" if changes["stage"] in ACTIVE_STAGES else None
        if want and (_latest_decisions().get(opp_id) or {}).get("decision") != want:
            with _decisions_lock:
                log = _decisions()
                log.append(Decision(opp_id=opp_id, decision=want, note=f"stage: {changes['stage']}", user=body.user).model_dump())
                storage.write_json(DECISIONS_PATH, log)
    return rec


@app.get("/api/tracker")
def list_tracker() -> list[dict]:
    """The user's application records (stage, owner, next action, notes, history)."""
    return list(_tracker().values())


@app.put("/api/tracker/{opp_id}")
def update_tracker(opp_id: str, body: TrackerIn) -> dict:
    """Change stage / owner / next action / notes / my decision / eligibility check. Every change is logged."""
    _row(opp_id)  # 404 if unknown
    if body.next_action_due:
        try:
            date.fromisoformat(body.next_action_due)
        except ValueError:
            raise HTTPException(422, "next_action_due must be YYYY-MM-DD")
    return _update_tracker(opp_id, body)


@app.post("/api/opportunities/{opp_id}/card")
def card_on_demand(opp_id: str) -> dict:
    """Write and verify a decision card for an item outside the morning top 15 (one LLM call, cached)."""
    from agents import card_agent, verifier
    from core.schemas import ScoredItem

    row = _row(opp_id)
    if row.get("checked"):
        return row["checked"]
    with _card_lock:
        extra = storage.read_json(ON_DEMAND_PATH, default={}) or {}
        if opp_id in extra:
            return extra[opp_id]
        opp, item, profile = Opportunity(**row["opportunity"]), ScoredItem(**row), load_profile()
        objective = _objectives().get(item.objective_id or "")
        text = objective.text if objective else ""
        card = card_agent.make_card(opp, item, profile)
        checked = verifier.check(card, opp, item, text, 1)
        if checked.needs_retry:   # same evaluator-optimizer loop as the batch: one retry with the Verifier's feedback
            card = card_agent.make_card(opp, item, profile, checked.verifier_feedback)
            checked = verifier.check(card, opp, item, text, 2)
        extra[opp_id] = checked.model_dump()
        storage.write_json(ON_DEMAND_PATH, extra)
    return extra[opp_id]


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
