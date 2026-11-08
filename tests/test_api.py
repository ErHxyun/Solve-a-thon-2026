"""Web API (owner: lead). Runs the offline pipeline into a temp folder, then calls every endpoint."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import pipeline
    from app.api import app

    assert pipeline.main() == 0  # fills tmp storage: scored/, runs/, alerts/
    return TestClient(app)


def first_id(client) -> str:
    items = client.get("/api/shortlist").json()["items"]
    assert items, "short list is empty"
    return items[0]["opp_id"]


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_shortlist_shape(client):
    body = client.get("/api/shortlist").json()
    assert body["last_refreshed"] and body["run_status"] == "succeeded"
    item = body["items"][0]
    for key in ("title", "funder", "url", "days_left", "score", "confidence", "suggested_decision",
                "objective", "route_to", "card", "verification", "my_decision"):
        assert key in item
    assert all(i["suggested_decision"] != "Skip" for i in body["items"])


def test_shortlist_can_include_skipped(client):
    small = len(client.get("/api/shortlist").json()["items"])
    big = len(client.get("/api/shortlist?include_skipped=true&include_low=true&limit=200").json()["items"])
    assert big >= small


def test_opportunity_detail(client):
    opp_id = first_id(client)
    body = client.get(f"/api/opportunities/{opp_id}").json()
    assert body["opportunity"]["id"] == opp_id and "rationale" in body and body["decisions"] == []
    assert client.get("/api/opportunities/nope").status_code == 404


def test_decision_then_undo(client):  # T9: Skip then undo
    opp_id = first_id(client)
    r = client.post("/api/decisions", json={"opp_id": opp_id, "decision": "Skip", "note": "not ours"})
    assert r.status_code == 201
    assert opp_id not in [i["opp_id"] for i in client.get("/api/shortlist").json()["items"]]  # skipped = hidden
    undone = client.delete(f"/api/decisions/{opp_id}").json()
    assert undone["undone"]["decision"] == "Skip" and undone["now"] is None
    assert opp_id in [i["opp_id"] for i in client.get("/api/shortlist").json()["items"]]  # back again


def test_bad_decision_rejected(client):
    opp_id = first_id(client)
    assert client.post("/api/decisions", json={"opp_id": opp_id, "decision": "Submit"}).status_code == 422
    assert client.post("/api/decisions", json={"opp_id": "nope", "decision": "Chase"}).status_code == 404


def test_route_and_apply_assist_offline(client):
    opp_id = first_id(client)
    route = client.post(f"/api/opportunities/{opp_id}/route").json()
    assert route["opp_id"] == opp_id and route["message"]
    pack = client.post(f"/api/opportunities/{opp_id}/apply-assist").json()
    assert pack["opp_id"] == opp_id and pack["sf424_fields_to_gather"]


def test_alerts_and_run(client):
    assert "alerts" in client.get("/api/alerts").json()
    assert client.get("/api/run").json()["status"] == "succeeded"


def test_money_category_is_exclusive():
    from app.api import money_category
    opp = {"applicant_types": [{"id": "00"}, {"id": "12"}]}
    assert money_category({"components": {"P_case": "1 formula"}, "opportunity": opp}) == "Formula"
    assert money_category({"components": {"P_case": "2 states only"}, "opportunity": opp}) == "State-only competition"
    assert money_category({"components": {"P_case": "3 open"}, "opportunity": opp}) == "Open competition"
    assert money_category({"components": {"P_case": "3 open"}, "opportunity": {"applicant_types": [{"id": "00"}]}}) == "Unknown"
    assert money_category({"components": {}, "opportunity": {"id": "1", "number": "N", "title": "t", "applicant_types": [{"id": "00", "description": "State governments"}]}}) == "State-only competition"


def test_shortlist_has_search_and_card_fields(client):
    item = client.get("/api/shortlist").json()["items"][0]
    assert {"number", "plan_match", "money_category", "tags"} <= set(item)


def test_save_then_track_moves_one_tracker_record(client):
    oid = first_id(client)
    client.post("/api/decisions", json={"opp_id": oid, "decision": "Waitlist"})
    assert client.get("/api/tracker").json()[0]["stage"] == "saved"
    client.post("/api/decisions", json={"opp_id": oid, "decision": "Chase"})
    recs = client.get("/api/tracker").json()
    assert len(recs) == 1 and recs[0]["stage"] == "reviewing"           # same record, no duplicate
    item = next(i for i in client.get("/api/shortlist").json()["items"] if i["opp_id"] == oid)
    assert item["tracker"]["stage"] == "reviewing" and item["suggested_decision"]  # AI suggestion untouched


def test_tracker_update_logs_history_and_syncs_decision(client):
    oid = first_id(client)
    r = client.put(f"/api/tracker/{oid}", json={"stage": "preparing", "owner": "State Parks", "next_action": "Confirm match",
                                                 "next_action_due": "2026-11-20", "eligibility": "verified", "my_decision": "pursue"})
    rec = r.json()
    assert rec["owner"] == "State Parks" and {h["field"] for h in rec["history"]} >= {"stage", "owner", "eligibility"}
    assert client.get("/api/decisions").json()[-1]["decision"] == "Chase"   # Alert / Notifier see it as chased
    item = next(i for i in client.get("/api/shortlist").json()["items"] if i["opp_id"] == oid)
    assert item["eligibility"] == "verified"
    assert client.put(f"/api/tracker/{oid}", json={"next_action_due": "soon"}).status_code == 422
    assert client.put(f"/api/tracker/{oid}", json={"stage": "won"}).status_code == 422
    assert client.put("/api/tracker/nope", json={"stage": "saved"}).status_code == 404


def test_card_on_demand_for_item_outside_top(client):
    items = client.get("/api/shortlist?include_skipped=true&include_low=true&limit=200").json()["items"]
    target = next(i for i in items if not i["card"])
    checked = client.post(f"/api/opportunities/{target['opp_id']}/card").json()
    assert checked["card"]["opp_id"] == target["opp_id"]
    again = next(i for i in client.get("/api/shortlist?include_skipped=true&include_low=true&limit=200").json()["items"]
                 if i["opp_id"] == target["opp_id"])
    assert again["card"] and again["card_on_demand"] is True


def test_funding_fields_are_separate(client):
    item = client.get("/api/shortlist").json()["items"][0]
    assert item["funding_mechanism"] in ("Formula", "Competitive", "Unknown")
    assert item["competition_scope"] in ("State-only", "Open to multiple applicant types", "Unknown")
    assert item["eligibility"] in ("verified", "pending", "ineligible", "unknown") and "A" in item["factors"]
