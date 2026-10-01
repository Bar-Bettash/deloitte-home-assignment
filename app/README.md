# Run and deploy

Operational instructions for the application. For the product overview, see the [root README](../README.md).

## Local run

Python 3.12 is the deployment target.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r app/backend/requirements-dev.txt
python -m uvicorn app.main:app --app-dir app/backend --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/.

The accepted data snapshots ship with the repository; runtime analysis does not download provider data.

## Environment

| Variable | Purpose |
|---|---|
| `APP_SIGNING_KEY` | Signs application context; required when hosted |
| `APP_ACCESS_PASSWORD` | Shared demo-access password |
| `ALLOWED_HOSTS` | Optional custom-host allowlist |
| `MAX_CONCURRENT_QUERIES` | Optional per-instance concurrency limit |
| `GEMINI_API_KEY` | Gemini free-text interpretation |
| `GEMINI_MODEL` | Admitted Gemini model; currently `gemini-3.8-flash` |
| `GEMINI_THINKING_BUDGET` | Optional model thinking setting |
| `MODEL_RUNTIME_ENABLED` | Enables admitted free-text runtime |
| `MODEL_ADMITTED_NAME` | Exact admitted model name |
| `MODEL_ADMITTED_ADAPTER_SHA256` | Exact admitted `model_adapter.py` hash |

Never commit secret values. Hosted secrets belong in Vercel environment settings.

Free text is enabled only when the Gemini key, runtime flag, admitted model name, and adapter SHA all match. Presets and deterministic analysis remain independent of the model.

Voice uses browser speech APIs and needs no backend key.

## Tests

From the repository root:

```sh
PYTHONPATH=app/backend python -m pytest app/backend/tests -q
node --test app/frontend/tests/*.cjs
```

Optional lint:

```sh
ruff check app/backend --ignore EXE002,SIM905
```

The latest RC2 evidence is summarized in [docs/VALIDATION.md](../docs/VALIDATION.md).

## Vercel

- Project Root Directory: `app`
- Python: 3.12
- `vercel.json` routes the app through `backend/index.py`
- Set hosted environment variables in Vercel; do not upload local `.env` files
- Validate on a protected Preview before Production
- Production URL: https://deloitte-airport-analyst.vercel.app

Minimum smoke test:

1. Sign in with the shared password.
2. Run the four assignment presets.
3. Ask one natural-language follow-up.
4. Use **Explain this result**.
5. Test voice/read-aloud where supported.
6. Sign out and confirm analysis endpoints are locked again.

## Data refresh

Data ingestion and bundle acceptance are offline operator tasks under `app/backend/app/sources/` and `app/backend/scripts/`.

The runtime reads only accepted, hash-verified snapshots. For methodology and data flow, see [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).
