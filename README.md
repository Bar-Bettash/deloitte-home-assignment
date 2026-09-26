# Airport Investment Analyst — Lean Build Plan

**Status: plan only.** The goal is a small, convincing home-assignment prototype, not a production aviation platform.

## Goal

Build an AI-assisted airport-investment screening tool that can answer the four assignment workflows with real public data, deterministic calculations, clear assumptions, and conversational follow-ups:

1. Which New England airports are strong screening candidates for terminal-expansion diligence?
2. How do LAX and SNA compare on congestion indicators?
3. What percentage of ANC flights are long-haul?
4. What demand pressure is visible at SFO, and what can or cannot be concluded about unmet demand?

The product is a **screening assistant**, not a profitability model. Public traffic data can identify pressure, growth, capacity utilization, and operational strain. It cannot establish project-specific returns or a causal quantity of latent demand without capex, revenue, fare, booking/search, and constraint-specific inputs.

## Definition of done

The prototype is complete only when all of the following work with real qualified data:

- **New England:** a deterministic numeric ranking is produced for assessable airports using the frozen scoring formula below.
- **LAX vs SNA:** the app returns a numeric CY2024 comparison of cancellation rate, mean departure delay, and taxi-out time for the same reporting population.
- **ANC:** the app returns a departure-weighted long-haul percentage with numerator, denominator, threshold, and coverage.
- **SFO:** the app returns passenger-growth and operational pressure indicators, then explicitly states that quantitative unmet demand is not identifiable from the available sources.
- **Public API:** the live DataSF API is called by the application and its result is consumed by the SFO workflow.
- **AI agent:** a natural-language question is mapped to one of the four allowlisted workflows and validated before execution.
- **Follow-up:** at least one follow-up changes or narrows an existing analysis without losing the selected airport/workflow context.
- **Transparency:** every answer shows period, population, source/coverage, assumptions, and any unavailable fields.
- **UI:** a reviewer can use the four example prompts from one browser screen.
- **Docs:** the repository includes a short architecture note explaining the scoring methodology, tradeoffs, and where AI is used.

A workflow that only returns a limitation does **not** satisfy the first three bullets. SFO is the deliberate exception because the requested latent-demand quantity is not defensible from the available public data; the app must still return useful measured indicators.

## Deliberate non-goals

To keep the assignment focused, the first submission does **not** include:

- a 3D globe or God’s Eye View integration;
- live aircraft tracking;
- voice;
- saved analyses across process restarts;
- a database server, queue, worker fleet, or multi-agent architecture;
- automated document crawling or RAG;
- a full financial-return model;
- a generalized aviation data platform.

The uploaded ORBIT reference is used only for visual tone: dark workspace, restrained typography, compact controls, and clear information hierarchy. God’s Eye View remains an optional post-core enhancement if the analytical prototype is already complete.

## Architecture

One local FastAPI process serves both the API and a small static browser UI. Source refresh is separate from calculation. The analytical workflows read qualified local snapshots so a demo is reproducible.

~~~mermaid
flowchart LR
    U[Analyst] --> UI[Static chat and results UI]
    UI --> API[FastAPI]
    API --> INTENT[One constrained intent call]
    INTENT --> VALIDATE[Validate allowlisted workflow and args]
    VALIDATE --> CALC[Deterministic calculations]
    CALC --> SNAP[Qualified local snapshots]
    API --> SESSION[In-memory session context]
    API --> RESP[Template explanation and source metadata]
    RESP --> UI
    DATASF[DataSF public API] --> REFRESH[Refresh adapter]
    REFRESH --> SNAP
~~~

### Why this shape

- **FastAPI + static HTML/JS:** one runnable application, no frontend build system required.
- **DuckDB or direct Parquet reads:** enough for the analytical joins without introducing a database service.
- **In-memory session state:** sufficient for a single-process home-assignment demo; restart persistence is unnecessary.
- **One LLM call per message:** the model classifies intent and follow-up parameters only. It does not calculate metrics or invent answer prose.
- **Template explanations:** every displayed number comes directly from the deterministic result object.

## Data sources and bounded scope

Detailed source qualification evidence already lives under backend/docs/evidence/. That evidence is useful input, but the submission does not need a large provenance subsystem.

| Source | Use in prototype | Scope |
|---|---|---|
| DataSF SFO Air Traffic Passenger Statistics | Runtime public-API integration and SFO passenger trends | CY2023–CY2024 |
| FAA CY2024 commercial-service airport list | Defines the New England screening cohort | CY2024 cohort only |
| BTS T-100 Segment | New England traffic/seat metrics and ANC long-haul calculation | CY2023–CY2024 as needed |
| BTS Reporting Carrier On-Time Performance | LAX/SNA congestion indicators and optional SFO operational context | CY2024 only |

