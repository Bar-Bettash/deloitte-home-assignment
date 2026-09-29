# Airport Investment Intelligence Agent

This is a home-assignment prototype for the Deloitte FDE exam brief ([FDE Exam 2.pdf](FDE%20Exam%202.pdf)). It is an analyst chat screen that answers four airport-investment questions from public US aviation data:

- Which New England airports are strong candidates for terminal expansion?
- How do LAX and Santa Ana (SNA) compare on congestion?
- What share of Anchorage (ANC) departures are long-haul?
- What is SFO's unmet flight demand, and why?

All numbers come from deterministic Python over packaged, hash-verified snapshots. AI appears in one place only: a single model call that turns free text into a strict structured request.

**The design and architecture deliverable is [docs/DESIGN.md](docs/DESIGN.md).** It covers the scoring methodology, key tradeoffs, where and how AI is used, and the assumptions and limits.

## Status

| Item | Status |
|---|---|
| Four workflows, follow-ups and error paths | **Locally tested** ([integrated acceptance, 2026-09-29](backend/docs/evidence/integrated-acceptance-20260929.md)) |
| Packaged data vs official sources | **Live source verified on 2026-09-27** ([activation review](backend/docs/evidence/recent-data-activation-review.md)); not re-checked since |
| Free-text model interpretation | **Not yet live-verified.** Free text returns `503 ai_unavailable` until the model is admitted; presets work regardless. |
| Hosted deployment (Vercel) | **Not yet deployed.** The package was checked offline only ([package check](backend/docs/evidence/vercel-package-check.md)). |

What remains to be done is listed in [TODO.md](TODO.md).

## Quick start (local)

You need Python 3.11 or 3.12. `backend/.python-version` pins 3.12, and `pyproject.toml` allows `>=3.11,<3.13`. Node is optional; it is needed only for the UI tests. The data ships with the repository, so nothing needs to be downloaded.

