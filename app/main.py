"""Web API and the live event stream."""
import asyncio
import json
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import checks as rules  # noqa: E402
from . import db, documents, pipeline, validators  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SAMPLES = ROOT / "samples"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_RUNS_PER_DAY = int(os.getenv("VENDOR_MAX_RUNS_PER_DAY", "300"))
MEDIA_SIGNATURES = {"application/pdf": b"%PDF", "image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff"}

app = FastAPI(title="Vendor onboarding")
db.init()
threading.Thread(target=documents.warm_up, daemon=True).start()


def _parse_fields(fields: str) -> dict:
    try:
        parsed = json.loads(fields)
    except json.JSONDecodeError:
        raise HTTPException(400, "The form data couldn't be read.")
    if not isinstance(parsed, dict):
        raise HTTPException(400, "The form data couldn't be read.")
    return {key: value if isinstance(value, str) else "" for key, value in parsed.items()}


def _scenarios() -> dict:
    return json.loads((SAMPLES / "scenarios.json").read_text(encoding="utf-8"))


def _start_run(case_id: str, submission: dict, new_docs: dict) -> dict:
    if db.runs_today() >= MAX_RUNS_PER_DAY:
        raise HTTPException(429, "The demo's daily limit has been reached.")
    run_id, version = db.create_run(case_id, submission)
    for slot, doc in new_docs.items():
        db.add_document(case_id, version, slot, doc["filename"], doc["media_type"], pipeline.sha256(doc["data"]), doc["data"])
    docs = db.latest_documents(case_id)
    db.add_audit(case_id, "vendor", "Submitted application" if version == 1 else f"Resubmitted (attempt {version})",
                 f"New documents: {', '.join(d['filename'] for d in new_docs.values()) or 'none'}")
    threading.Thread(target=pipeline.execute, args=(run_id, case_id, version, submission, docs), daemon=True).start()
    return {"case_id": case_id, "run_id": run_id, "version": version}


async def _read_upload(upload: UploadFile | None) -> dict | None:
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    if not data:
        return None
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{upload.filename} is larger than 10 MB.")
    media_type = next((m for m, sig in MEDIA_SIGNATURES.items() if data.startswith(sig)), None)
    if not media_type:
        raise HTTPException(415, f"{upload.filename} isn't a PDF, PNG or JPEG.")
    return {"filename": upload.filename, "media_type": media_type, "data": data}


def _sample_doc(path: str) -> dict:
    file = (SAMPLES / path).resolve()
    if SAMPLES.resolve() not in file.parents:
        raise HTTPException(404, "Unknown sample document.")
    media_type = "application/pdf" if file.suffix == ".pdf" else "image/png"
    return {"filename": file.name, "media_type": media_type, "data": file.read_bytes()}


@app.get("/api/health")
def health():
    return {"ok": True, "ai_configured": documents.ai_available(), "ai_name": documents.ai_name(), "model": documents.model_label()}


@app.get("/api/meta")
def meta():
    return {
        "stages": [{"key": k, "title": t, "question": q} for k, t, q in pipeline.STAGES],
        "countries": {k: {"name": c["name"], "tax_label": c["tax_label"], "bank_code_label": c["bank_code_label"], "tax_doc_name": c["tax_doc_name"]} for k, c in rules.COUNTRIES.items()},
        "in_states": sorted(validators.GST_STATES.values()),
        "scenarios": [{"key": s["key"], "tag": s["tag"], "title": s["title"], "shows": s["shows"], "expected": s["expected"],
                       "fix_label": s.get("fix_label") if s.get("fix") else None, "submission": s["submission"], "documents": s["documents"]}
                      for s in _scenarios()["scenarios"]],
    }


@app.get("/api/stats")
def stats():
    return db.stats()


@app.get("/api/cases")
def cases():
    return db.list_cases()


@app.get("/api/cases/{case_id}")
def case(case_id: str):
    found = db.get_case(case_id)
    if not found:
        raise HTTPException(404, "Case not found.")
    return {"case": found, "runs": db.runs_for_case(case_id), "documents": db.list_documents(case_id), "audit": db.audit_for_case(case_id)}


@app.get("/api/vendor/{case_id}")
def vendor_view(case_id: str):
    """What the vendor is allowed to see. Internal reasons are deliberately left out."""
    found = db.get_case(case_id)
    if not found:
        raise HTTPException(404, "Case not found.")
    latest = db.runs_for_case(case_id)[-1]
    current_docs = db.latest_documents(case_id)
    return {
        "case_id": case_id, "status": found["status"], "waiting_on": found["waiting_on"], "version": found["version"],
        "message": found["message_override"] or latest["message"], "submission": latest["submission"],
        "documents": {slot: {"filename": doc["filename"]} for slot, doc in current_docs.items()},
    }


@app.get("/api/runs/{run_id}")
def run(run_id: int):
    found = db.get_run(run_id)
    if not found:
        raise HTTPException(404, "Run not found.")
    return {"run": found, "events": db.events_after(run_id, 0)}