### Simplified source rule

Each accepted snapshot needs only:

- source name and URL;
- retrieval UTC;
- period;
- row count;
- SHA-256;
- validation result.

Do not build a generalized lineage service. If a refresh fails validation, keep the last accepted snapshot and show that the live refresh is unavailable.

### Public API requirement

The DataSF Socrata endpoint is the runtime public API. The application must actually call it during refresh and consume the accepted output in the SFO workflow. FAA/BTS official downloads provide the broader historical datasets.

## Frozen analytical rules

These formulas are intentionally simple, visible, and versioned as methodology v1.

### 1. New England screening score

Use the FAA CY2024 commercial-service cohort and T-100 origin-direction scheduled passenger-service records for CY2023 and CY2024.

For each assessable airport calculate:

- **2024 passenger volume**
- **2023→2024 passenger growth**
- **2024 seat occupancy = passengers / available seats**

Eligibility requires complete required months, positive 2023 passengers, and positive 2024 seats. Missing required inputs make the airport not assessable; do not impute.

Normalize each metric with percentile rank across the assessable cohort and calculate:

**score = 0.40 × growth percentile + 0.30 × volume percentile + 0.30 × occupancy percentile**

Display the component metrics beside the score. This is a **traffic-pressure screening score**, not a probability of investment success and not proof that a terminal project is the binding intervention.

Return the top screening candidates plus every excluded/unassessable airport and its reason.

### 2. LAX vs SNA congestion comparison

Use CY2024 BTS Reporting Carrier On-Time Performance, origin airport only, for domestic scheduled flights represented by reporting carriers.

Show side-by-side:

- cancellation rate;
- diversion rate;
- mean DepDelayMinutes on non-cancelled/non-diverted flights;
- mean TaxiOut on non-cancelled/non-diverted flights;
- eligible row counts and non-null denominators.

Use the same population definition for both airports. Do not collapse these indicators into a fake precision score. The answer explains which airport is higher on each indicator and notes that carrier/route mix can affect the comparison.

### 3. ANC long-haul share

Use eligible T-100 ANC-origin scheduled passenger-service records.

Define long-haul for this prototype as **distance >= 3,000 statute miles**.

Weight by **DEPARTURES_PERFORMED**, not route-row count or passenger count.

**long-haul share = long-haul performed departures / all eligible performed departures**

Show numerator, denominator, threshold, and missing-distance coverage. If distance coverage is incomplete, show a range rather than a false exact percentage.

### 4. SFO demand-pressure answer

Use DataSF to calculate monthly/annual enplaned passenger trends for CY2023–CY2024. Optionally add the same CY2024 BTS operational indicators used above once qualified.

The answer separates:

- **Observed:** passengers, changes, cancellation/delay/taxi indicators.
- **Calculated:** year-over-year change and comparable rates.
- **Inferred:** evidence of demand pressure or operational strain.
- **Unknown:** quantitative unmet demand and its causal source.

If asked for a numeric quantity of unmet demand, return **not_identifiable** and list the missing inputs. Still provide the measured demand-pressure indicators instead of ending with a disclaimer.

## AI agent contract

The LLM receives the user message plus minimal previous context and may return only a strict schema:

- workflow: new_england_screen | lax_sna_compare | anc_long_haul | sfo_demand_pressure
- airports
- year or period
- optional requested metric
- optional long-haul threshold
- clarification_needed

The backend rejects unknown workflows, unsupported periods, unknown airports, malformed values, or unsupported parameters.

**The model never returns authoritative numbers, calculations, citations, or final answer prose.**

The request path allows at most **one model call per user message**. If the model is unavailable or invalid, the UI remains usable through the four quick prompts and a small deterministic parser for explicit requests.

Follow-up context lives in memory for the current process. Examples:

- “Show only cancellations” keeps LAX/SNA and the current year.
- “Why is BOS above PVD?” keeps the latest New England ranking.
- “What threshold did you use?” keeps the ANC result.
- “Use 2023 for ANC” recomputes only if that period is qualified.

## UI scope

The UI is intentionally small:

- one chat input;
- four example prompt buttons;
- one result area;
- a compact comparison/ranking table;
- a source/coverage/methodology details drawer;
- clear loading, error, unavailable, and success states.

Use the ORBIT recording as styling inspiration only: near-black background, restrained white typography, muted secondary text, one accent color, and minimal chrome.

**No globe is required for acceptance.** If the core is complete early, a simple map or selectively reused God’s Eye View/Cesium view may be added behind the same result objects. The analytical result must remain independent of camera position.

## Step-by-step build plan

