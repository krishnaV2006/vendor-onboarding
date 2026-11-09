"""Web API and the live event stream.

Two kinds of caller: a vendor, who sees only their own application through a private link,
and a reviewer, who signs in with the passcode in REVIEWER_PASSCODE and sees everything.
With no passcode set, the reviewer console stays locked.
"""
import asyncio
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import checks as rules  # noqa: E402
from . import db, documents, intake, pipeline, validators  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SAMPLES = ROOT / "samples"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_RUNS_PER_DAY = int(os.getenv("VENDOR_MAX_RUNS_PER_DAY", "300"))
MEDIA_SIGNATURES = {"application/pdf": b"%PDF", "image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff"}
REVIEWER_COOKIE = "reviewer"
REVIEWER_SESSION_SECONDS = 12 * 3600
LOGIN_ATTEMPTS = 10  # wrong passcodes allowed from one address within LOGIN_WINDOW_SECONDS
LOGIN_WINDOW_SECONDS = 900

app = FastAPI(title="Vendor onboarding")
db.init()
pipeline.recover_interrupted()
threading.Thread(target=documents.warm_up, daemon=True).start()
_failed_logins: dict[str, list[float]] = {}


# ---------- who is asking ----------

def _passcode() -> str:
    return os.getenv("REVIEWER_PASSCODE", "").strip()


def _session_value() -> str:
    return hmac.new(_passcode().encode(), b"vendor-onboarding reviewer session", hashlib.sha256).hexdigest()


def is_reviewer(request: Request) -> bool:
    return bool(_passcode()) and hmac.compare_digest(request.cookies.get(REVIEWER_COOKIE, ""), _session_value())


def require_reviewer(request: Request) -> None:
    if not is_reviewer(request):
        raise HTTPException(401, "Reviewer sign-in required.")


def _client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _case_for(request: Request, case_id: str, token: str | None) -> dict:
    """The case, if the caller is a reviewer or holds the vendor's private token. To anyone else it doesn't exist."""
    found = db.get_case(case_id)
    if found and (is_reviewer(request) or (token and found.get("vendor_token") and hmac.compare_digest(token, found["vendor_token"]))):
        return found
    raise HTTPException(404, "Application not found. Check that you opened the full link you were given.")


class Login(BaseModel):
    passcode: str


@app.get("/api/session")
def session(request: Request):
    return {"reviewer": is_reviewer(request), "reviewer_setup": bool(_passcode())}


@app.post("/api/reviewer/login")
def login(body: Login, request: Request):
    if not _passcode():
        raise HTTPException(503, "The reviewer console isn't set up on this server. Set REVIEWER_PASSCODE.")
    address, now = _client_address(request), time.monotonic()
    recent = [t for t in _failed_logins.get(address, []) if now - t < LOGIN_WINDOW_SECONDS]
    if len(recent) >= LOGIN_ATTEMPTS:
        raise HTTPException(429, "Too many wrong passcodes. Try again in 15 minutes.")
    if not hmac.compare_digest(body.passcode.strip().encode(), _passcode().encode()):
        _failed_logins[address] = recent + [now]
        raise HTTPException(401, "That passcode isn't right.")
    _failed_logins.pop(address, None)
    response = JSONResponse({"reviewer": True})
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(REVIEWER_COOKIE, _session_value(), max_age=REVIEWER_SESSION_SECONDS, httponly=True, samesite="strict", secure=secure)
    return response


@app.post("/api/reviewer/logout")
def logout():
    response = JSONResponse({"reviewer": False})
    response.delete_cookie(REVIEWER_COOKIE)
    return response


# ---------- helpers ----------

def _parse_fields(fields: str) -> dict:
    try:
        parsed = json.loads(fields)
    except json.JSONDecodeError:
        raise HTTPException(400, "The form data couldn't be read.")
    if not isinstance(parsed, dict):
        raise HTTPException(400, "The form data couldn't be read.")
    return {key: value if isinstance(value, str) else "" for key, value in parsed.items()}


def _reject_invalid(submission: dict, docs_on_file: set[str]) -> None:
    """The form can't be sent until every answer has the right shape. The browser checks first; this is the guarantee."""
    errors = intake.validate(submission, docs_on_file)
    if errors:
        count = len(errors)
        raise HTTPException(422, {"message": f"{count} detail{'s need' if count > 1 else ' needs'} correcting before this can be sent.", "errors": errors})


