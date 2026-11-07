"""Alert — deadline and watch-list alerts.  Owner: lead
Spec: docs/agents/alert.md

Four alert kinds (core.schemas.Alert):
- closing_soon         short-list or Chased item closes within CLOSING_SOON_DAYS
- new_since_yesterday  short-list item not seen in the last run of a *previous* day
- waitlist_opening     Waitlisted forecast is now posted (open for applications)
- waitlist_21_days     Waitlisted posted item is WAITLIST_WARN_DAYS or less from closing

Rules that keep alerts trustworthy and few:
- Days left are recomputed from close_date and the RUN date (Eastern time), never taken from a
  stale days_to_close or from a card. A rerun the next morning gives the right number.
- "Yesterday" means the last run on an earlier date, so running twice a day doesn't wipe it.
- The user's latest decision wins: Skipped items get no alerts; a Waitlisted item gets its
  waitlist alert instead of a duplicate closing_soon.
- First run: nothing is "new" (no baseline yet).
Also writes alerts/digest.json: the counts and lines a morning email would lead with.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from core import storage
from core.schemas import Alert, Opportunity

CLOSING_SOON_DAYS = int(os.getenv("ALERT_CLOSING_SOON_DAYS", "10"))
WAITLIST_WARN_DAYS = int(os.getenv("ALERT_WAITLIST_WARN_DAYS", "21"))
HISTORY_DAYS = 14                        # how many past run dates to keep
TZ = ZoneInfo("America/New_York")        # the daily job runs at 6:00 ET

HISTORY_PATH = "opportunities/seen_history.json"   # {run_date: {opp_id: status}}
LEGACY_IDS_PATH = "opportunities/previous_ids.json"
DECISIONS_PATH = "decisions/dncr.json"
ORDER = {"waitlist_opening": 0, "closing_soon": 1, "waitlist_21_days": 2, "new_since_yesterday": 3}


def today_et() -> date:
    return datetime.now(TZ).date()


def days_left(opp: Opportunity, today: date) -> Optional[int]:
    """Days until close, counted from the run date. None = rolling / not stated."""
    if opp.close_date:
        try:
            return (date.fromisoformat(opp.close_date[:10]) - today).days
        except ValueError:
            pass
    return opp.days_to_close


def latest_decisions(decisions: list[dict]) -> dict[str, str]:
    """opp_id -> the user's most recent decision (file order, then timestamp)."""
    latest: dict[str, tuple[str, str]] = {}
    for d in decisions:
        opp_id, at = d.get("opp_id"), d.get("at", "")
        if opp_id and (opp_id not in latest or at >= latest[opp_id][0]):
            latest[opp_id] = (at, d.get("decision", ""))
    return {k: v[1] for k, v in latest.items()}


def previous_run(history: dict[str, dict[str, str]], today: date) -> Optional[dict[str, str]]:
    """id -> status from the most recent run on an EARLIER date, if any."""
    earlier = sorted(d for d in history if d < today.isoformat())
    if earlier:
        return history[earlier[-1]]
    if history:
        return None  # only today's earlier run exists: still day one, nothing is "new"
    legacy = storage.read_json(LEGACY_IDS_PATH, default=None)  # baseline written before history existed
    return {i: "" for i in legacy} if legacy else None


def _when(n: int) -> str:
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


def build_alerts(opps: dict[str, Opportunity], top_ids: list[str], decided: dict[str, str],
                 previous: Optional[dict[str, str]], today: date) -> list[Alert]:
    alerts: list[Alert] = []
    waitlisted = {i for i, d in decided.items() if d == "Waitlist"}
    chased = {i for i, d in decided.items() if d == "Chase"}

    # Waitlist reminders (whether or not the item is still on today's short list)
    for opp_id in sorted(waitlisted & opps.keys()):
        opp = opps[opp_id]
        n = days_left(opp, today)
        was = (previous or {}).get(opp_id)
        if opp.status == "posted" and was == "forecasted":
            alerts.append(Alert(opp_id=opp_id, kind="waitlist_opening", days_to_close=n,
                                message="Waitlisted forecast is now open for applications"
                                        + (f"; closes {_when(n)} ({opp.close_date})" if n is not None and n >= 0 else "")))
        elif opp.status == "posted" and n is not None and 0 <= n <= WAITLIST_WARN_DAYS:
            alerts.append(Alert(opp_id=opp_id, kind="waitlist_21_days", days_to_close=n,
                                message=f"Waitlisted item closes {_when(n)} ({opp.close_date})"))

    # Deadlines: today's short list plus anything the user is chasing
    for opp_id in [i for i in dict.fromkeys(top_ids + sorted(chased)) if i in opps]:
        if decided.get(opp_id) in ("Skip", "Waitlist"):
            continue  # skipped = silenced; waitlisted handled above
        opp = opps[opp_id]
        n = days_left(opp, today)
        if opp.status == "posted" and n is not None and 0 <= n <= CLOSING_SOON_DAYS:
            who = "You're chasing this; it closes" if opp_id in chased else "Closes"
            alerts.append(Alert(opp_id=opp_id, kind="closing_soon", days_to_close=n,
                                message=f"{who} {_when(n)} ({opp.close_date})"))

    # New since the previous day's run (short list only; never on the first run)
    if previous is not None:
        for opp_id in top_ids:
            if opp_id in opps and opp_id not in previous and decided.get(opp_id) != "Skip":
                alerts.append(Alert(opp_id=opp_id, kind="new_since_yesterday", message="New since yesterday"))

    alerts.sort(key=lambda a: (ORDER[a.kind], a.days_to_close if a.days_to_close is not None else 10_000))
    return alerts


def digest(alerts: list[Alert], opps: dict[str, Opportunity], n_top: int, today: date) -> dict:
    """What the morning email leads with, e.g. 'DNCR today: 12 items, 3 close within 7 days'."""
    count = {k: sum(a.kind == k for a in alerts) for k in ORDER}
    within_week = sum(a.kind == "closing_soon" and (a.days_to_close or 0) <= 7 for a in alerts)
    headline = f"DNCR today: {n_top} items on the short list"
    if within_week:
        headline += f", {within_week} close within 7 days"
    if count["new_since_yesterday"]:
        headline += f", {count['new_since_yesterday']} new"
    return {
        "date": today.isoformat(),
        "headline": headline,
        "counts": count,
        "lines": [f"{opps[a.opp_id].title}: {a.message}" for a in alerts if a.opp_id in opps],
    }


def run(state: dict, today: Optional[date] = None) -> dict:
    today = today or today_et()
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    top_ids = [s["opp_id"] for s in state.get("top", [])]
    decided = latest_decisions(storage.read_json(DECISIONS_PATH, default=[]))
    history: dict[str, dict[str, str]] = storage.read_json(HISTORY_PATH, default={})

    alerts = build_alerts(opps, top_ids, decided, previous_run(history, today), today)

    # Save today's snapshot (rerunning the same day overwrites it; old dates are pruned)
    history[today.isoformat()] = {i: o.status for i, o in opps.items()}
    history = {d: history[d] for d in sorted(history)[-HISTORY_DAYS:]}
    storage.write_json(HISTORY_PATH, history)
    storage.write_json(LEGACY_IDS_PATH, list(opps.keys()))

    out = [a.model_dump() for a in alerts]
    storage.write_json("alerts/latest.json", out)
    storage.write_json("alerts/digest.json", digest(alerts, opps, len(top_ids), today))
    return {"alerts": out}
