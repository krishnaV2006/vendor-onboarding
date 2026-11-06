"""SQLite storage: cases, runs, the live event log, documents and the audit trail."""
import json
import os
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("VENDOR_DB", ROOT / "data" / "vendor.db"))
# Real runs recorded by scripts/make_demo_history.py. Regenerate it after any schema change.
HISTORY_PATH = ROOT / "seed" / "demo_history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY, vendor_name TEXT, country TEXT, status TEXT, waiting_on TEXT,
    version INTEGER, scenario TEXT, created_at TEXT, updated_at TEXT, message_override TEXT,
    vendor_token TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, version INTEGER, state TEXT, status TEXT,
    waiting_on TEXT, submission TEXT, reads TEXT, checks TEXT, decision TEXT, message TEXT,
    started_at TEXT, finished_at TEXT, duration_ms INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, ts TEXT, kind TEXT, stage TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, version INTEGER, slot TEXT, filename TEXT,
    media_type TEXT, sha256 TEXT, data BLOB
);
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, tax_id TEXT, bank_account TEXT, case_id TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (name TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts TEXT, actor TEXT, action TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
"""

SEED_VENDORS = [
    ("Orion Traders Pvt Ltd", "27AABCO1234E1ZL", "918020045671234"),
    ("Meridian Packaging Pvt Ltd", "29AACCM5521B1Z3", "50200011223344"),
    ("Lakeshore Office Supply LLC", "36-4417725", "882014773201"),
    ("Greenfield Chemicals Ltd", "GB314159267", "GB29NWBK60161331926819"),
    ("Sundar Electricals", "33AAFPS7788H1ZK", "6021100045789"),
    ("Pinnacle IT Services Pvt Ltd", "07AADCP9123Q1Z9", "004501552200"),
    ("Harbor Freight Partners Inc", "91-2288341", "334455667788"),
]
SEED_WATCHLIST = [
    ("Northgate Trading FZE", "sanctions list, illustrative entry"),
    ("Volkov Marine Services", "sanctions list, illustrative entry"),
    ("Crescent Star Exports", "internal debarment, illustrative entry"),
    ("Redline Commodities Ltd", "sanctions list, illustrative entry"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init(reset: bool = False) -> None:
    if not reset and not DB_PATH.exists() and HISTORY_PATH.exists():
        # A free host wipes its disk on every restart, so bring back the recorded runs.
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HISTORY_PATH, DB_PATH)
    with connect() as conn:
        if reset:
            for table in ("cases", "runs", "events", "documents", "vendors", "watchlist", "audit"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)
        if "vendor_token" not in {row["name"] for row in conn.execute("PRAGMA table_info(cases)")}:
            conn.execute("ALTER TABLE cases ADD COLUMN vendor_token TEXT")  # databases recorded before private vendor links
        if not conn.execute("SELECT 1 FROM vendors LIMIT 1").fetchone():
            conn.executemany("INSERT INTO vendors (name, tax_id, bank_account, case_id) VALUES (?,?,?,NULL)", SEED_VENDORS)
        if not conn.execute("SELECT 1 FROM watchlist LIMIT 1").fetchone():
            conn.executemany("INSERT INTO watchlist VALUES (?,?)", SEED_WATCHLIST)


def _decode(row: sqlite3.Row | None, *json_fields: str) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for field in json_fields:
        item[field] = json.loads(item[field]) if item.get(field) else None
    return item


RUN_JSON = ("submission", "reads", "checks", "decision", "message")


def create_case(vendor_name: str, country: str, scenario: str | None) -> tuple[str, str]:
    """Returns the case ID and the vendor's private token, which their status link carries."""
    case_id = "VND-" + uuid.uuid4().hex[:6].upper()
    token = secrets.token_urlsafe(18)
    with connect() as conn:
        conn.execute("""INSERT INTO cases (id, vendor_name, country, status, waiting_on, version, scenario, created_at, updated_at, vendor_token)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (case_id, vendor_name, country, "running", None, 0, scenario, now(), now(), token))
    return case_id, token


def create_run(case_id: str, submission: dict) -> tuple[int, int]:
    with connect() as conn:
        version = conn.execute("SELECT version FROM cases WHERE id=?", (case_id,)).fetchone()["version"] + 1
        cur = conn.execute("INSERT INTO runs (case_id, version, state, submission, started_at) VALUES (?,?,?,?,?)",
                           (case_id, version, "running", json.dumps(submission), now()))
        conn.execute("UPDATE cases SET version=?, status='running', waiting_on=NULL, message_override=NULL, vendor_name=?, country=?, updated_at=? WHERE id=?",
                     (version, submission.get("legal_name") or "Unnamed vendor", submission.get("country"), now(), case_id))
    return cur.lastrowid, version


def finish_run(run_id: int, case_id: str, reads: dict, checks: list[dict], decision: dict, message: dict, duration_ms: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE runs SET state='done', status=?, waiting_on=?, reads=?, checks=?, decision=?, message=?, finished_at=?, duration_ms=? WHERE id=?",
                     (decision["status"], decision["waiting_on"], json.dumps(reads), json.dumps(checks), json.dumps(decision), json.dumps(message), now(), duration_ms, run_id))
        conn.execute("UPDATE cases SET status=?, waiting_on=?, updated_at=? WHERE id=?",
                     (decision["status"], decision["waiting_on"], now(), case_id))


def fail_run(run_id: int, case_id: str, message: dict, error: str) -> None:
    decision = {"status": "pending", "waiting_on": "internal", "counts": {}, "reasons": [f"The process hit an unexpected error, so a person needs to check this by hand: {error}"]}
    with connect() as conn:
        conn.execute("UPDATE runs SET state='error', status='pending', waiting_on='internal', decision=?, message=?, finished_at=? WHERE id=?",
                     (json.dumps(decision), json.dumps(message), now(), run_id))
        conn.execute("UPDATE cases SET status='pending', waiting_on='internal', updated_at=? WHERE id=?", (now(), case_id))


def get_run(run_id: int) -> dict | None:
    with connect() as conn:
        return _decode(conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone(), *RUN_JSON)


def get_case(case_id: str) -> dict | None:
    with connect() as conn:
        return _decode(conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone(), "message_override")


def runs_for_case(case_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM runs WHERE case_id=? ORDER BY version", (case_id,)).fetchall()
    return [_decode(r, *RUN_JSON) for r in rows]


def previous_run(case_id: str, version: int) -> dict | None:
    with connect() as conn:
        return _decode(conn.execute("SELECT * FROM runs WHERE case_id=? AND version<? ORDER BY version DESC LIMIT 1", (case_id, version)).fetchone(), *RUN_JSON)


def list_cases() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("""SELECT c.*, r.id AS latest_run_id, r.duration_ms,
                                      (SELECT MIN(started_at) FROM runs WHERE case_id=c.id) AS first_run_at
                               FROM cases c LEFT JOIN runs r ON r.case_id=c.id AND r.version=c.version
                               ORDER BY c.updated_at DESC""").fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT status, waiting_on, COUNT(*) AS n FROM cases GROUP BY status, waiting_on").fetchall()
        runs = conn.execute("SELECT COUNT(*) AS n, AVG(duration_ms) AS avg_ms FROM runs WHERE state='done'").fetchone()
        vendors = conn.execute("SELECT COUNT(*) AS n FROM vendors").fetchone()["n"]
    out = {"total": 0, "approved": 0, "pending_vendor": 0, "pending_internal": 0, "rejected": 0, "running": 0}
    for row in rows:
        out["total"] += row["n"]
        key = f"pending_{row['waiting_on']}" if row["status"] == "pending" else row["status"]
        if key in out:
            out[key] += row["n"]
    out.update(runs=runs["n"], avg_run_ms=round(runs["avg_ms"] or 0), vendors_on_file=vendors)
    return out


def add_event(run_id: int, kind: str, stage: str | None, payload: dict) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO events (run_id, ts, kind, stage, payload) VALUES (?,?,?,?,?)",
                     (run_id, now(), kind, stage, json.dumps(payload)))


def events_after(run_id: int, last_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, last_id)).fetchall()
    return [_decode(r, "payload") for r in rows]


def add_document(case_id: str, version: int, slot: str, filename: str, media_type: str, sha256: str, data: bytes) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO documents (case_id, version, slot, filename, media_type, sha256, data) VALUES (?,?,?,?,?,?,?)",
                     (case_id, version, slot, filename, media_type, sha256, data))


def latest_documents(case_id: str) -> dict:
    """The newest upload for each slot, so documents the vendor didn't replace carry forward."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM documents WHERE case_id=? ORDER BY version", (case_id,)).fetchall()
    return {r["slot"]: dict(r) for r in rows}


def list_documents(case_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, version, slot, filename, media_type, sha256 FROM documents WHERE case_id=? ORDER BY version, slot", (case_id,)).fetchall()
    return [dict(r) for r in rows]


def get_document(doc_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    return dict(row) if row else None


def vendors() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM vendors ORDER BY id").fetchall()]


def watchlist() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM watchlist").fetchall()]


def add_vendor(name: str, tax_id: str, bank_account: str, case_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM vendors WHERE case_id=?", (case_id,))
        conn.execute("INSERT INTO vendors (name, tax_id, bank_account, case_id) VALUES (?,?,?,?)", (name, tax_id, bank_account, case_id))


def remove_prepared_vendors(scenario: str) -> None:
    """Lets a prepared case run again on a shared demo without colliding with its own earlier approval."""
    with connect() as conn:
        conn.execute("DELETE FROM vendors WHERE case_id IN (SELECT id FROM cases WHERE scenario=?)", (scenario,))


def remove_vendor(case_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM vendors WHERE case_id=?", (case_id,))


def add_audit(case_id: str, actor: str, action: str, detail: str = "") -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit (case_id, ts, actor, action, detail) VALUES (?,?,?,?,?)", (case_id, now(), actor, action, detail))


def audit_for_case(case_id: str) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM audit WHERE case_id=? ORDER BY id", (case_id,)).fetchall()]


def set_reviewer_outcome(case_id: str, status: str, message: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE cases SET status=?, waiting_on=NULL, message_override=?, updated_at=? WHERE id=?",
                     (status, json.dumps(message), now(), case_id))


def runs_today() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM runs WHERE started_at >= ?", (now()[:10],)).fetchone()["n"]