def _scenarios() -> dict:
    return json.loads((SAMPLES / "scenarios.json").read_text(encoding="utf-8"))


def _check_daily_limit() -> None:
    """Checked before a case is created, so hitting the limit never leaves an empty case behind."""
    if db.runs_today() >= MAX_RUNS_PER_DAY:
        raise HTTPException(429, "The demo's daily limit has been reached.")


def _start_run(case_id: str, submission: dict, new_docs: dict) -> dict:
    _check_daily_limit()
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


async def _read_uploads(tax_certificate: UploadFile | None, bank_proof: UploadFile | None) -> dict:
    new_docs = {}
    for slot, upload in (("tax_certificate", tax_certificate), ("bank_proof", bank_proof)):
        doc = await _read_upload(upload)
        if doc:
            new_docs[slot] = doc
    return new_docs


def _sample_doc(path: str) -> dict:
    file = (SAMPLES / path).resolve()
    if SAMPLES.resolve() not in file.parents:
        raise HTTPException(404, "Unknown sample document.")
    media_type = "application/pdf" if file.suffix == ".pdf" else "image/png"
    return {"filename": file.name, "media_type": media_type, "data": file.read_bytes()}


# ---------- open to everyone ----------

@app.get("/api/health")
def health():
    return {"ok": True, "ai_configured": documents.ai_available(), "ai_name": documents.ai_name(), "model": documents.model_label()}


@app.get("/api/meta")
def meta(request: Request):
    reviewer = is_reviewer(request)
    return {
        "stages": [{"key": k, "title": t, "question": q} for k, t, q in pipeline.STAGES],
        "countries": {k: {"name": c["name"], "tax_label": c["tax_label"], "bank_code_label": c["bank_code_label"], "tax_doc_name": c["tax_doc_name"]} for k, c in rules.COUNTRIES.items()},
        "entity_types": intake.ENTITY_TYPES,
        "in_states": sorted(validators.GST_STATES.values()),
        "us_states": sorted(validators.US_STATES.values()),
        "scenarios": [{"key": s["key"], "tag": s["tag"], "title": s["title"], "shows": s["shows"], "expected": s["expected"],
                       "fix_label": s.get("fix_label") if s.get("fix") else None, "submission": s["submission"], "documents": s["documents"]}
                      for s in _scenarios()["scenarios"]] if reviewer else [],
    }


class FormCheck(BaseModel):
    fields: dict
    docs: list[str] = []


@app.post("/api/validate")
def validate_form(body: FormCheck):
    """The same rules the submit endpoints enforce, so the form can mark mistakes before anything is sent."""
    fields = {key: value if isinstance(value, str) else "" for key, value in body.fields.items()}
    return {"errors": intake.validate(fields, {slot for slot in body.docs if slot in intake.DOC_SLOTS})}


@app.post("/api/submissions")
async def submit(request: Request, fields: str = Form(...), prepared: str | None = Form(None),
                 tax_certificate: UploadFile | None = File(None), bank_proof: UploadFile | None = File(None)):
    submission = _parse_fields(fields)
    new_docs = await _read_uploads(tax_certificate, bank_proof)
    _reject_invalid(submission, set(new_docs))
    _check_daily_limit()
    scenario = next((s["key"] for s in _scenarios()["scenarios"] if s["key"] == prepared), None) if is_reviewer(request) else None
    if scenario:  # a reviewer filled the form from a prepared case
        db.remove_prepared_vendors(scenario)
    case_id, token = db.create_case(submission.get("legal_name") or "Unnamed vendor", submission.get("country"), scenario)
    return {**_start_run(case_id, submission, new_docs), "vendor_token": token}


# ---------- the vendor's own application (private link, or a reviewer) ----------

@app.get("/api/vendor/{case_id}")
def vendor_view(case_id: str, request: Request, x_vendor_token: str | None = Header(None)):
    """What the vendor is allowed to see. Internal reasons are deliberately left out."""
    found = _case_for(request, case_id, x_vendor_token)
    latest = db.runs_for_case(case_id)[-1]
    current_docs = db.latest_documents(case_id)
    progress = None
    if found["status"] == "running":  # how far along, without saying what any check found
        finished = {e["stage"] for e in db.events_after(latest["id"], 0) if e["kind"] == "stage_end"}
        progress = {"done": [key for key, _, _ in pipeline.STAGES if key in finished], "total": len(pipeline.STAGES)}
    return {
        "case_id": case_id, "status": found["status"], "waiting_on": found["waiting_on"], "version": found["version"],
        "message": found["message_override"] or latest["message"], "submission": latest["submission"],
        "documents": {slot: {"filename": doc["filename"]} for slot, doc in current_docs.items()},
        "progress": progress, "submitted_at": latest["started_at"],
    }