@app.get("/api/runs/{run_id}/stream")
async def stream(run_id: int):
    if not db.get_run(run_id):
        raise HTTPException(404, "Run not found.")

    async def events():
        last_id = 0
        idle_ticks = 0
        while idle_ticks < 1800:  # give up after about 6 minutes of silence
            batch = await asyncio.to_thread(db.events_after, run_id, last_id)
            for event in batch:
                last_id = event["id"]
                yield f"data: {json.dumps(event)}\n\n"
                if event["kind"] == "run_end":
                    return
            idle_ticks = 0 if batch else idle_ticks + 1
            if idle_ticks and idle_ticks % 50 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/documents/{doc_id}")
def document(doc_id: int):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    safe_name = doc["filename"].replace('"', "")
    return Response(doc["data"], media_type=doc["media_type"], headers={"Content-Disposition": f'inline; filename="{safe_name}"'})


@app.post("/api/submissions")
async def submit(fields: str = Form(...), prepared: str | None = Form(None), tax_certificate: UploadFile | None = File(None), bank_proof: UploadFile | None = File(None)):
    submission = _parse_fields(fields)
    new_docs = {}
    for slot, upload in (("tax_certificate", tax_certificate), ("bank_proof", bank_proof)):
        doc = await _read_upload(upload)
        if doc:
            new_docs[slot] = doc
    scenario = next((s["key"] for s in _scenarios()["scenarios"] if s["key"] == prepared), None)
    if scenario:  # the form was filled from a prepared case
        db.remove_prepared_vendors(scenario)
    case_id = db.create_case(submission.get("legal_name") or "Unnamed vendor", submission.get("country"), scenario)
    return _start_run(case_id, submission, new_docs)


def _check_can_resubmit(case_id: str) -> dict:
    found = db.get_case(case_id)
    if not found:
        raise HTTPException(404, "Case not found.")
    if found["status"] == "running":
        raise HTTPException(409, "This case is still running.")
    if found["status"] != "pending" or found["waiting_on"] != "vendor":
        raise HTTPException(409, "This case isn't waiting on the vendor.")
    return found


@app.post("/api/cases/{case_id}/resubmit")
async def resubmit(case_id: str, fields: str = Form(...), tax_certificate: UploadFile | None = File(None), bank_proof: UploadFile | None = File(None)):
    _check_can_resubmit(case_id)
    new_docs = {}
    for slot, upload in (("tax_certificate", tax_certificate), ("bank_proof", bank_proof)):
        doc = await _read_upload(upload)
        if doc:
            new_docs[slot] = doc
    return _start_run(case_id, _parse_fields(fields), new_docs)


@app.post("/api/samples/{key}/run")
def run_sample(key: str):
    scenario = next((s for s in _scenarios()["scenarios"] if s["key"] == key), None)
    if not scenario:
        raise HTTPException(404, "Unknown scenario.")
    db.remove_prepared_vendors(key)
    case_id = db.create_case(scenario["submission"]["legal_name"], scenario["submission"]["country"], key)
    return _start_run(case_id, scenario["submission"], {slot: _sample_doc(path) for slot, path in scenario["documents"].items()})


@app.post("/api/cases/{case_id}/resubmit-sample")
def resubmit_sample(case_id: str):
    found = _check_can_resubmit(case_id)
    scenario = next((s for s in _scenarios()["scenarios"] if s["key"] == found.get("scenario")), None)
    if not scenario or not scenario.get("fix"):
        raise HTTPException(404, "There's no prepared fix for this case.")
    fix = scenario["fix"]
    return _start_run(case_id, {**scenario["submission"], **fix.get("changes", {})}, {slot: _sample_doc(path) for slot, path in fix["documents"].items()})


class ReviewAction(BaseModel):
    action: str
    note: str


@app.post("/api/cases/{case_id}/review")
def review(case_id: str, body: ReviewAction):
    found = db.get_case(case_id)
    if not found:
        raise HTTPException(404, "Case not found.")
    if found["status"] == "running":
        raise HTTPException(409, "This case is still running.")
    if body.action not in ("approve", "reject"):
        raise HTTPException(400, "Choose approve or reject.")
    if not body.note.strip():
        raise HTTPException(400, "A reason is required to overrule the process.")
    latest = db.runs_for_case(case_id)[-1]
    sub = latest["submission"]
    if body.action == "approve":
        bank = sub.get("bank_code") if sub.get("country") == "GB" else sub.get("account_number")
        db.add_vendor(sub.get("legal_name"), validators.clean(sub.get("tax_id")), validators.clean(bank), case_id)
    else:
        db.remove_vendor(case_id)
    message = rules.reviewer_message(body.action, sub)
    db.set_reviewer_outcome(case_id, "approved" if body.action == "approve" else "rejected", message)
    db.add_audit(case_id, "reviewer", "Approved by reviewer" if body.action == "approve" else "Rejected by reviewer", body.note.strip())
    db.add_audit(case_id, "process", "Message posted to the vendor's page", message["subject"])
    return db.get_case(case_id)


@app.post("/api/reset")
def reset():
    db.init(reset=True)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/samples", StaticFiles(directory=SAMPLES), name="samples")
