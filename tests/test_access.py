"""Who can see and do what, and what the form refuses to send."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import intake
from app import validators as v
from app.main import app

reviewer = TestClient(app)
reviewer.post("/api/reviewer/login", json={"passcode": "test-passcode"})
SCENARIOS = {s["key"]: s for s in reviewer.get("/api/meta").json()["scenarios"]}
HAPPY = SCENARIOS["happy"]

# Close to what a curious visitor typed into the live demo.
JUNK = {"legal_name": "meri marji", "entity_type": "Private Limited", "country": "IN", "state": "pakistan",
        "address": "Gustavstrasse 59", "city": "Furth", "postal_code": "90762", "contact_name": "D V",
        "contact_email": "someone@gmail.com", "contact_phone": "015510372989", "tax_id": "123456789012345",
        "pan": "1234567890", "account_holder": "Dhrivin", "bank_name": "Commerz bank",
        "account_number": "123239294923", "bank_code": "BIC32DJ12"}


@pytest.fixture(autouse=True)
def clean_database():
    assert reviewer.post("/api/reset").status_code == 200


def visitor() -> TestClient:
    return TestClient(app)


def happy_files() -> dict:
    return {slot: (name, (Path("samples") / name).read_bytes(), "application/pdf") for slot, name in HAPPY["documents"].items()}


def wait_for(run_id: int) -> dict:
    for _ in range(200):
        body = reviewer.get(f"/api/runs/{run_id}").json()
        run = body["run"]
        if run["state"] != "running" and any(e["kind"] == "run_end" for e in body["events"]):  # the last write is done too
            return run
        time.sleep(0.05)
    raise AssertionError("Run didn't finish")


def vendor_applies(client: TestClient, submission: dict | None = None, **data) -> dict:
    response = client.post("/api/submissions", data={"fields": json.dumps(submission or HAPPY["submission"]), **data}, files=happy_files())
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("method,path", [
    ("get", "/api/stats"), ("get", "/api/cases"), ("get", "/api/cases/VND-000000"), ("get", "/api/runs/1"),
    ("get", "/api/runs/1/stream"), ("get", "/api/documents/1"), ("post", "/api/samples/happy/run"),
    ("post", "/api/cases/VND-000000/resubmit-sample"), ("post", "/api/reset"),
])
def test_reviewer_console_is_closed_to_visitors(method, path):
    assert getattr(visitor(), method)(path).status_code == 401


def test_review_buttons_are_closed_to_visitors():
    started = vendor_applies(visitor())
    wait_for(started["run_id"])
    response = visitor().post(f"/api/cases/{started['case_id']}/review", json={"action": "approve", "note": "looks fine"})
    assert response.status_code == 401


def test_visitors_dont_get_the_prepared_cases():
    assert visitor().get("/api/meta").json()["scenarios"] == []
    assert len(SCENARIOS) == 7


def test_wrong_passcode_is_refused_and_repeated_guessing_is_locked_out():
    client = visitor()
    headers = {"x-forwarded-for": "203.0.113.9"}
    for _ in range(10):
        assert client.post("/api/reviewer/login", json={"passcode": "guess"}, headers=headers).status_code == 401
    assert client.post("/api/reviewer/login", json={"passcode": "test-passcode"}, headers=headers).status_code == 429
    assert client.get("/api/cases").status_code == 401


def test_right_passcode_opens_the_console_and_signing_out_closes_it():
    client = visitor()
    assert client.get("/api/session").json() == {"reviewer": False, "reviewer_setup": True}
    assert client.post("/api/reviewer/login", json={"passcode": "test-passcode"}).status_code == 200
    assert client.get("/api/cases").status_code == 200
    client.post("/api/reviewer/logout")
    assert client.get("/api/cases").status_code == 401


def test_console_stays_locked_when_no_passcode_is_set(monkeypatch):
    monkeypatch.delenv("REVIEWER_PASSCODE")
    assert visitor().post("/api/reviewer/login", json={"passcode": ""}).status_code == 503
    assert reviewer.get("/api/cases").status_code == 401


def test_vendor_sees_only_their_own_application_through_the_private_link():
    vendor = visitor()
    started = vendor_applies(vendor)
    case_id, token = started["case_id"], started["vendor_token"]
    wait_for(started["run_id"])
    assert vendor.get(f"/api/vendor/{case_id}").status_code == 404
    assert vendor.get(f"/api/vendor/{case_id}", headers={"x-vendor-token": "wrong"}).status_code == 404
    seen = vendor.get(f"/api/vendor/{case_id}", headers={"x-vendor-token": token})
    assert seen.status_code == 200 and seen.json()["status"] == "approved"
    assert not {"checks", "reads", "decision", "audit", "vendor_token"} & set(seen.json())
    assert vendor.get(f"/api/cases/{case_id}").status_code == 401


def test_vendor_view_shows_progress_while_running():
    started = vendor_applies(visitor())
    seen = visitor().get(f"/api/vendor/{started['case_id']}", headers={"x-vendor-token": started["vendor_token"]}).json()
    assert seen["status"] in ("running", "approved")
    if seen["status"] == "running":
        assert seen["progress"]["total"] == 7
    wait_for(started["run_id"])


def test_only_the_vendor_or_a_reviewer_can_resubmit():
    started = reviewer.post("/api/samples/personal_account/run").json()
    wait_for(started["run_id"])
    fixed = {**SCENARIOS["personal_account"]["submission"], "account_holder": "BLUEPEAK LOGISTICS PRIVATE LIMITED"}
    url = f"/api/cases/{started['case_id']}/resubmit"
    assert visitor().post(url, data={"fields": json.dumps(fixed)}).status_code == 404
    response = visitor().post(url, data={"fields": json.dumps(fixed)}, headers={"x-vendor-token": started["vendor_token"]})
    assert response.status_code == 200, response.text  # documents already on file count
    wait_for(response.json()["run_id"])


def test_a_visitor_cant_mark_an_application_as_a_prepared_case():
    started = vendor_applies(visitor(), prepared="happy")
    wait_for(started["run_id"])
    assert reviewer.get(f"/api/cases/{started['case_id']}").json()["case"]["scenario"] is None


def test_reviewer_cant_approve_while_the_vendor_still_has_fixes_to_make():
    started = reviewer.post("/api/samples/personal_account/run").json()
    wait_for(started["run_id"])
    url = f"/api/cases/{started['case_id']}/review"
    assert reviewer.post(url, json={"action": "approve", "note": "appprove"}).status_code == 409
    assert reviewer.post(url, json={"action": "reject", "note": "Not a real company"}).status_code == 200


def test_junk_application_is_refused_with_every_problem_named():
    before = reviewer.get("/api/stats").json()["total"]
    response = visitor().post("/api/submissions", data={"fields": json.dumps(JUNK)})
    assert response.status_code == 422
    errors = response.json()["detail"]["errors"]
    assert {"state", "postal_code", "contact_phone", "tax_id", "pan", "bank_code", "tax_certificate", "bank_proof"} <= set(errors)
    assert "This has" not in errors["tax_id"]  # it is 15 characters, so the length isn't the problem
    assert "PAN" in errors["tax_id"]
    assert reviewer.get("/api/stats").json()["total"] == before


def test_every_prepared_case_gets_past_the_form():
    for scenario in SCENARIOS.values():
        assert intake.validate(scenario["submission"], set(scenario["documents"])) == {}, scenario["key"]


def test_the_form_can_check_itself_before_sending():
    errors = visitor().post("/api/validate", json={"fields": JUNK, "docs": ["tax_certificate"]}).json()["errors"]
    assert "bank_proof" in errors and "tax_certificate" not in errors
    happy_docs = ["tax_certificate", "bank_proof"]
    assert visitor().post("/api/validate", json={"fields": HAPPY["submission"], "docs": happy_docs}).json()["errors"] == {}


@pytest.mark.parametrize("rule,value,country", [
    (v.phone_ok, "+91 98250 11223", "IN"), (v.phone_ok, "098250 11223", "IN"), (v.phone_ok, "(415) 555-0142", "US"),
    (v.phone_ok, "0118 496 0123", "GB"), (v.postal_ok, "RG1 8AA", "GB"), (v.postal_ok, "94102-1234", "US"),
    (v.state_ok, "tamil nadu", "IN"), (v.state_ok, "CA", "US"),
])
def test_ordinary_ways_of_writing_details_are_accepted(rule, value, country):
    assert rule(value, country)[0]


def test_wrong_length_says_how_long_it_is():
    assert "This has 7" in v.gstin_ok("1111111")[1]
    assert "This has 6" in v.pan_ok("222222")[1]


def test_sole_proprietor_must_name_the_owner():
    sub = {**HAPPY["submission"], "entity_type": "Sole proprietor", "owner_name": ""}
    assert intake.validate(sub, {"tax_certificate", "bank_proof"}) == {"owner_name": "Required."}
