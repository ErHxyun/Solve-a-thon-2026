"""Alert — deadline and watch-list alerts.  Owner: P2
Spec: docs/agents/alert.md
"""
from __future__ import annotations

from core import storage
from core.schemas import Alert, Opportunity

CLOSING_SOON_DAYS = 10
WAITLIST_WARN_DAYS = 21


def run(state: dict) -> dict:
    opps = {o["id"]: Opportunity(**o) for o in state["opportunities"]}
    top_ids = [s["opp_id"] for s in state["top"]]
    decisions = storage.read_json("decisions/dncr.json", default=[])
    waitlisted = {d["opp_id"] for d in decisions if d.get("decision") == "Waitlist"}
    yesterday_ids = set(storage.read_json("opportunities/previous_ids.json", default=[]))

    alerts: list[Alert] = []
    for opp_id in top_ids:
        opp = opps[opp_id]
        if opp.days_to_close is not None and 0 <= opp.days_to_close <= CLOSING_SOON_DAYS:
            alerts.append(Alert(opp_id=opp_id, kind="closing_soon", days_to_close=opp.days_to_close,
                                message=f"Closes in {opp.days_to_close} days"))
        if yesterday_ids and opp_id not in yesterday_ids:
            alerts.append(Alert(opp_id=opp_id, kind="new_since_yesterday", message="New since yesterday"))

    for opp_id in waitlisted & opps.keys():
        opp = opps[opp_id]
        if opp.status == "posted" and opp.days_to_close is not None and opp.days_to_close <= WAITLIST_WARN_DAYS:
            alerts.append(Alert(opp_id=opp_id, kind="waitlist_21_days", days_to_close=opp.days_to_close,
                                message=f"Waitlisted item closes in {opp.days_to_close} days"))
        # TODO(P2): 'waitlist_opening' when a waitlisted forecast becomes posted

    storage.write_json("opportunities/previous_ids.json", list(opps.keys()))
    out = [a.model_dump() for a in alerts]
    storage.write_json("alerts/latest.json", out)
    return {"alerts": out}