Each substep below has one outcome. If implementation needs more than two code files or becomes materially larger than the stated outcome, split it again instead of expanding the step.

### Phase 1 — Runnable shell

**What:** get a browser and API running before building analytics.

**How:** one FastAPI process serves health, API routes, and static files.

**Why:** it creates a real end-to-end path immediately and prevents infrastructure work from getting ahead of the product.

#### 1.1 Create the FastAPI shell
- Files: backend/app/main.py, backend/requirements.txt
- Outcome: GET /health returns 200 with a small JSON body.
- Check: start uvicorn and curl /health.

#### 1.2 Serve the static browser shell
- Files: backend/app/static/index.html, backend/app/static/app.js
- Outcome: / displays a chat input and the four assignment prompt buttons.
- Check: open the page and click each button; each produces a request payload in the browser.

#### 1.3 Define request/result contracts
- Files: backend/app/contracts.py, backend/tests/test_contracts.py
- Outcome: workflow, airport, period, status, metrics, coverage, and source metadata validate deterministically.
- Check: pytest backend/tests/test_contracts.py.

### Phase 2 — First complete vertical slice: SFO passenger trends

**What:** prove one public API can travel all the way from fetch to browser result.

**How:** use the already-qualified DataSF source and keep the first calculation narrow.

**Why:** this is the fastest real end-to-end proof and satisfies the assignment’s public-API requirement before broader data work.

#### 2.1 Implement the DataSF refresh adapter
- Files: backend/app/sources/datasf.py, backend/tests/test_datasf.py
- Outcome: fetch the bounded CY2023–CY2024 dataset, validate required columns/periods, and save an accepted snapshot.
- Check: pytest backend/tests/test_datasf.py plus one live refresh command.

#### 2.2 Implement SFO passenger-trend calculation
- Files: backend/app/calculations/sfo.py, backend/tests/test_sfo.py
- Outcome: return monthly/annual enplaned levels and 2024-vs-2023 changes with zero-baseline handling.
- Check: pytest backend/tests/test_sfo.py against a hand-calculated fixture.

#### 2.3 Expose the SFO workflow
- Files: backend/app/main.py, backend/app/responses.py
- Outcome: POST /api/query with the explicit SFO workflow returns the deterministic SFO result and source metadata.
- Check: curl the endpoint and compare one displayed metric with the fixture/source snapshot.

#### 2.4 Render the first real browser result
- Files: backend/app/static/app.js, backend/app/static/index.html
- Outcome: the SFO quick prompt shows real measured values, coverage, and the not_identifiable boundary for numeric unmet demand.
- Check: use the browser prompt and verify the snapshot/source label shown on screen.

### Phase 3 — Add the remaining deterministic workflows

**What:** implement the three remaining assignment calculations without changing the architecture.

**How:** add only the loaders and calculation functions each workflow needs.

**Why:** each new capability becomes a small deterministic extension of the proven vertical slice.

#### 3.1 Load the FAA New England cohort
- Files: backend/app/sources/faa.py, backend/tests/test_faa.py
- Outcome: produce the defined CY2024 New England commercial-service airport set.
- Check: pytest backend/tests/test_faa.py and assert the expected cohort count/IDs.

#### 3.2 Load normalized T-100 snapshots
- Files: backend/app/sources/t100.py, backend/tests/test_t100.py
- Outcome: produce validated records needed for the New England CY2023/24 screen and ANC calculation.
- Check: pytest backend/tests/test_t100.py; missing months remain missing, duplicates do not multiply totals.

#### 3.3 Implement ANC long-haul share
- Files: backend/app/calculations/anc.py, backend/tests/test_anc.py
- Outcome: return performed-departure long-haul share, counts, threshold, and distance coverage.
- Check: pytest backend/tests/test_anc.py with a hand-calculated fixture and incomplete-distance case.

#### 3.4 Implement New England ranking
- Files: backend/app/calculations/screen.py, backend/tests/test_screen.py
- Outcome: return the methodology-v1 ranking, component values, and explicit unassessable airports.
- Check: pytest backend/tests/test_screen.py with hand-calculated percentile/weight/tie fixtures.

#### 3.5 Load CY2024 on-time data
- Files: backend/app/sources/ontime.py, backend/tests/test_ontime.py
- Outcome: produce one qualified CY2024 origin-level dataset covering LAX, SNA, and SFO from the 12 monthly files.
- Check: pytest backend/tests/test_ontime.py and assert all 12 requested months are represented before annual metrics are enabled.

#### 3.6 Implement LAX/SNA comparison
- Files: backend/app/calculations/operations.py, backend/tests/test_operations.py
- Outcome: return the side-by-side cancellation, diversion, delay, taxi-out, and denominator values for both airports.
- Check: pytest backend/tests/test_operations.py against an independently aggregated fixture.

