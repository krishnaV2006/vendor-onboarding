"""Keeps tests away from real AI services, even when a real key is sitting in .env."""
import os

import dotenv

dotenv.load_dotenv = lambda *_, **__: False
for name in ("VENDOR_AI_PROVIDER", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(name, None)
