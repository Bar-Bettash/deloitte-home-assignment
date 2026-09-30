# Airport Investment Intelligence Agent

A prototype built for the Deloitte FDE home assignment ([brief](docs/FDE%20Exam%202.pdf)). An analyst uses a chat screen to ask investment-screening questions about US airports. The agent answers from public aviation data, ranks or compares airports with deterministic logic, explains how it reached each answer, and supports follow-up questions.

This README is the design and architecture document the brief asks for. It covers the scoring methodology, the key tradeoffs, where and how AI is used, and the assumptions, uncertainty and scope. To run, test or deploy the app, see **[app/README.md](app/README.md)**.

## Repository layout

```text
.
├── README.md          ← this document: design, methodology, tradeoffs, AI use
├── docs/              ← supporting documentation
│   ├── ARCHITECTURE.md    modules, request flow, numerical definitions, data refresh
│   ├── API_UI_MAP.md      the HTTP contract and how the UI uses it
│   ├── adr/               architecture decision records
│   ├── evidence/          verification records (hashes, recomputed figures, acceptance runs)
│   └── history/           earlier plans and reviews, kept for traceability
└── app/
    ├── README.md      ← run, test, configure and deploy
    ├── backend/       FastAPI service, calculations, data snapshots, ingestion scripts, tests
    └── frontend/      static chat UI (HTML, CSS, JS, WebGL globe), no build step
```

## The four questions and what the agent answers

By default the agent compares calendar year 2024 with 2025 (the accepted data bundle `annual-2025-r1`). If the analyst names 2023 or 2024, it uses the historical 2023 → 2024 snapshots instead.

| Question | How it is answered | Data source | Answer (CY2025) |
|---|---|---|---|
| Which New England airports are strong candidates for terminal expansion? | Screen score ranking, with terminal notes | BTS T-100, FAA airport list | 22 airports ranked. HVN is 1st (score 74.76); BGR and PWM tie for 2nd. PVC is excluded because its data is incomplete. |
| How do LAX and Santa Ana (SNA) compare on congestion? | 4 operational-strain indicators, side by side | BTS On-Time Performance | Mixed. SNA has more cancellations (1.047 % vs 0.690 %) and longer departure delays (15.17 vs 13.80 min). LAX has longer taxi-out times (17.73 vs 16.05 min). |
| What share of Anchorage (ANC) departures are long-haul? | Departures of 3,000 miles or more ÷ all departures | BTS T-100 | 999 / 36,040 = 2.77 % |
| What is SFO's unmet flight demand, and why? | Passenger growth minus seat growth, with supporting context | DataSF, BTS T-100, BTS On-Time | −1.22 pp: seats grew faster than passengers (+4.70 %). This is not evidence of unmet demand, and unmet demand cannot be measured from traffic data. |

Every figure above was recomputed independently from the raw files and matched the API ([integrated acceptance](docs/evidence/integrated-acceptance-20260929.md)).

## Architecture

```mermaid
flowchart LR
    subgraph Offline["Offline, run by an operator"]
        Src["Public sources<br/>BTS T-100 · BTS On-Time · DataSF · FAA · AIP"]
        Ingest["Ingestion scripts<br/>download or import, validate, hash"]
        Snap[("Parquet snapshots<br/>+ manifests + bundle registry")]
        Src --> Ingest --> Snap
    end

    subgraph Runtime["Runtime, one FastAPI app"]
        UI["Chat UI<br/>app/frontend"]
        Guard["Host and Origin guard"]
        API["POST /api/query<br/>strict contracts"]
        LLM["One Gemini call<br/>free text → structured request"]
        Engine["Deterministic engine<br/>Python + DuckDB"]
        Cookie["Signed follow-up cookie<br/>airport_context"]
    end

    UI --> Guard --> API
    API -. "free text only, when enabled" .-> LLM
    LLM -. "validated AnalysisRequest" .-> API
    API --> Engine
    Snap -- "SHA-256 verified on load" --> Engine
    Engine --> API
    API <--> Cookie
    API --> UI
```

