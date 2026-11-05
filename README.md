# Vendor onboarding

Takes a vendor's application, form fields and documents, and produces a status of **approved**, **pending** or **rejected**, with the reasoning visible and a message back to the vendor.

AI reads the documents, fixed rules make the decision, and every step is logged. See [the process map](docs/PROCESS_MAP.md) and [the assumptions](docs/ASSUMPTIONS.md).

## Run it locally

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --port 8010
```

Then open http://localhost:8010.

To turn on AI reading, copy `.env.example` to `.env` and add a Gemini API key, which is free from Google AI Studio. Put keys only in `.env`, which git ignores, never in `.env.example`. Without a key, a backup text parser reads text PDFs and scanned documents go to a person.

`VENDOR_AI_PROVIDER` picks the reader: `gemini`, `claude` or `off`. Claude needs `ANTHROPIC_API_KEY`. It has only been tested against a stand-in server, never with a real key.

## Test it

```bash
.venv/Scripts/python -m pytest -q
```

The tests run every prepared case through the real API. They use the backup parser and stand-in AI servers, so they need no key, cost nothing and ignore any key in `.env`.

## How it's built

| Part | Choice | Why |
|---|---|---|
| Backend | Python, FastAPI | One small service, easy to host |
| Storage | SQLite | Enough for a demo. Cases, attempts, events and audit trail live in one file |
| Live view | Server-sent events, with a slow poll as backup | Shows each stage as it happens, even on hosts that buffer streams |
| Document reading | Gemini's free tier, with structured output. Claude is available as a switch | Reads scans and messy layouts, and can only fill in fixed fields. If a free model is busy or slow, the next one starts as well and the first good answer wins |
| Decision | Plain Python rules in `app/checks.py` | Predictable, testable and explainable |
| Screens | Plain HTML, CSS and JavaScript | No build step |

| File | What it does |
|---|---|
| `app/pipeline.py` | Runs the seven stages and logs each step |
| `app/checks.py` | The rulebook, the decision rule and the vendor messages |
| `app/validators.py` | Tax ID and bank code formats and checksums |
| `app/names.py` | Compares company and account-holder names |
| `app/documents.py` | Reads documents with Gemini or Claude, or the backup parser |
| `scripts/make_samples.py` | Generates the prepared cases and their PDFs |

## Host it

`render.yaml` sets the app up on Render's free plan. Create a Blueprint from this repository and enter `GEMINI_API_KEY` when asked. The `Dockerfile` also runs on any other host that supports containers.

The free plan sleeps after 15 minutes without traffic, takes about a minute to wake, and wipes its disk each time. To keep the dashboard useful, the app restores real runs recorded in `seed/demo_history.db` whenever its disk is empty. Run `scripts/make_demo_history.py` to record them again, which is required after any database schema change.

`VENDOR_DB` can point to a persistent disk on a paid plan, and `VENDOR_MAX_RUNS_PER_DAY` caps use of a public link (300 by default).
