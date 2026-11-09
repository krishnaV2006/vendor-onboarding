"""A restart mid-run, and the daily limit, must never leave a case stuck as running."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, main, pipeline
from app.main import app

reviewer = TestClient(app)
reviewer.post("/api/reviewer/login", json={"passcode": "test-passcode"})
HAPPY = next(s for s in reviewer.get("/api/meta").json()["scenarios"] if s["key"] == "happy")


@pytest.fixture(autouse=True)
def clean_database():
    assert reviewer.post("/api/reset").status_code == 200


def cut_off_run() -> tuple[str, str, int]:
    """A case as a restart leaves it: the run started, then the server died before it finished."""
    case_id, token = db.create_case(HAPPY["submission"]["legal_name"], "IN", None)
    run_id, _ = db.create_run(case_id, HAPPY["submission"])
    db.add_event(run_id, "stage_start", "intake", {})
    return case_id, token, run_id


def test_a_run_cut_off_by_a_restart_goes_to_a_person():
    case_id, token, run_id = cut_off_run()
    assert pipeline.recover_interrupted() == 1

    case = reviewer.get(f"/api/cases/{case_id}").json()
    assert (case["case"]["status"], case["case"]["waiting_on"]) == ("pending", "internal")
    assert case["runs"][-1]["state"] == "error"
    assert any("server restart" in a["action"] for a in case["audit"])
    events = reviewer.get(f"/api/runs/{run_id}").json()["events"]
    assert events[-1]["kind"] == "run_end" and events[-1]["payload"]["error"] == pipeline.RESTART_ERROR

    vendor = TestClient(app).get(f"/api/vendor/{case_id}", headers={"X-Vendor-Token": token}).json()
    assert vendor["status"] == "pending" and "being reviewed" in vendor["message"]["subject"]
    assert "restart" not in json.dumps(vendor)  # the vendor isn't told about our servers


def test_a_recovered_case_can_be_decided_by_a_reviewer():
    case_id, _, _ = cut_off_run()
    pipeline.recover_interrupted()
    response = reviewer.post(f"/api/cases/{case_id}/review", json={"action": "reject", "note": "Checked by hand"})
    assert response.status_code == 200 and response.json()["status"] == "rejected"


def test_recovery_leaves_finished_cases_alone():
    started = reviewer.post("/api/samples/happy/run").json()
    for _ in range(200):
        if reviewer.get(f"/api/cases/{started['case_id']}").json()["case"]["status"] != "running":
            break
        time.sleep(0.05)
    assert pipeline.recover_interrupted() == 0
    assert reviewer.get(f"/api/cases/{started['case_id']}").json()["case"]["status"] == "approved"


def test_a_case_with_no_run_is_removed():
    case_id, _ = db.create_case("Never started", "IN", None)
    pipeline.recover_interrupted()
    assert db.get_case(case_id) is None


def test_the_daily_limit_leaves_no_empty_case(monkeypatch):
    monkeypatch.setattr(main, "MAX_RUNS_PER_DAY", 0)
    files = {slot: (name, (Path("samples") / name).read_bytes(), "application/pdf") for slot, name in HAPPY["documents"].items()}
    response = TestClient(app).post("/api/submissions", data={"fields": json.dumps(HAPPY["submission"])}, files=files)
    assert response.status_code == 429
    assert reviewer.post("/api/samples/happy/run").status_code == 429
    assert reviewer.get("/api/cases").json() == []