- **Data is prepared offline.** Ingestion scripts fetch or import each official file, check it, and write it as a Parquet snapshot. They record a SHA-256 hash for every file. The server never calls a data provider while it answers a question. It loads the snapshots named in `data/bundles/accepted.json`, checks every hash, and refuses to answer if any file is missing or has been altered.
- **All numbers come from Python.** `dispatch.py` sends each validated request to one plain function in `calculations/`, which runs DuckDB SQL over the snapshots. There is no agent loop, no SQL written by a model, no database server and no queue.
- **Follow-ups are stateless.** Each result sets a signed `airport_context` cookie (HMAC-SHA256). The cookie holds the resolved request, a digest of the result and an expiry time. When the analyst asks for an explanation, the server checks the signature, recomputes the result, and checks the digest. Because nothing is stored on the server, this works on any serverless instance.

### A question, from start to finish

```mermaid
sequenceDiagram
    actor A as Analyst
    participant UI as Chat UI
    participant API as FastAPI
    participant M as Gemini (one call)
    participant E as Engine (DuckDB)

    alt Preset or "Adjust scope" controls
        A->>UI: Click a preset or change the scope
        UI->>API: POST {analysis: {...}}
    else Free-text question
        A->>UI: Type a question
        UI->>API: POST {message, context_result_id?}
        API->>M: Question + previous request as context
        M-->>API: AnalysisRequest, or "clarify" / "unsupported"
        API->>API: Validate with the same strict contract as presets
    end
    API->>E: Deterministic calculation
    E-->>API: Rows, sources, scope, coverage notes
    API-->>UI: Typed result + Set-Cookie airport_context
    A->>UI: Click "Explain this result"
    UI->>API: POST {analysis: {action: explain}, context_result_id}
    API->>API: Verify cookie, recompute, compare digest
    API->>E: Same calculation again
    API-->>UI: Explanation of the same figures and their sources
```

## Scoring methodology

A general rule for every workflow: missing months are never filled in. If any month of an airport-year is missing, that airport-year is treated as unavailable, never as zero.

### New England terminal-expansion screen

```mermaid
flowchart LR
    Cohort["FAA New England<br/>commercial-service airports<br/>(23 in 2025)"] --> Elig{"12/12 months in both years?<br/>Passengers > 0 and seats > 0?"}
    Elig -- no --> Excl["Excluded, with the reason shown<br/>(e.g. PVC: December 2024 missing)"]
    Elig -- yes --> Inputs["Growth %<br/>Passenger volume<br/>Occupancy %"]
    Inputs --> Pct["Mid-rank percentile<br/>for each input"]
    Pct --> Score["Score = 100 × (0.40·growth<br/>+ 0.30·volume + 0.30·occupancy)"]
    Score --> Rank["Competition ranking<br/>(tied scores share a rank)"]
```

- **Inputs.** P is passengers and S is seats, for the baseline and comparison years.
  - Growth = `100 · (P_cmp − P_base) / P_base`.
  - Volume = `P_cmp`.
  - Occupancy = `100 · P_cmp / S_cmp`.
- **Percentile.** Each input is converted to a mid-rank percentile among the eligible airports: `(2·lower + tied − 1) / (2·(n − 1))`. Here `lower` is the number of airports with a lower value and `tied` is the number with the same value, including the airport itself. Percentiles make the three inputs comparable on one scale. They also stop BOS's size from dominating the score.
- **Why these weights.** Growth gets 40 % and volume and occupancy 30 % each. Growth represents observed traffic momentum, volume represents the scale of activity, and seat occupancy represents utilization of supplied airline seats. Together they provide a traffic-pressure screen, not a measurement of terminal utilization or project profitability. The weights are a judgment call, not a calibrated model. Each result says the score is a heuristic measure of traffic pressure, not of terminal capacity or investment success.
- **Why the leader leads.** Each ranked airport shows its points from each input (growth out of 40, volume out of 30, occupancy out of 30), which add up to its score. The summary compares the leader with the next airport input by input. In 2025, HVN (74.76) leads BGR (72.86) on growth (30.5 vs 28.6) and volume (24.3 vs 20.0) points, which outweighs BGR's higher occupancy (24.3 vs 20.0). HVN is not a small airport winning on growth alone: its volume points are its strongest input.
- **How stable the order is.** An offline check re-weighted the same percentiles 11 ways (growth 30–60 %). HVN stayed in the top four every time and was first in 6 of 11. BOS, PWM and BGR also stay near the top. The top five sit within about 4 points, so read the screen as a shortlist, not a strict order. The shipped weights are unchanged.
- **Terminal notes.** Each ranked airport shows curated terminal-project evidence. Every claim is labelled "Status unknown", because the sources do not confirm project status.
- If fewer than two airports are eligible, the result is `insufficient_data` and no ranking is shown.

