"""Grant Topic Radar. Spec: docs/agents/radar.md. All offline."""
from datetime import date

from agents import radar

TODAY = date(2026, 11, 7)


def row(i, title, close, agency="", eligible=True):
    return {"id": str(i), "title": title, "agency": agency, "close": close, "bucket": radar.bucket(close, TODAY), "eligible": eligible}


def test_area_rules_first_match_and_agency_fallback():
    assert radar.assign("Historic Preservation Fund Grants", "") == "Historic preservation & heritage"
    assert radar.assign("Outdoor Recreation Legacy Partnership", "National Park Service") == "Parks & outdoor recreation"
    assert radar.assign("Project Grants", "National Endowment for the Arts") == "Arts, humanities & museums"
    assert radar.assign("Something unusual", "Nobody") == "Other"


def test_buckets():
    assert radar.bucket("2026-10-01", TODAY) == "Closed"
    assert radar.bucket("2026-12-01", TODAY) == "2026"
    assert radar.bucket("2027-03-01", TODAY) == "2027"
    assert radar.bucket("2029-01-01", TODAY) == "2028+"
    assert radar.bucket(None, TODAY) == "Forecast"


def test_growth_is_coming_share_over_average():
    rows = ([row(i, "Museum collections grant", "2027-01-01") for i in range(6)]
            + [row(10 + i, "Police crime grant", "2026-01-01") for i in range(6)])
    out = radar.build(rows, TODAY, "test")
    areas = {a["name"]: a for a in out["all"]["areas"]}
    assert out["all"]["overall_coming"] == 0.5
    assert areas["Arts, humanities & museums"]["momentum"] == 2.0 and areas["Arts, humanities & museums"]["dncr"]
    assert areas["Justice & public safety"]["momentum"] == 0.0
    assert {"n", "closed", "coming", "buckets", "areas", "words", "words_dncr", "words_other"} <= set(out["all"])
    assert set(out) >= {"as_of", "source", "all", "eligible"}


def test_no_closed_history_means_no_growth_claim():
    rows = [row(i, "Museum collections grant", "2027-01-01") for i in range(5)]
    out = radar.build(rows, TODAY, "test")
    assert all(a["momentum"] is None for a in out["all"]["areas"])


def test_open_to_states_drops_other_regions():
    assert radar.open_to_states("Museum grants", True)
    assert not radar.open_to_states("Gulf of Mexico habitat restoration", True)
    assert not radar.open_to_states("Museum grants", False)


def test_pipeline_writes_radar_and_page_serves_it():
    import pipeline
    from fastapi.testclient import TestClient

    from app.api import app

    assert pipeline.main() == 0
    client = TestClient(app)
    data = client.get("/api/radar").json()
    assert data["all"]["n"] == 40 and data["source"].startswith("Offline fixture")
    page = client.get("/radar")
    assert page.status_code == 200 and "Grant Topic Radar" in page.text and 'href="/#today"' in page.text
    assert "__DATA__" not in page.text


def test_radar_failure_never_breaks_the_run(monkeypatch):
    monkeypatch.setattr(radar, "rows_from_state", lambda state, today: (_ for _ in ()).throw(RuntimeError("boom")))
    assert radar.run({"opportunities": []})["radar"]["status"] == "error"
