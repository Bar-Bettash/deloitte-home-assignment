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
- Free text (typing a question) needs a Gemini API key **and** an admitted model. See [Enable free text](#enable-free-text) below.

## Test

Run these commands from the repository root:

```sh
PYTHONPATH=app/backend python -m pytest app/backend/tests -q   # 843 passed, 1 skipped (one author-local raw input)
node --test app/frontend/tests/*.cjs                           # 130 passed
ruff check app/backend --ignore EXE002,SIM905                  # optional; ruff is not in the requirements
```

The skipped test needs a raw input file that exists only on the original author's machine.

## Configuration

All settings are environment variables. For a local run the backend also reads `backend/.env` (git-ignored); anything already set in your shell wins, and nothing is read from it on Vercel. The table below lists every variable.

| Variable | When it is needed |
|---|---|
| `APP_SIGNING_KEY` | Required when hosted. It must be 32–512 bytes. Generate one with `python -c "import secrets;print(secrets.token_urlsafe(32))"`. |
| `ALLOWED_HOSTS` | Optional. A comma-separated list of hostnames, needed only for a custom domain. Vercel's own hostnames are allowed automatically. |
| `MAX_CONCURRENT_QUERIES` | Optional. The limit is per instance: 1–16, default 4 when hosted. |
| `GEMINI_API_KEY` | Needed for free text and for the live evaluation. A key alone never turns free text on. |
| `GEMINI_MODEL` | Optional. Default `gemini-3.8-flash`, the admitted model. |
| `GEMINI_THINKING_BUDGET` | Optional. Default `0`. `gemini-3.8-flash` may still think briefly, so the output cap is 1,024 tokens. `default` lets the model decide. |
| `MODEL_RUNTIME_ENABLED`, `MODEL_ADMITTED_NAME`, `MODEL_ADMITTED_ADAPTER_SHA256` | Free text only, and only after the model passes the evaluation. See below. |

## Enable free text

Free text is off by default. It turns on only when all of the following are true; otherwise typed questions return `503 ai_unavailable` and presets still work:

- `GEMINI_API_KEY` is set
- `MODEL_RUNTIME_ENABLED=true`
- `MODEL_ADMITTED_NAME` equals the model in use (`GEMINI_MODEL`, or the default `gemini-3.8-flash`)
- `MODEL_ADMITTED_ADAPTER_SHA256` equals `sha256sum app/backend/app/model_adapter.py`

Any change to `model_adapter.py` (its prompt, schema or code) changes the hash and turns free text off again. The model only turns the question into a structured request; every number still comes from the deterministic engine.

1. Create a key at <https://aistudio.google.com/apikey>. Set a quota or budget on its Google project: that is the only spending cap, because the app keeps no record of spend.
2. Local run: create `app/backend/.env` with `GEMINI_API_KEY=<your key>` (plus the admission variables once the model is admitted), then restart the server.

3. From `app/backend/`, run both evaluations against the live model (prices are per million tokens, read from Google's price list on the day). The script needs only the key and makes one call per case:

   ```sh
   # 30-case development corpus: passes at 29/30 or better, every demonstration and safety case, 0 errors
   python -m scripts.eval_intents --mode candidate --live --acceptance \
     --input-usd-per-mtok <price> --output-usd-per-mtok <price>
   # 12-case unseen holdout: passes at 11/12 or better, every safety case, 0 errors
   python -m scripts.eval_intents --mode candidate --live --acceptance --policy holdout \
     --cases tests/fixtures/intent_holdout_2025.json \
     --input-usd-per-mtok <price> --output-usd-per-mtok <price>
   ```

   An omitted `long_haul_share` threshold counts as the documented 3,000-mile default. `aggregate_cost_usd` includes calls whose output the contract rejected; `unknown_cost_calls` counts only calls with no usable provider response.
4. If both pass, set `MODEL_RUNTIME_ENABLED=true`, `MODEL_ADMITTED_NAME` and `MODEL_ADMITTED_ADAPTER_SHA256`, then restart the server.

Each model call writes one log line with the model, outcome, latency and token counts, never the question. A wrong key or model name shows up there as, for example, `outcome=ai_unavailable:http_404`.

## Deploy to Vercel

1. **Project.** Import the repository and set **Root Directory = `app`**.
   - `vercel.json` makes `backend/index.py` the only function, with a 60 s maximum duration, and routes every path to it.
   - The function bundle includes `backend/app`, `backend/data` and `frontend`.
   - There is no `public/` folder, so every file is served through the host and Origin checks.
2. **Environment variables.**
   - Set `APP_SIGNING_KEY` for both Preview and Production. Without it, every route except `/health` returns 503.
   - Add the model variables only after admission. Set `GEMINI_API_KEY` (and optionally `GEMINI_MODEL`) as a **Sensitive** environment variable, plus the three admission variables. It lives only in Vercel's encrypted settings, never in Git; `backend/.env` is not uploaded.
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

- [x] **Admit the live model.** `gemini-3.8-flash` was admitted on 2026-09-30 ([evidence](../docs/evidence/model-admission-20260930/README.md)). The current admitted adapter hash is `1873f9e4304110be438fa69d79f0e09948f863a138a3cdd9fa1be6f327f9451d`; set it with the other admission variables wherever free text should be on (an older hash turns free text off).
- [ ] **Deploy to Vercel.** Follow the section above, then run the smoke test.
- [ ] **Recheck data freshness before the demo.** From `app/backend/`, run `python -m scripts.check_source_status --bundle annual-2025-r1 --output <receipt.json>` on a host that can reach BTS, DataSF and FAA. Re-promoting `annual-2025-r1` after **2026-10-04T17:17Z** requires a new freshness receipt. Serving the current bundle does not depend on this, and the date is not a rebuild deadline: a rebuilt bundle needs its own fresh receipt whenever it is promoted. As of 2026-09-29 the check blocks on FAA, which replaced its preliminary CY2025 PDF with the final edition; see [the FAA impact check](../docs/evidence/faa-final-cy2025-impact-20260929.md).