### LAX vs SNA congestion

The agent compares domestic departures by reporting carriers on four indicators:

- cancellation rate
- diversion rate
- mean departure delay (early departures count as 0)
- mean taxi-out time

Delay and taxi-out averages use completed flights only. For each indicator the agent reports which airport is higher, lower, or tied, and the headline names them: in 2025, "No single airport is uniformly more congested. LAX has the higher diversion rate and average taxi-out time, while SNA has the higher cancellation rate and average departure delay." It deliberately does **not** combine them into one congestion index, because any weighting would be arbitrary and would hide the fact that the picture is mixed.

### ANC long-haul share

Long-haul share = departures of at least 3,000 miles ÷ all performed departures, from T-100. The analyst can change the threshold to any value above 0 and up to 12,000 miles. If some departures have no recorded distance, the agent returns lower and upper bounds rather than a single number. For ANC there are no such departures. The share covers scheduled passenger-service departures with reported seats; cargo-only and charter operations are outside this measure, and the result says so.

### SFO unmet demand

Traffic data records flights that operated. It cannot show demand that was never served. The agent therefore does not produce a number for "unmet demand" and marks it `not_identifiable`. Instead it reports a **pressure indicator**: passenger growth % minus seat growth %, in percentage points. A positive value would mean passenger growth outpaced growth in the airline seats supplied; a negative value means supplied seats grew faster than passengers. It says nothing about terminal or airfield capacity. For 2025 the value is −1.22 pp: supplied airline seats grew faster than passengers. The answer says so directly: there is no sign in 2025 that seat supply at SFO fell behind passenger traffic, seat occupancy was 82.31 %, and latent demand is not ruled out because traffic data counts only people who flew. For the operational "why", it adds the BTS delay-cause mix for SFO departures (late-arriving aircraft 42.4 %, carrier issues 30.3 %, national airspace system 24.1 %, extreme weather 3.1 %), stating that these shares explain reported delays, not latent demand or terminal capacity. Fares and slot limits are not in the data, and the answer does not claim them. The indicator is shown alongside the DataSF monthly enplanement trend and the on-time indicators.

## Where and how AI is used

AI is used in **exactly one place**: `app/backend/app/model_adapter.py` turns a free-text question into a structured request.

