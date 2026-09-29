# Running the app

This folder holds the runnable application. For the design, the methodology and the reasons behind them, read the [root README](../README.md).

| Path | Contents |
|---|---|
| `backend/app/` | FastAPI service: `main.py` (routes, host and Origin guard), `contracts.py`, `dispatch.py`, `calculations/`, `model_adapter.py`, `context_token.py`, `settings.py` |
| `backend/data/` | Accepted Parquet snapshots, their manifests, the bundle registry and curated terminal evidence |
| `backend/app/sources/`, `backend/scripts/` | Offline ingestion, source checks, reconciliation and bundle acceptance. None of these run when a user asks a question. |
| `backend/tests/` | Python tests |
| `frontend/` | Static chat UI (`index.html`, `app.js`, `globe.js`, `styles.css`, `assets/`), served by the backend under `/static`. There is no build step. |
| `frontend/tests/` | Node UI tests |
| `vercel.json`, `.vercelignore`, `.python-version` | Vercel packaging. The Vercel Root Directory is this folder. |

## Run locally

You need Python 3.11 or 3.12. The data ships with the repository, so nothing needs to be downloaded. Run these commands from the repository root:

```sh
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r app/backend/requirements-dev.txt
python -m uvicorn app.main:app --app-dir app/backend --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/>. The presets, the "Adjust scope" controls and "Explain this result" work without any configuration.

- `GET /health` only shows that the server is running.
- A local run needs no environment variables. The signing key for follow-up context is then generated fresh each time the server starts, so a restart ends any follow-up context.
- Free text needs an admitted model. See [Enable free text](#enable-free-text) below.

## Test

Run these commands from the repository root:

```sh
PYTHONPATH=app/backend python -m pytest app/backend/tests -q   # 722 passed, 2 skipped
node --test app/frontend/tests/*.cjs                           # 102 passed
ruff check app/backend --ignore EXE002,SIM905                  # optional; ruff is not in the requirements
```

The two skipped tests need raw input files that exist only on the original author's machine.

## Configuration

All settings are environment variables. The app never reads a `.env` file itself. [backend/.env.example](backend/.env.example) lists every variable, with placeholders.

| Variable | When it is needed |
|---|---|
| `APP_SIGNING_KEY` | Required when hosted. It must be 32–512 bytes. Generate one with `python -c "import secrets;print(secrets.token_urlsafe(32))"`. |
| `ALLOWED_HOSTS` | Optional. A comma-separated list of hostnames, needed only for a custom domain. Vercel's own hostnames are allowed automatically. |
| `MAX_CONCURRENT_QUERIES` | Optional. The limit is per instance: 1–16, default 4 when hosted. |
| `OPENAI_API_KEY`, `OPENAI_MODEL`, `MODEL_RUNTIME_ENABLED`, `MODEL_ADMITTED_NAME`, `MODEL_ADMITTED_ADAPTER_SHA256`, `MODEL_REASONING_EFFORT` | Free text only, and only after admission. |

## Enable free text

Free text is off by default. It turns on only when all of the following are true:

- `MODEL_RUNTIME_ENABLED=true`
- `OPENAI_API_KEY` and `OPENAI_MODEL` are both set
- `MODEL_ADMITTED_NAME` equals `OPENAI_MODEL`
- `MODEL_ADMITTED_ADAPTER_SHA256` equals `sha256sum app/backend/app/model_adapter.py`

To admit a model:

1. Create an OpenAI project and give it a **hard monthly budget**. That budget is the only spending cap, because the app keeps no record of spend.
2. From `app/backend/`, run the live evaluation. It passes at 29 of 30 or better. The network must allow `api.openai.com`.

   ```sh
   python -m scripts.eval_intents --mode candidate --live --acceptance \
     --input-usd-per-mtok <price> --output-usd-per-mtok <price>
   ```

3. If the evaluation passes, set the admission variables. Any change to `model_adapter.py` changes its hash and turns free text off again.

## Deploy to Vercel

1. **Project.** Import the repository and set **Root Directory = `app`**.
   - `vercel.json` makes `backend/index.py` the only function, with a 60 s maximum duration, and routes every path to it.
   - The function bundle includes `backend/app`, `backend/data` and `frontend`.
   - There is no `public/` folder, so every file is served through the host and Origin checks.
2. **Environment variables.**
   - Set `APP_SIGNING_KEY` for both Preview and Production. Without it, every route except `/health` returns 503.
   - Add the model variables only after admission. Put the key in Vercel's encrypted settings, never in Git.
3. **Check these before the first deploy.** They could not be verified offline; details are in [vercel-package-check.md](../docs/evidence/vercel-package-check.md).
   - Is `requirements.txt` found next to the entrypoint (`backend/`)? If not, copy or reference it at `app/requirements.txt`.
   - Is Python 3.12 selected from `.python-version`?
   - Do `builds`, `routes`, `maxDuration` and `includeFiles` still work as configured?
   - Does the bundle, about 82 MiB unzipped, fit under the function size limit?
   - Are the `VERCEL_*` host variables present at runtime?
   - Are `no-store` responses kept out of the edge cache?
4. **Smoke test on a preview deployment.** Check the following, then promote to production:
   - `/health` responds.
   - The four presets return results.
   - "Explain this result" works.
   - A cross-origin POST is refused.

## What's left

- [ ] **Admit the live model.** Follow [Enable free text](#enable-free-text), save the result without secrets under `docs/evidence/`, and demo the four questions as free text plus one follow-up.
- [ ] **Deploy to Vercel.** Follow the section above, then run the smoke test.
- [ ] **Recheck data freshness before the demo.** From `app/backend/`, run `python -m scripts.check_source_status --bundle annual-2025-r1 --output <receipt.json>` on a host that can reach BTS, DataSF and FAA. Promoting a bundle again after **2026-10-04T17:17Z** requires a new freshness receipt. Serving the current bundle does not depend on this.
