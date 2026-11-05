"""Records real runs of the prepared cases into seed/demo_history.db, using the AI key in .env.

The app copies this file into place when its disk is empty, so the dashboard still has
history after a free host restarts. Run it again after any change to the database schema.
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "seed" / "demo_history.db"
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
SNAPSHOT.parent.mkdir(exist_ok=True)
for leftover in SNAPSHOT.parent.glob(f"{SNAPSHOT.name}*"):
    leftover.unlink()
os.environ["VENDOR_DB"] = str(SNAPSHOT)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
RESUBMIT = {"personal_account", "gstin_conflict"}  # the others stay open, so the dashboard shows every status


def finish(run_id: int) -> dict:
    while True:
        run = client.get(f"/api/runs/{run_id}").json()["run"]
        if run["state"] != "running":
            readers = {read["reader"] for read in run["reads"].values()}
            if readers != {"gemini"}:
                sys.exit(f"Stopped: run {run_id} was read by {readers}. The recording must use real AI.")
            return run
        time.sleep(0.3)


for scenario in client.get("/api/meta").json()["scenarios"]:
    started = client.post(f"/api/samples/{scenario['key']}/run").json()
    run = finish(started["run_id"])
    print(f"{scenario['title']}: {run['status']}")
    if run["status"] != scenario["expected"]:
        sys.exit(f"Stopped: expected {scenario['expected']}.")
    if scenario["key"] in RESUBMIT:
        run = finish(client.post(f"/api/cases/{started['case_id']}/resubmit-sample").json()["run_id"])
        print(f"  after resubmitting: {run['status']}")

with sqlite3.connect(SNAPSHOT) as conn:  # fold everything into one file
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
print(f"Saved {SNAPSHOT} ({SNAPSHOT.stat().st_size // 1024} KB)")