```sh
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt     # runtime pins + pytest
python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ and use the presets or the "Adjust scope" controls. `GET /health` returns `{"status":"ok"}`. That response shows the server is reachable, not that the data or the model is ready.

A local loopback run needs no environment variables. The signing key for follow-up context is then generated per process, so restarting the server ends any existing follow-up context. For the settings that turn on free text, see [backend/.env.example](backend/.env.example) and the section on enabling free text below.

## Tests

Run these from the repository root:

```sh
PYTHONPATH=backend python -m pytest backend/tests -q
node --test backend/tests/*.cjs
ruff check backend --ignore EXE002,SIM905     # optional; ruff is not in the requirements
```

Recorded results:

| Revision | Python tests | Node UI tests | Other |
|---|---|---|---|
| `104acfc` ([integrated acceptance](backend/docs/evidence/integrated-acceptance-20260929.md)) | 583 passed, 2 skipped | 88/88 | 41 real-HTTP calls matched the evidence figures |
| `fbd2342` (rerun during the docs update) | 679 passed, 2 skipped | 96/96 | — |

The two skipped tests need raw external inputs that exist only on the original author's machine.

## Repository map

| Path | What it holds |
|---|---|
| `backend/app/main.py` | FastAPI app: the host/Origin guard, `POST /api/query` and static UI serving |
| `backend/app/contracts.py` | Strict request, result and error models |
| `backend/app/dispatch.py` | Deterministic routing from a request to the calculations |
| `backend/app/calculations/` | Screen, traffic, operations, comparison, long-haul and SFO calculations |
| `backend/app/model_adapter.py` | The single OpenAI Responses API call for free text |
| `backend/app/context_token.py` | The signed, stateless `airport_context` follow-up cookie |
| `backend/app/settings.py` | Validated model and hosting settings |
| `backend/app/sources/`, `backend/scripts/` | Offline ingestion, qualification and bundle acceptance; never run on a user request |
| `backend/data/` | Accepted Parquet snapshots, manifests, the bundle registry and curated evidence |
| `backend/app/static/` | The static chat UI (no build step) |
| `backend/index.py`, `backend/vercel.json`, `backend/.vercelignore` | Vercel packaging |

More detail:

- [backend/docs/ARCHITECTURE.md](backend/docs/ARCHITECTURE.md): components, numerical definitions and data refresh.
- [docs/API_UI_MAP.md](docs/API_UI_MAP.md): the wire contract and how the UI uses it.
- [backend/docs/evidence/](backend/docs/evidence/): verification records.
- [backend/docs/specs/](backend/docs/specs/) and [docs/frontend/](docs/frontend/): historical planning documents. They are superseded where they conflict with the code and [docs/DESIGN.md](docs/DESIGN.md).

## Enabling free text (model admission)

Free text is disabled by default and fails closed. Every one of the following must hold before a message reaches the model:

- `MODEL_RUNTIME_ENABLED=true`.
- `OPENAI_API_KEY` and `OPENAI_MODEL` are both set.
- `MODEL_ADMITTED_NAME` equals `OPENAI_MODEL`.
- `MODEL_ADMITTED_ADAPTER_SHA256` equals the SHA-256 of `backend/app/model_adapter.py`.

To admit a model:

1. Create an OpenAI project and set a **hard monthly budget** on it. This budget is the only spending cap, because the app keeps no spend ledger.
2. Run the live evaluation from `backend/`. It passes at 29/30 or better.

   ```sh
   python scripts/eval_intents.py --mode candidate --live --acceptance \
     --input-usd-per-mtok <price> --output-usd-per-mtok <price>
   ```

   Read the per-token prices from OpenAI on the day of the run. The network must allow `api.openai.com`.
3. If the evaluation passes, set the four admission variables. Get the adapter hash with `sha256sum backend/app/model_adapter.py`. Any edit to that file invalidates the admission.

## Deploying to Vercel (not yet done)

1. **Project.** Import the repository and set **Root Directory = `backend`**.
   - `vercel.json` pins `index.py` as the only function, with `maxDuration` 60 s, and routes every path to it.
   - There is no `public/` directory, so no file is served around the host and Origin checks.
2. **Environment variables.** Set these for Preview and Production:
   - `APP_SIGNING_KEY` is **required**. It must be 32 to 512 bytes. Generate one with `python -c "import secrets;print(secrets.token_urlsafe(32))"`. Without it, hosted mode returns 503 on every route except `/health`. Rotating it invalidates all follow-up cookies.
   - `ALLOWED_HOSTS` is optional. It is a comma-separated list of hostnames and is needed only for custom domains. Vercel's own `VERCEL_URL`, `VERCEL_BRANCH_URL` and `VERCEL_PROJECT_PRODUCTION_URL` hostnames are allowed automatically.
   - `MAX_CONCURRENT_QUERIES` is optional. It applies per instance: the default is 4 when hosted, and the allowed range is 1 to 16.
   - Model variables are needed only after admission: `OPENAI_API_KEY`, `OPENAI_MODEL`, `MODEL_RUNTIME_ENABLED`, `MODEL_ADMITTED_NAME`, `MODEL_ADMITTED_ADAPTER_SHA256` and, optionally, `MODEL_REASONING_EFFORT`. Put the key in Vercel's encrypted environment settings, never in Git.
3. **Cost control.** There is no login, by the owner's decision. Model spend is bounded per call and capped overall by the OpenAI project's hard budget.
   - The per-call bounds are a 4,000-character question, 512 output tokens, a 20 s model timeout and a 30 s query deadline.
4. **RE-VERIFY before the first deploy.** These points come from [vercel-package-check.md](backend/docs/evidence/vercel-package-check.md) and could not be checked because the network was blocked:
   1. `@vercel/python` still accepts `builds` and `routes` and honours `maxDuration` and `includeFiles`. If it does not, use the fallback layout (`api/index.py`) described in that file.
   2. Python 3.12 is selected from `.python-version`.
   3. The measured bundle, about 82.4 MiB uncompressed, fits within the function size limit, which is assumed to be 250 MB.
   4. The `VERCEL_*` host variables are present at runtime.
   5. The function sees the public `Host` and an `https://` `Origin`.
   6. The Deployment Protection choice is made, and `Cache-Control: no-store` responses are not cached at the edge.
   7. The filesystem is read-only apart from `/tmp`.
5. **Smoke test.** Deploy a preview and check the following. Only then promote to production.
   - `/health` responds.
   - The four presets return results.
   - "Explain" works through the cookie.
   - A cross-origin POST is refused.