| | |
|---|---|
| **What the model does** | One Google Gemini `generateContent` call with a JSON response schema. It returns an `AnalysisRequest`, or `clarification_required`, or `unsupported_scope`. For a follow-up, the previous request is passed in as context. |
| **What the model never does** | It never produces numbers, SQL, citations or text shown to the analyst. Everything shown comes from the deterministic engine and fixed templates. |
| **Validation** | The model's output is checked by the same strict contract used for presets: a closed list of airports, metrics and years, and no extra fields. Anything outside that list is refused with a clear message. |
| **Limits** | Up to 4,000 characters in; up to 1,024 output tokens (room for the model's brief thinking plus the JSON answer); temperature 0; thinking budget 0; a 20 s timeout; spend capped by the Google project's quota or budget. |
| **Follow-ups** | A follow-up can switch the metric, the year, or bring in another airport ("compare it with LAX"). A comparison runs only if the previous metric exists at both airports; otherwise the agent asks which comparable measure to use, and never substitutes one. |
| **Admission gate** | Free text stays off until the chosen model passes its evaluation sets with zero errors (`scripts/eval_intents.py`): a 30-case corpus (at least 29), a 12-case holdout (at least 11), 8 cases in the assignment's own wording (all 8), a 20-case chat regression (at least 19) and 10 follow-ups that bring in another airport (at least 9). The server then calls the model only if `MODEL_RUNTIME_ENABLED=true`, the model name matches the admitted name, and the SHA-256 of the adapter file matches the admitted hash, so an unreviewed change to the prompt or code turns the feature off. A Gemini key alone never enables free text. |
| **Failure** | Any provider error becomes a safe `ai_unavailable` or `query_timeout` response. Presets and the scope controls work without the model. |

The design keeps AI where it adds value, which is understanding loosely worded questions. It keeps AI out of places where it could make up a figure.

## Key tradeoffs

| Choice | Benefit | Cost |
|---|---|---|
| Parquet snapshots prepared ahead of time, queried with DuckDB, instead of live API calls | Reproducible, fast and free to run. Every figure can be traced to a file hash. | Refreshing the data means re-running ingestion and redeploying. |
| One constrained model call instead of an agent framework | The model cannot invent numbers. Behaviour can be tested with a fixed evaluation set. | Questions outside the supported list are refused rather than improvised. |
| A deterministic scoring formula instead of LLM judgement | Transparent, repeatable and explainable line by line. | The weights are a judgment call, and percentiles depend on which airports are in the cohort. |
| No composite congestion index | Honest about a mixed picture. | No single "winner" headline. |
| A signed cookie instead of a session store | Works on any serverless instance, with no database. | Context lasts at most 1 hour and is lost when the signing key changes. |
| Public data only | Every number traces back to an official source. | Capacity, fares and latent demand cannot be observed. |
| No login (owner's decision) | Simple to demo and review. | Cost is limited by per-call limits and the Google project's quota, not by user accounts. |

## Assumptions, uncertainty and scope

- **Scope.** The four questions above, 2023–2025, and the airports and metrics in the contract. Profitability, ROI, capital cost and causal claims are out of scope. Results state this in their limitations, and the model path refuses such questions as unsupported.
- **Differences between data sources.** T-100 covers scheduled passenger service with seats reported. On-Time data covers domestic flights by reporting carriers only. Figures from different sources are never combined into one ratio.
- **Incomplete data.** Incomplete airport-years are excluded, and the reason is shown. Missing data is never replaced with an estimate.
- **Terminal evidence** is a small, curated review. Every claim is labelled "Status unknown".
- **Freshness.** The accepted bundle was source-checked on 2026-09-27. By 2026-09-29, FAA had replaced its preliminary CY2025 commercial-service file with the final edition (dated 2026-09-24); the relevant 23-airport New England cohort and CY2025 enplanements were unchanged, so the screening results are unaffected ([FAA impact check](docs/evidence/faa-final-cy2025-impact-20260929.md)). The application continues to use the frozen accepted snapshot for reproducibility. The answers are about the past; they are not forecasts.
- **In the UI.** Every result shows the period compared, the data sources, notes on data coverage and the reason for any exclusion.
- **Not implemented.** Voice input, an optional extra in the brief.

## Verification status

| Claim | Status |
|---|---|
| Four workflows, follow-ups and error paths | Locally tested. Python: 827 passed, 1 skipped (the skip needs an author-local raw input). UI: 128 passed. Every figure was recomputed independently. 23,474 API cases ran with no server errors. ([evidence](docs/evidence/)) |
| Packaged data matches the official sources | Source-checked on 2026-09-27 ([activation review](docs/evidence/recent-data-activation-review.md)). The 2026-09-29 recheck blocked on FAA's final CY2025 file, which leaves the New England cohort and figures unchanged ([FAA impact check](docs/evidence/faa-final-cy2025-impact-20260929.md)). The app serves the frozen accepted snapshot. |
| Free-text model | `gemini-3.8-flash` admitted on 2026-09-30 with adapter `d617c5fb…`: 10/10 follow-ups, 30/30 corpus, 12/12 holdout, 8/8 assignment wording and 20/20 chat regression, 0 errors, one run each. The previous candidate failed the corpus twice (a timeout and a misclassification); the prompt was fixed and every set rerun, with no policy or timeout relaxed ([admission evidence](docs/evidence/model-admission-20260930/README.md)). Free text is on only where the Gemini key and admission variables are set (locally in `app/backend/.env`, hosted in the Vercel project settings); otherwise it returns `503 ai_unavailable` and presets still work. |
| Hosted deployment | Packaged for Vercel and checked offline. Not deployed yet ([package check](docs/evidence/vercel-package-check.md)). |

For more detail, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (modules, numerical definitions, how data is refreshed) and [docs/API_UI_MAP.md](docs/API_UI_MAP.md) (the HTTP contract).