#### 3.7 Add SFO operational pressure
- Files: backend/app/calculations/sfo.py, backend/tests/test_sfo.py
- Outcome: optionally attach the qualified CY2024 operational indicators to the existing passenger-trend result without turning them into a causal unmet-demand estimate.
- Check: pytest backend/tests/test_sfo.py.

### Phase 4 — Add the conversational agent

**What:** make the deterministic workflows conversational.

**How:** one constrained intent call selects a workflow and validated arguments; in-memory context supports follow-ups.

**Why:** the assignment asks for an AI-powered agent and conversational follow-up, but the AI should not become the calculator.

#### 4.1 Add in-memory conversation context
- Files: backend/app/session.py, backend/tests/test_session.py
- Outcome: store latest workflow, airports, period, and result ID for one local process.
- Check: pytest backend/tests/test_session.py, including isolation between two session IDs.

#### 4.2 Add constrained intent parsing
- Files: backend/app/intent.py, backend/tests/test_intent.py
- Outcome: natural-language questions map to the strict allowlist; invalid output is rejected.
- Check: unit tests plus one real-model probe for each of the four example questions.

#### 4.3 Add deterministic fallback
- Files: backend/app/intent.py, backend/tests/test_intent.py
- Outcome: when the model is unavailable/invalid, the four quick prompts and explicit requests still work.
- Check: disable the model dependency and rerun intent tests.

#### 4.4 Wire intent, session, and calculations
- Files: backend/app/main.py, backend/app/responses.py
- Outcome: all four natural-language workflows and supported follow-ups execute the correct deterministic calculation.
- Check: curl all four prompts and at least one follow-up using the returned session ID.

### Phase 5 — Finish the reviewer-facing UI and submission

**What:** make the working core easy to inspect and demo.

**How:** display results, sources, assumptions, and methodology clearly; then document the architecture.

**Why:** clarity and reasoning are more important here than visual spectacle.

#### 5.1 Render ranking/comparison tables
- Files: backend/app/static/index.html, backend/app/static/app.js
- Outcome: tables distinguish real zero, missing, unavailable, and not assessable values.
- Check: exercise fixtures for all four states in the browser.

#### 5.2 Add source/methodology details
- Files: backend/app/static/index.html, backend/app/static/app.js
- Outcome: every result exposes period, population, source, coverage, and methodology/threshold.
- Check: verify each of the four workflows has a visible details section.

#### 5.3 Add basic accessibility/responsiveness
- Files: backend/app/static/index.html, backend/app/static/app.js
- Outcome: keyboard use, labels, focus, and a narrow viewport remain usable.
- Check: complete the four flows with keyboard navigation and a narrow browser width.

#### 5.4 Write the architecture note
- Files: backend/docs/ARCHITECTURE.md, README.md
- Outcome: document scoring, AI boundaries, source scope, tradeoffs, and known limitations.
- Check: a clean-environment reader can start the app using only repository instructions.

#### 5.5 Run final acceptance
- Files: backend/docs/evidence/demo-acceptance.md
- Outcome: record the four real flows, one supported follow-up, model-failure fallback, and one missing-data case.
- Check: every Definition of Done bullet has a linked observed result; any failure leaves prototype acceptance not passed.

## Final demo flow

A short demonstration should prove the assignment, not every possible feature:

1. Ask: “Which New England airports look strongest for terminal-expansion diligence?”
   - show ranked results and the three score components;
   - open methodology and explain that this is a traffic-pressure screen, not a return forecast.
2. Ask: “Compare LAX and Santa Ana congestion.”
   - show cancellation, delay, and taxi-out side by side.
3. Ask: “What percentage of Anchorage flights are long-haul?”
   - show threshold, numerator, denominator, and percentage.
4. Ask: “What is the unmet demand at SFO and why?”
   - show measured passenger/operational pressure;
   - state why a numeric latent-demand quantity is not identifiable.
5. Ask one follow-up such as “Show only cancellations” or “Why is BOS above PVD?”
   - prove the same session context is reused.

## Optional polish only after the core passes

If every Definition of Done item already passes:

1. apply more of the ORBIT visual language;
2. optionally add a simple airport map;
3. only then consider selective God’s Eye View/Cesium reuse.

None of those enhancements may change the underlying AnalysisResult or delay the required four workflows.

## Existing evidence

The repository already contains source-research artifacts under backend/docs/evidence/. They remain useful provenance for implementation, but they are **not** additional runtime systems and they do not expand the product scope.

The historical backend/docs/evidence/plan-review-20260926.md applies only to the earlier plan hash recorded inside that file. This README is the current normative plan.
