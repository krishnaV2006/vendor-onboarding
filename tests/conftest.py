"""Keeps tests away from real AI services and the real database, even when .env holds real settings."""
import os
import tempfile
from pathlib import Path

import dotenv

dotenv.load_dotenv = lambda *_, **__: False
for name in ("VENDOR_AI_PROVIDER", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(name, None)
os.environ["VENDOR_DB"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["VENDOR_MIN_STAGE_MS"] = "0"
os.environ["REVIEWER_PASSCODE"] = "test-passcode"
