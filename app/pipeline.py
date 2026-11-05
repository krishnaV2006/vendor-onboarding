"""Runs one submission through the seven stages, logging every step as it happens."""
import hashlib
import logging
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import checks as rules
from . import db, documents, validators

log = logging.getLogger(__name__)

STAGES = [
    ("intake", "Intake", "Record the submission and compare it with any earlier attempt"),
    ("completeness", "Completeness", "Are all required fields and documents here?"),
    ("format", "Format", "Are the tax IDs and bank codes valid for this country?"),
    ("documents", "Document reading", "Read each document into structured fields"),
    ("cross", "Cross-checks", "Do the form and documents agree with each other?"),
    ("risk", "Risk screening", "Check the vendor master and restricted parties"),
    ("decision", "Decision and message", "Apply the decision rule and tell the vendor"),
]
SLOT_LABELS = {"tax_certificate": "Tax document", "bank_proof": "Bank document"}
# Keeps instant rule stages on screen long enough to follow. It adds no logic and can be set to 0.
MIN_STAGE_MS = int(os.getenv("VENDOR_MIN_STAGE_MS", "450"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Stage:
    """Times a stage and reports its checks to the live event log."""

    def __init__(self, run_id: int, key: str, collected: list):
        self.run_id, self.key, self.collected = run_id, key, collected
        self.started = time.monotonic()
        db.add_event(run_id, "stage_start", key, {})

    def report(self, items: list[rules.Check]) -> None:
        for check in items:
            self.collected.append(check)
            db.add_event(self.run_id, "check", self.key, check.to_dict())

    def finish(self) -> None:
        elapsed_ms = (time.monotonic() - self.started) * 1000
        if elapsed_ms < MIN_STAGE_MS:
            time.sleep((MIN_STAGE_MS - elapsed_ms) / 1000)
        results = [c.result for c in self.collected]
        db.add_event(self.run_id, "stage_end", self.key, {"ms": round((time.monotonic() - self.started) * 1000)})
        return results


def _read_all(run_id: int, docs: dict, prior_reads: dict, reader) -> dict:
    """Reads every new document at once. A document the vendor didn't replace keeps its earlier reading."""
    reads, to_read = {}, {}
    for slot, doc in docs.items():
        prior = prior_reads.get(slot)
        if prior and prior.get("sha256") == doc["sha256"] and prior["reader"] != "none":
            reads[slot] = {**prior, "reused": True, "ms": 0}
            db.add_event(run_id, "doc_read", "documents", {"slot": slot, "label": SLOT_LABELS[slot], "filename": doc["filename"], **reads[slot]})
        else:
            to_read[slot] = doc
            db.add_event(run_id, "doc_reading", "documents", {"slot": slot, "label": SLOT_LABELS[slot], "filename": doc["filename"]})

    def read_one(slot, doc):
        started = time.monotonic()
        result = reader(doc["data"], doc["media_type"])
        return slot, result, round((time.monotonic() - started) * 1000)

    if to_read:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(read_one, slot, doc) for slot, doc in to_read.items()]
            for future in as_completed(futures):
                slot, result, ms = future.result()
                reads[slot] = {**result.model_dump(), "sha256": to_read[slot]["sha256"], "ms": ms, "reused": False}
                db.add_event(run_id, "doc_read", "documents", {"slot": slot, "label": SLOT_LABELS[slot], "filename": to_read[slot]["filename"], **reads[slot]})
    return reads


def execute(run_id: int, case_id: str, version: int, sub: dict, docs: dict, reader=None, intro_writer=None) -> None:
    reader = reader or documents.read_document
    intro_writer = intro_writer or documents.draft_intro
    started = time.monotonic()
    all_checks: list[rules.Check] = []
    try:
        previous = db.previous_run(case_id, version)

        stage = Stage(run_id, "intake", all_checks)
        stage.report([rules.Check("recorded", "Submission recorded", rules.PASS,
                                  f"Attempt {version} for {sub.get('legal_name') or 'an unnamed vendor'}, with {len(docs)} document(s).")])
        stage.report(rules.compare_versions(previous["submission"] if previous else None, sub))
        stage.finish()

        stage = Stage(run_id, "completeness", all_checks)
        stage.report(rules.completeness(sub, docs))
        stage.finish()

        stage = Stage(run_id, "format", all_checks)
        stage.report(rules.formats(sub))
        stage.finish()

        stage = Stage(run_id, "documents", all_checks)
        reads = _read_all(run_id, docs, (previous or {}).get("reads") or {}, reader)
        stage.report(rules.read_quality("Tax document", reads.get("tax_certificate"), "tax_doc"))
        stage.report(rules.read_quality("Bank document", reads.get("bank_proof"), "bank_doc"))
        stage.finish()

        stage = Stage(run_id, "cross", all_checks)
        stage.report(rules.cross_checks(sub, reads))
        stage.finish()

        stage = Stage(run_id, "risk", all_checks)
        stage.report(rules.risk(sub, db.vendors(), db.watchlist(), case_id))
        stage.finish()

        stage = Stage(run_id, "decision", all_checks)
        decision = rules.decide(all_checks)
        db.add_event(run_id, "decision", "decision", decision)
        intro = None
        if decision["status"] == "pending" and decision["waiting_on"] == "vendor":
            # The AI only ever sees what the vendor is allowed to see.
            verified = [c.name for c in all_checks if c.result == rules.PASS][:8]
            needed = [c.name for c in all_checks if c.result == rules.FIX]
            intro = intro_writer(sub.get("legal_name") or "the vendor", verified, needed)
        message = rules.build_message(decision, all_checks, sub, intro)
        message["intro_source"] = "ai" if intro else "template"
        message["intro_by"] = documents.ai_name() if intro else None
        db.add_event(run_id, "message", "decision", message)
        stage.finish()

        if decision["status"] == "approved":
            bank = sub.get("bank_code") if sub.get("country") == "GB" else sub.get("account_number")
            db.add_vendor(sub.get("legal_name"), validators.clean(sub.get("tax_id")), validators.clean(bank), case_id)

        duration_ms = round((time.monotonic() - started) * 1000)
        db.finish_run(run_id, case_id, reads, [c.to_dict() for c in all_checks], decision, message, duration_ms)
        waiting = f", waiting on {decision['waiting_on']}" if decision["waiting_on"] else ""
        db.add_audit(case_id, "process", f"Attempt {version}: {decision['status']}{waiting}",
                     f"{decision['counts']['pass']} passed, {decision['counts']['fix']} to fix, {decision['counts']['review']} for review, {decision['counts']['block']} blocking")
        db.add_audit(case_id, "process", "Message posted to the vendor's page", message["subject"])
        db.add_event(run_id, "run_end", None, {"status": decision["status"], "waiting_on": decision["waiting_on"], "duration_ms": duration_ms})
    except Exception as exc:  # a broken run must still leave the case with someone responsible for it
        log.error("Run %s failed: %s", run_id, traceback.format_exc())
        message = rules.build_message({"status": "pending", "waiting_on": "internal"}, [], sub)
        message["intro_source"] = "template"
        db.fail_run(run_id, case_id, message, str(exc))
        db.add_audit(case_id, "process", f"Attempt {version}: error, sent to a person", str(exc))
        db.add_event(run_id, "run_end", None, {"status": "pending", "waiting_on": "internal", "error": str(exc)})