def _check_can_resubmit(found: dict) -> dict:
    if found["status"] == "running":
        raise HTTPException(409, "This application is still being checked.")
    if found["status"] != "pending" or found["waiting_on"] != "vendor":
        raise HTTPException(409, "This application isn't waiting on the vendor.")
    return found


@app.post("/api/cases/{case_id}/resubmit")
async def resubmit(case_id: str, request: Request, fields: str = Form(...), tax_certificate: UploadFile | None = File(None),
                   bank_proof: UploadFile | None = File(None), x_vendor_token: str | None = Header(None)):
    _check_can_resubmit(_case_for(request, case_id, x_vendor_token))
    submission = _parse_fields(fields)
    new_docs = await _read_uploads(tax_certificate, bank_proof)
    _reject_invalid(submission, set(new_docs) | set(db.latest_documents(case_id)))
    return _start_run(case_id, submission, new_docs)


# ---------- reviewer console ----------

@app.get("/api/stats", dependencies=[Depends(require_reviewer)])
def stats():
    return db.stats()


@app.get("/api/cases", dependencies=[Depends(require_reviewer)])
def cases():
    return db.list_cases()


@app.get("/api/cases/{case_id}", dependencies=[Depends(require_reviewer)])
def case(case_id: str):
    found = db.get_case(case_id)
    if not found:
        raise HTTPException(404, "Case not found.")
    return {"case": found, "runs": db.runs_for_case(case_id), "documents": db.list_documents(case_id), "audit": db.audit_for_case(case_id)}


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_reviewer)])
def run(run_id: int):
    found = db.get_run(run_id)
    if not found:
        raise HTTPException(404, "Run not found.")
    return {"run": found, "events": db.events_after(run_id, 0)}


@app.get("/api/runs/{run_id}/stream", dependencies=[Depends(require_reviewer)])
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


@app.get("/api/documents/{doc_id}", dependencies=[Depends(require_reviewer)])
def document(doc_id: int):
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    safe_name = doc["filename"].replace('"', "")
    return Response(doc["data"], media_type=doc["media_type"], headers={"Content-Disposition": f'inline; filename="{safe_name}"'})


@app.post("/api/samples/{key}/run", dependencies=[Depends(require_reviewer)])
def run_sample(key: str):
    scenario = next((s for s in _scenarios()["scenarios"] if s["key"] == key), None)
    if not scenario:
        raise HTTPException(404, "Unknown scenario.")
    _check_daily_limit()
    db.remove_prepared_vendors(key)
    case_id, token = db.create_case(scenario["submission"]["legal_name"], scenario["submission"]["country"], key)
    started = _start_run(case_id, scenario["submission"], {slot: _sample_doc(path) for slot, path in scenario["documents"].items()})
    return {**started, "vendor_token": token}


@app.post("/api/cases/{case_id}/resubmit-sample", dependencies=[Depends(require_reviewer)])
def resubmit_sample(case_id: str):
    found = db.get_case(case_id)
    if not found:
        raise HTTPException(404, "Case not found.")
    _check_can_resubmit(found)
    scenario = next((s for s in _scenarios()["scenarios"] if s["key"] == found.get("scenario")), None)
    if not scenario or not scenario.get("fix"):
        raise HTTPException(404, "There's no prepared fix for this case.")
    fix = scenario["fix"]
    return _start_run(case_id, {**scenario["submission"], **fix.get("changes", {})}, {slot: _sample_doc(path) for slot, path in fix["documents"].items()})


class ReviewAction(BaseModel):
    action: str
    note: str


@app.post("/api/cases/{case_id}/review", dependencies=[Depends(require_reviewer)])
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
    if body.action == "approve" and found["status"] == "pending" and found["waiting_on"] == "vendor":
        raise HTTPException(409, "The vendor still has to correct their application, so it can't be approved yet. You can reject it, or wait for them to resubmit.")
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


@app.post("/api/reset", dependencies=[Depends(require_reviewer)])
def reset():
    db.init(reset=True)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/samples", StaticFiles(directory=SAMPLES), name="samples")
