# Airport Investment Intelligence Agent: design note

An analyst chat screen over a deterministic engine. Presets send structured requests; free text is mapped to the same request by one model call; all numbers come from Python over packaged public data. Run and test commands: [README](../README.md).

## Scope: the four questions

The default period is the accepted bundle `annual-2025-r1`, which compares CY2024 with CY2025. Naming 2023 or 2024 selects the historical CY2023 → CY2024 snapshots. Anything outside the supported airports, metrics and years is rejected with `422`.

| Question | KPI | Source | CY2025 result |
|---|---|---|---|
| New England expansion candidates | Screen score ranking, plus terminal notes | BTS T-100, FAA cohort | 22 ranked: HVN 1st (74.76), BGR and PWM tied 2nd; PVC excluded |
| LAX vs Santa Ana congestion | 4 operational-strain indicators | BTS On-Time (FGJ) | Cancellations: LAX 0.690 %, SNA 1.047 %; mixed picture overall |
| ANC long-haul share | Departures ≥ 3,000 mi ÷ all departures | T-100 | 999 / 36,040 = 2.77 % |
| SFO unmet demand, and why | Passenger growth − seat growth | DataSF, T-100, FGJ | −1.2247 pp; unmet demand not identifiable |

Figures: [integrated-acceptance-20260929.md](../backend/docs/evidence/integrated-acceptance-20260929.md) and [recent-api-verification.json](../backend/docs/evidence/recent-api-verification.json).

## Scoring methodology

Missing months are never imputed: an incomplete airport-year is unavailable, not zero.

**New England screen** (`calculations/screen.py`)

- **Cohort:** FAA New England commercial-service airports, 22 in the historical period and 23 in the 2025 bundle (EWB added).
- **Eligibility:** 12/12 months in both years, valid counts, baseline passengers > 0 and comparison-year seats > 0. PVC is excluded because it lacks December 2024.
- **Inputs:** growth `100·(P_cmp − P_base)/P_base`, volume `P_cmp` and occupancy `100·P_cmp/S_cmp`, where P is passengers and S is seats.
- **Percentile:** each input gets a mid-rank percentile among eligible airports, `(2·lower + tied − 1)/(2(n − 1))`.
- **Score:** `100·(0.40·growth + 0.30·volume + 0.30·occupancy)`.
- **Ranking:** competition ranking, so tied scores share a rank. Fewer than two eligible airports gives `insufficient_data`.

**LAX vs SNA** (`calculations/operations.py`, `calculations/comparison.py`) compares domestic reporting-carrier departures on cancellation rate, diversion rate, mean departure delay (`DepDelayMinutes`, early = 0) and mean taxi-out. Means cover completed flights only. Each indicator gets a higher/lower/tied direction; there is deliberately no composite index.

**ANC long-haul** (`calculations/long_haul.py`) uses T-100 performed departures. The default threshold is 3,000 miles, adjustable within (0, 12000]. A null distance produces lower and upper bounds instead of a single value; the unknown count is 0 for ANC. An invalid distance or an incomplete year gives `insufficient_data`.

**SFO pressure** (`calculations/sfo.py`) is T-100 passenger growth % minus seat growth %, in percentage points. It is shown with the DataSF enplaned monthly trend and the on-time indicators. Traffic data cannot reveal unserved demand, so profitability and quantitative unmet demand are `not_identifiable`.

## Data and architecture

```mermaid
flowchart LR
    Src[Public sources] -->|offline ingest, hash| Snap[Parquet snapshots + bundle registry]
    UI[Static chat UI] -->|POST /api/query| API[FastAPI + strict contracts]
    API -.->|free text, if admitted| LLM[One OpenAI call]
    API --> Eng[Deterministic calculations, DuckDB]
    Snap --> Eng
    Eng --> UI
```

- **Data:** offline ingestion writes immutable Parquet; the runtime re-verifies every SHA-256 against `data/bundles/accepted.json` and fails closed.
- **Follow-ups:** an HMAC-signed `airport_context` cookie holds the resolved request and a result digest; "Explain" recomputes and checks it, so there is no server state.
- **Hosting:** Vercel (root `backend`) requires `APP_SIGNING_KEY`, a host allowlist and same-origin POSTs.

## Where and how AI is used

AI is used only in `model_adapter.py`, for free text:

- **The call:** one OpenAI Responses API call (strict JSON schema, `store: false`, ≤ 512 output tokens, 20 s timeout).
- **Output:** an `AnalysisRequest`, `clarification_required` or `unsupported_scope`, re-validated with the preset contract. Never numbers, SQL, citations or user-visible wording.
- **Failure:** any provider failure becomes a sanitized error (`ai_unavailable`, `query_timeout` or `invalid_request`), and presets keep working.
- **Admission gate:** the call runs only if the model name and the adapter's SHA-256 match admitted values, after a live 30-case evaluation.

## Key tradeoffs

- **Packaged Parquet with DuckDB, instead of a database or live API calls.** Reproducible and free to run; refreshing data needs a re-ingest and redeploy.
- **Stateless signed cookie, instead of a session store.** Works on any serverless instance; context lasts at most 1 hour and ends when the key rotates.
- **One constrained model call, instead of an agent framework.** The model cannot fabricate numbers; questions outside the menu are refused.
- **Public data only.** Every figure is traceable to an official source; capacity, fares and latent demand are not observable.
- **No login (owner decision).** Cost is bounded by per-call caps and a hard monthly budget on the OpenAI project; there is no in-app spend ledger.

## Assumptions, uncertainty and limits

- **Screen weights:** the 40/30/30 weights are a judgment, not calibrated. Percentiles depend on which airports are in the cohort.
- **Data populations:** T-100 covers scheduled passenger service with positive seats. On-time data covers domestic reporting carriers only. Populations from different sources are never joined.
- **Terminal evidence:** a curated review in which every claim is labelled "Status unknown".
- **Out of scope:** profitability, ROI, capex, causal claims and voice input.
- **Freshness:** sources were last live-checked on 2026-09-27. Re-promoting the bundle after 2026-10-04T17:17Z needs a new live check.

## Verification status

| Claim | Status |
|---|---|
| Four workflows, follow-ups, error paths | Locally tested ([integrated-acceptance-20260929.md](../backend/docs/evidence/integrated-acceptance-20260929.md)) |
| Packaged data matches official sources | Live source check passed on 2026-09-27 ([recent-data-activation-review.md](../backend/docs/evidence/recent-data-activation-review.md)) |
| Free-text model | Not yet live-verified; returns `503 ai_unavailable` until admitted |
| Deployment | Not deployed ([vercel-package-check.md](../backend/docs/evidence/vercel-package-check.md) is an offline check) |

For more detail, see [ARCHITECTURE.md](../backend/docs/ARCHITECTURE.md) and [API_UI_MAP.md](API_UI_MAP.md).
