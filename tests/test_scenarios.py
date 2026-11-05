"""Runs every prepared case end to end through the real API, using the backup reader (no AI)."""
import json
import os
import tempfile
import time
from pathlib import Path

os.environ["VENDOR_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["VENDOR_MIN_STAGE_MS"] = "0"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database():
    client.post("/api/reset")


def wait_for(run_id: int) -> dict:
    for _ in range(200):
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["state"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("Run didn't finish")


def start(key: str) -> dict:
    response = client.post(f"/api/samples/{key}/run")
    assert response.status_code == 200, response.text
    return response.json()


def failing_ids(run: dict, result: str) -> set:
    return {c["id"] for c in run["checks"] if c["result"] == result}


def test_happy_path_is_approved_and_joins_the_vendor_master():
    started = start("happy")
    run = wait_for(started["run_id"])
    assert run["status"] == "approved", run["decision"]
    case_ = client.get(f"/api/cases/{started['case_id']}").json()
    assert case_["case"]["status"] == "approved"
    assert client.get("/api/stats").json()["vendors_on_file"] == 8


def test_personal_account_then_fix_is_approved():
    started = start("personal_account")
    run = wait_for(started["run_id"])
    assert run["status"] == "pending" and run["waiting_on"] == "vendor"
    assert "bank_holder" in failing_ids(run, "fix")
    assert any("Ramesh K Patel" in item for item in run["message"]["items"])

    again = client.post(f"/api/cases/{started['case_id']}/resubmit-sample").json()
    assert again["version"] == 2
    second = wait_for(again["run_id"])
    assert second["status"] == "approved", second["decision"]
    assert any(c["id"] == "version_diff" and "Bank details changed" in c["detail"] for c in second["checks"])


def test_gstin_conflict_finds_both_contradictions():
    run = wait_for(start("gstin_conflict")["run_id"])
    assert run["status"] == "pending"
    assert {"gstin_pan", "gstin_state"} <= failing_ids(run, "fix")
    assert not failing_ids(run, "block")


def test_gstin_conflict_fix_is_approved():
    started = start("gstin_conflict")
    wait_for(started["run_id"])
    again = client.post(f"/api/cases/{started['case_id']}/resubmit-sample").json()
    assert wait_for(again["run_id"])["status"] == "approved"


def test_shared_bank_account_is_rejected_without_tipping_off():
    run = wait_for(start("shared_bank")["run_id"])
    assert run["status"] == "rejected"
    assert "bank_unique" in failing_ids(run, "block")
    message = json.dumps(run["message"]).lower()
    assert "orion" not in message and "bank" not in message and run["message"]["items"] == []


def test_wrong_documents_then_fix_is_approved():
    started = start("wrong_docs")
    run = wait_for(started["run_id"])
    assert run["status"] == "pending"
    assert {"tax_doc_type", "bank_doc_present"} <= failing_ids(run, "fix")
    again = client.post(f"/api/cases/{started['case_id']}/resubmit-sample").json()
    second = wait_for(again["run_id"])
    assert second["status"] == "approved", second["decision"]


def test_scan_without_ai_goes_to_a_person():
    run = wait_for(start("scanned")["run_id"])
    assert run["status"] == "pending" and run["waiting_on"] == "internal"
    assert "bank_doc_read" in failing_ids(run, "review")


def test_document_addressing_the_reader_changes_nothing_and_goes_to_a_person():
    run = wait_for(start("injection")["run_id"])
    assert run["status"] == "pending" and run["waiting_on"] == "internal"
    assert "tax_doc_injection" in failing_ids(run, "review")


def submit_happy_by_form(**extra) -> dict:
    scenario = next(s for s in client.get("/api/meta").json()["scenarios"] if s["key"] == "happy")
    files = {slot: (name, (Path("samples") / name).read_bytes(), "application/pdf") for slot, name in scenario["documents"].items()}
    response = client.post("/api/submissions", data={"fields": json.dumps(scenario["submission"]), **extra}, files=files)
    assert response.status_code == 200, response.text
    return response.json()


def test_repeat_application_is_caught():
    wait_for(start("happy")["run_id"])
    run = wait_for(submit_happy_by_form()["run_id"])
    assert run["status"] == "pending" and run["waiting_on"] == "internal"
    assert "tax_unique" in failing_ids(run, "review")


def test_prepared_case_gives_the_same_result_every_time_on_a_shared_demo():
    assert wait_for(start("happy")["run_id"])["status"] == "approved"
    assert wait_for(start("happy")["run_id"])["status"] == "approved"
    assert client.get("/api/stats").json()["vendors_on_file"] == 8


def test_form_filled_from_a_prepared_case_also_starts_fresh():
    wait_for(start("happy")["run_id"])
    started = submit_happy_by_form(prepared="happy")
    assert wait_for(started["run_id"])["status"] == "approved"
    assert client.get(f"/api/cases/{started['case_id']}").json()["case"]["scenario"] == "happy"


def test_unknown_prepared_key_is_ignored():
    wait_for(start("happy")["run_id"])
    started = submit_happy_by_form(prepared="made-up-key")
    assert "tax_unique" in failing_ids(wait_for(started["run_id"]), "review")
    assert client.get(f"/api/cases/{started['case_id']}").json()["case"]["scenario"] is None


def test_empty_disk_restores_the_recorded_runs_and_reset_still_clears_them(monkeypatch, tmp_path):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh" / "vendor.db")
    db.init()
    assert client.get("/api/stats").json()["total"] == 7
    wait_for(start("happy")["run_id"])
    db.init()  # a disk that already has data keeps it
    assert client.get("/api/stats").json()["total"] == 8
    client.post("/api/reset")
    assert client.get("/api/stats").json()["total"] == 0


def test_recorded_runs_are_real_ai_runs_with_every_kind_of_outcome(monkeypatch, tmp_path):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "vendor.db")
    db.init()
    cases = client.get("/api/cases").json()
    assert {c["status"] for c in cases} == {"approved", "pending", "rejected"}
    for case_ in cases:
        for run in client.get(f"/api/cases/{case_['id']}").json()["runs"]:
            assert run["state"] != "running"
            assert {read["reader"] for read in run["reads"].values()} == {"gemini"}


def test_reviewer_override_needs_a_reason_and_is_logged():
    started = start("injection")
    wait_for(started["run_id"])
    url = f"/api/cases/{started['case_id']}/review"
    assert client.post(url, json={"action": "approve", "note": "  "}).status_code == 400
    assert client.post(url, json={"action": "approve", "note": "Called the vendor"}).status_code == 200
    case_ = client.get(f"/api/cases/{started['case_id']}").json()
    assert case_["case"]["status"] == "approved"
    assert any(a["actor"] == "reviewer" and "Called the vendor" in a["detail"] for a in case_["audit"])


def test_vendor_hears_about_the_reviewer_decision_but_not_the_note():
    started = start("injection")
    wait_for(started["run_id"])
    client.post(f"/api/cases/{started['case_id']}/review", json={"action": "reject", "note": "Forged certificate"})
    seen = client.get(f"/api/vendor/{started['case_id']}").json()
    assert seen["status"] == "rejected"
    assert "unable to proceed" in seen["message"]["intro"]
    assert "Forged" not in json.dumps(seen)


def test_vendor_view_leaves_out_internal_reasons():
    started = start("shared_bank")
    wait_for(started["run_id"])
    seen = json.dumps(client.get(f"/api/vendor/{started['case_id']}").json())
    assert "Orion" not in seen and "checks" not in seen


def test_broken_reader_leaves_the_case_with_a_person(monkeypatch):
    from app import documents

    def broken(*_):
        raise ValueError("unexpected failure")
    monkeypatch.setattr(documents, "read_document", broken)
    started = start("happy")
    run = wait_for(started["run_id"])
    assert run["state"] == "error" and run["status"] == "pending" and run["waiting_on"] == "internal"
    assert client.get(f"/api/cases/{started['case_id']}").json()["case"]["waiting_on"] == "internal"


def test_blank_upload_runs_and_asks_for_everything():
    response = client.post("/api/submissions", data={"fields": json.dumps({"country": "IN"})})
    run = wait_for(response.json()["run_id"])
    assert run["status"] == "pending" and run["waiting_on"] == "vendor"
    assert {"fields_present", "tax_doc_present", "bank_doc_present"} <= failing_ids(run, "fix")


def test_non_document_upload_is_refused():
    response = client.post("/api/submissions", data={"fields": json.dumps({"country": "IN"})},
                           files={"bank_proof": ("virus.exe", b"MZ fake", "application/octet-stream")})
    assert response.status_code == 415


def test_ai_outage_midway_doesnt_stop_the_run(monkeypatch):
    from app import documents

    def broken(*_):
        raise RuntimeError("AI is down")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(documents, "read_with_claude", broken)
    run = wait_for(start("happy")["run_id"])
    assert run["status"] == "approved"
    assert run["reads"]["bank_proof"]["reader"] == "text_parser"


def test_ai_path_reads_a_scan_and_sends_the_expected_request(monkeypatch):
    from tests.stub_anthropic import Handler, serve
    server = serve()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    run = wait_for(start("scanned")["run_id"])
    assert run["status"] == "approved", run["decision"]
    assert run["reads"]["bank_proof"]["reader"] == "claude"

    sent = Handler.requests[0]
    assert sent["body"]["model"] == "claude-opus-5-5"
    assert sent["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in sent["headers"]["anthropic-beta"]
    assert sent["body"]["output_config"]["format"]["type"] == "json_schema"
    assert sent["body"]["messages"][0]["content"][0]["type"] == "document"
    assert "temperature" not in sent["body"] and "thinking" not in sent["body"]


def test_ai_only_ever_sees_what_the_vendor_may_see(monkeypatch):
    from tests.stub_anthropic import Handler, serve
    server = serve()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    run = wait_for(start("personal_account")["run_id"])
    assert run["message"]["intro_source"] == "ai"
    prompts = [r["body"]["messages"][0]["content"] for r in Handler.requests if isinstance(r["body"]["messages"][0]["content"], str)]
    assert len(prompts) == 1 and "fraud" not in prompts[0].lower()

    Handler.requests.clear()
    run = wait_for(start("shared_bank")["run_id"])
    assert run["status"] == "rejected" and run["message"]["intro_source"] == "template"
    assert not [r for r in Handler.requests if isinstance(r["body"]["messages"][0]["content"], str)]


def use_gemini(monkeypatch, behaviour=None):
    from tests.stub_gemini import serve
    server = serve(behaviour)
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("VENDOR_GEMINI_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    return server


def models_tried():
    from tests.stub_gemini import Handler
    return [r["model"] for r in Handler.requests]


def test_gemini_reads_a_scan_and_sends_the_expected_request(monkeypatch):
    from app import documents
    from tests.stub_gemini import Handler
    use_gemini(monkeypatch)
    run = wait_for(start("scanned")["run_id"])
    assert run["status"] == "approved", run["decision"]
    assert run["reads"]["bank_proof"]["reader"] == "gemini"
    assert run["reads"]["bank_proof"]["model"] == documents.GEMINI_MODELS[0]

    sent = Handler.requests[0]
    assert sent["headers"]["x-goog-api-key"] == "not-a-real-key"
    assert sent["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert sent["body"]["generationConfig"]["responseJsonSchema"]["required"] == documents.SCHEMA["required"]
    assert "application/pdf" in sent["body"]["contents"][0]["parts"][0]["inlineData"].values()
    assert "untrusted data" in sent["body"]["systemInstruction"]["parts"][0]["text"]


@pytest.mark.parametrize("behaviour", ["busy", "full", "garbage"])
def test_a_failing_gemini_model_hands_over_to_the_next_with_no_library_retries(monkeypatch, behaviour):
    from app import documents
    first, second = documents.GEMINI_MODELS[:2]
    use_gemini(monkeypatch, {first: behaviour})
    data = (Path("samples") / "thames_bank_letter_scan.pdf").read_bytes()
    result = documents.read_document(data, "application/pdf")
    assert (result.reader, result.model, result.error) == ("gemini", second, None)
    assert models_tried() == [first, second]


def test_a_slow_gemini_model_hands_over_to_the_next(monkeypatch):
    from app import documents
    first, second = documents.GEMINI_MODELS[:2]
    use_gemini(monkeypatch, {first: "slow"})
    monkeypatch.setattr(documents, "GEMINI_ATTEMPT_SECONDS", 0.5)
    data = (Path("samples") / "thames_bank_letter_scan.pdf").read_bytes()
    started = time.monotonic()
    result = documents.read_document(data, "application/pdf")
    assert (result.reader, result.model) == ("gemini", second)
    assert time.monotonic() - started < 2.5


def test_a_model_thats_slow_but_not_failing_brings_in_the_next_and_the_first_answer_wins(monkeypatch):
    from app import documents
    first, second = documents.GEMINI_MODELS[:2]
    use_gemini(monkeypatch, {first: "slow"})
    monkeypatch.setattr(documents, "GEMINI_HEDGE_SECONDS", 0.2)
    data = (Path("samples") / "thames_bank_letter_scan.pdf").read_bytes()
    started = time.monotonic()
    result = documents.read_document(data, "application/pdf")
    assert (result.reader, result.model, result.error) == ("gemini", second, None)
    assert time.monotonic() - started < 2.5
    assert models_tried() == [first, second]


def test_a_rejected_key_stops_at_the_first_model(monkeypatch):
    from app import documents
    use_gemini(monkeypatch, {m: "bad_key" for m in documents.GEMINI_MODELS})
    run = wait_for(start("happy")["run_id"])
    assert run["reads"]["bank_proof"]["reader"] == "text_parser"
    assert "rejected" in run["reads"]["bank_proof"]["error"]
    assert len(models_tried()) == 2  # one per document, not one per model
    assert run["status"] == "approved"


def test_when_every_gemini_model_is_full_a_text_pdf_uses_the_parser_and_a_scan_goes_to_a_person(monkeypatch):
    from app import documents
    use_gemini(monkeypatch, {m: "full" for m in documents.GEMINI_MODELS})
    run = wait_for(start("happy")["run_id"])
    assert run["status"] == "approved"
    assert run["reads"]["bank_proof"]["reader"] == "text_parser"
    assert "rate limiting" in run["reads"]["bank_proof"]["error"]

    run = wait_for(start("scanned")["run_id"])
    assert run["reads"]["bank_proof"]["reader"] == "none"
    assert (run["status"], run["waiting_on"]) == ("pending", "internal")


def test_gemini_only_ever_sees_what_the_vendor_may_see(monkeypatch):
    from tests.stub_gemini import Handler
    use_gemini(monkeypatch)
    run = wait_for(start("personal_account")["run_id"])
    assert (run["message"]["intro_source"], run["message"]["intro_by"]) == ("ai", "Gemini")
    prompts = [r["body"]["contents"][0]["parts"][0]["text"] for r in Handler.requests if "text" in r["body"]["contents"][0]["parts"][0]]
    assert len(prompts) == 1 and "fraud" not in prompts[0].lower()

    Handler.requests.clear()
    run = wait_for(start("shared_bank")["run_id"])
    assert run["status"] == "rejected" and run["message"]["intro_source"] == "template"
    assert not [r for r in Handler.requests if "text" in r["body"]["contents"][0]["parts"][0]]


@pytest.mark.parametrize("env, expected", [
    ({}, "off"),
    ({"GEMINI_API_KEY": "k"}, "gemini"),
    ({"ANTHROPIC_API_KEY": "k"}, "claude"),
    ({"GEMINI_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}, "gemini"),
    ({"GEMINI_API_KEY": "k", "ANTHROPIC_API_KEY": "k", "VENDOR_AI_PROVIDER": "claude"}, "claude"),
    ({"GEMINI_API_KEY": "k", "VENDOR_AI_PROVIDER": "off"}, "off"),
    ({"GEMINI_API_KEY": "k", "VENDOR_AI_PROVIDER": "claude"}, "off"),
])
def test_provider_setting(monkeypatch, env, expected):
    from app import documents
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert documents.provider() == expected
    response = client.get("/api/health").json()
    assert response["ai_configured"] == (expected != "off")
