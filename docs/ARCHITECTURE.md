# Architecture

The system keeps language interpretation separate from numerical analysis: Gemini resolves supported intent, while deterministic Python/DuckDB code owns every figure shown to the analyst.

## System

```mermaid
flowchart LR
    U[Analyst] --> UI[Static web UI]
    UI --> AUTH[Shared password gate]
    UI -->|Typed or voice text| API[FastAPI]
    API --> CONTRACT[Strict request validation]
    API -. free text only .-> LLM[Gemini]
    LLM -. structured intent .-> CONTRACT
    CONTRACT --> DISPATCH[Deterministic dispatcher]
    DISPATCH --> CALC[Python calculations]
    CALC --> DB[(DuckDB over Parquet)]
    DISPATCH --> RESULT[Typed result]
    RESULT --> UI
    UI -->|Optional| TTS[Browser speech synthesis]
```

Runtime has no agent loop, generated SQL, vector store, database server, or queue.

## Request flow

```mermaid
sequenceDiagram
    actor U as User
    participant B as Browser
    participant A as FastAPI
    participant G as Gemini
    participant E as Deterministic engine

    U->>B: Enter shared password
    B->>A: Authenticate
    A-->>B: Signed HttpOnly cookie
    U->>B: Speak or type
    B->>B: Speech → editable text (if supported)
    B->>A: Query
    opt Free text
        A->>G: Question + bounded prior request
        G-->>A: Structured AnalysisRequest / clarification
    end
    A->>A: Validate contract
    A->>E: Run supported analysis
    E-->>A: Metrics + sources + limitations
    A-->>B: Typed result
    B->>B: Optional read aloud
```

Presets skip Gemini completely. **Explain this result** recomputes and explains the referenced deterministic result without another model call.

## Data pipeline

```mermaid
flowchart LR
    BTS[BTS T-100 / On-Time]
    FAA[FAA]
    DS[DataSF]
    BTS --> ING[Offline ingestion]
    FAA --> ING
    DS --> ING
    ING --> CHECK[Validate + SHA-256]
    CHECK --> SNAP[Parquet snapshots]
    SNAP --> REG[Accepted bundle registry]
    REG --> DB[DuckDB]
    DB --> CALC[Python calculations]
```

Provider APIs/files are used during offline preparation, not while answering a user. The accepted bundle is frozen and hash-checked so a result can be reproduced later.

Default runtime data is `annual-2025-r1`, comparing CY2024 with CY2025. Historical 2023 → 2024 data remains available where the request contract allows it.

## AI boundary

| Gemini does | Gemini does not |
|---|---|
| Interpret a free-text question | Calculate metrics |
| Resolve supported follow-ups | Generate SQL |
| Return a structured request or safe clarification | Create citations |
| Select from allowed airports/metrics/years | Write authoritative numerical answers |

The adapter uses one Gemini `generateContent` call with structured JSON output. Backend validation remains authoritative.

Free text is admitted only when the configured model name and the SHA-256 of `model_adapter.py` match the reviewed values. Any adapter change disables free text until re-admitted.

## Deterministic analytics

| Workflow | Definition |
|---|---|
| New England screen | Mid-rank percentile score: 40% passenger growth + 30% passenger volume + 30% seat occupancy |
| Congestion | Cancellation %, diversion %, average departure delay, average taxi-out; no composite score |
| ANC long-haul | Eligible performed departures with distance ≥ threshold ÷ all eligible performed departures |
| SFO pressure | Passenger-growth % − seat-growth %, plus occupancy, trend, and delay-cause context |

Important rules:

- Missing airport-years are unavailable, never zero-filled.
- New England scoring is a **traffic-pressure shortlist**, not terminal-capacity or profitability proof.
- ANC excludes cargo-only and charter operations from the scheduled passenger-service measure.
- SFO traffic data cannot identify latent demand that never materialized as a flight/passenger.
- Populations from BTS T-100, BTS On-Time, FAA, and DataSF stay explicit rather than being silently mixed.

## State and security

| Concern | Design |
|---|---|
| Demo access | Server-side shared password; no user database |
| Auth session | Signed `HttpOnly`, `Secure`, `SameSite=Strict` cookie |
| Follow-up context | Separate signed `airport_context` cookie with resolved request, result digest, ID, and expiry |
| Server state | No session database |
| Cross-site requests | Same-origin/Host checks; no wildcard CORS |
| Framing/content | `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff` |
| Dynamic caching | `Cache-Control: no-store` |

A referenced result is recomputed and digest-checked before it can be explained or used as follow-up context.

## Voice

Voice is a browser-layer feature:

- Speech recognition produces normal editable text.
- Sending that text uses the same API and Gemini intent path as typing.
- Read-aloud uses browser `speechSynthesis`.
- Audio is not stored by the application.
- Voice adds no second model call and no alternate analytics path.
- Unsupported browsers keep the typed-chat experience.

## Deployment

The repository deploys to Vercel with Root Directory `app` and Python 3.12. One FastAPI function serves the UI and API. Preview deployments stay protected during QA; the Production alias is the recruiter-facing demo.

## Key tradeoffs

| Choice | Benefit | Tradeoff |
|---|---|---|
| Frozen snapshots | Reproducible, provider-independent runtime | Refresh requires ingestion + redeploy |
| Deterministic engine | Auditable numbers | Supported analyses are intentionally bounded |
| One constrained LLM call | Flexible language with small AI surface | Unsupported questions are refused/clarified |
| Signed stateless context | Serverless-friendly follow-ups | Context expires and key rotation invalidates it |
| Shared password | Simple private demo | Not a full identity/role system |

## Limits

- No profitability, ROI, capital-cost, or forecast claims.
- Terminal evidence is curated context, not proof of current project status.
- Historical traffic measures observed activity; they do not reveal all latent demand.
- Results depend on the documented source populations and accepted snapshot period.
