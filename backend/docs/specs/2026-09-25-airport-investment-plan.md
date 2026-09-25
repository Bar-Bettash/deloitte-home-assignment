# Spec: Airport Investment Intelligence Agent

Date: 2026-09-25  
Author: brainstorming-agent  
Status: Plan only; conditional on the source-qualification gate

## Problem Statement

A Deloitte Forward Deployed Engineer candidate needs a feasible 24-hour plan for an airport-investment intelligence agent. The system must use a public API, deterministic airport ranking or comparison logic, clear reasoning, and conversational follow-ups while remaining honest about uncertainty and the gap between operational pressure and profitable airport modernization.

## Owner Outcome

Deliver a backend-first, reviewer-reproducible application whose first complete capability compares airports and recomputes a follow-up from stored structured context, then generalizes across all four exam workflows without presenting a screening score as investment return.

## Assignment Interpretation

The exam evaluator is the primary user for the 24-hour milestone. They need a defensible end-to-end result more than a polished globe. The full ORBIT-style product remains a separate follow-on phase. The exact submission clock is unknown; optional scope is cut before the architecture or truthfulness rules.

## Out-of-Scope Fence

- No code, dependency setup, test, deployment, credential operation, or Git operation is authorized by this plan.
- No ROI, NPV, profitability forecast, or causal unmet-demand quantity is claimed by the core product.
- No production auth, billing, cloud infrastructure, queue, vector database, autonomous crawler, or multi-agent product runtime is required.
- No globe, live-flight context, voice, hosting, or full visual polish may block the exam milestone.
- No candidate analytical source, snapshot, methodology, or application path is described as runtime-qualified.

## Chosen Approach

Use a separate FastAPI modular monolith and React 19 + Vite + TypeScript frontend. React provides explicit state composition and a later Cesium path; Vite keeps bootstrap small; TypeScript guards API/result contracts. Implement `parse -> validate -> execute -> persist -> explain`: the model proposes a typed intent and later an ordered list of approved claim IDs plus enumerated relations/qualifiers; code validates and dispatches deterministic tools; a compare-and-swap transaction persists the result and session revision; server templates render every substantive numeric/causal sentence; and a deterministic template handles model failure. Arbitrary model prose is never trusted. DuckDB reads immutable Parquet. SQLite stores local sessions, capability metadata, idempotency reservations, results, and follow-up links.

This is the smallest design that protects analytical semantics while preserving a clean path from the exam's map-free interface to the later ORBIT/globe product. The canonical implementation sequence is the [root README](../../../README.md#8-numbered-implementation-tickets).

## Alternatives Considered

| Approach | Load-bearing difference | Complexity | Blast radius | Reversibility | IL Privacy | Disposition |
|---|---|---:|---:|---|---|---|
| **Separate FastAPI + Vite** | Explicit API boundary between engine and UI | Medium/high | 8/10 future build | Services/adapters replace independently | Low | **Chosen** |
| **Single Python app/UI** | One runtime and presentation stack | Low/medium | 5/10 future build | Easy initial rollback; harder later UI replacement | Low | Faster demo, weaker product path |
| **Upstream-first spatial app** | Existing spatial app becomes host runtime | High | 9/10 future build | Hard because inherited coupling spreads | Low | Rejected for Phase A |

The recommendation stays stable across the four dimensions. The single-app option is cheaper, but the selected boundary prevents presentation or model state from changing analysis meaning. The upstream-first option has the largest unverified dependency surface.

## Proposed Architecture

1. Source adapters qualify and ingest public API/download inputs into immutable versioned snapshots.
2. DuckDB performs read-only queries over pinned Parquet files.
3. Deterministic tools implement compare, screen, long-haul share, and demand-pressure workflows.
4. Evidence records distinguish terminal fit from runway, airspace, airline, access, and weather constraints.
5. FastAPI owns typed configuration, package startup, `main.py`, the versioned router, every promised endpoint, and atomic result/session persistence.
6. A provider adapter isolates vendor calls; the conversational layer cannot author formulas, SQL, numbers, or substantive free-form claims.
7. The React/Vite/TypeScript interface first renders chat, tables, evidence, coverage, and limitations without a map.
8. Phase B may add an ORBIT/globe view as another consumer of the same result IDs.

## Data Model Changes

This is a greenfield plan; all records are proposed.

- `Airport`: canonical ID, IATA/ICAO, time-valid source IDs, reviewed aliases, region membership, registry status.
- `SourceSnapshot`: source, retrieval/observation dates, partitions, checksum, schema version, coverage, mode.
- `EvidenceRecord`: airport, publisher, date, locator, proposition, constraint type, counterevidence, review status.
- `AnalysisRequest`: workflow, airport/cohort, period, service scope, threshold, methodology version.
- `AnalysisResult`: immutable request, snapshot IDs, metrics, coverage, evidence IDs, assumptions, exclusions, unknowns, predecessor.
- `Claim`: typed approved statement referencing validated result fields and evidence.
- `ChatSession`/`ChatTurn`: session revision, idempotency key, payload hash, reservation state, intent, clarification or result ID.

No result or follow-up mutates an earlier result. Missing required ranking data produces `not_assessable`; it never becomes zero or triggers silent reweighting.

## API Surface

Planned routes are in [README Section 6](../../../README.md#6-api-and-state-contracts). The root router is created first, then each endpoint ticket registers its route only after its dependencies exist; a final inventory check closes the API surface. `POST /api/v1/sessions` issues an anonymous, opaque, expiring capability in an HttpOnly SameSite cookie before comparison is exposed. Every chat/result/evidence route is session-scoped; IDs alone grant no access. The comparison route is a precursor: the first complete gate also requires evidence, stored structured context, follow-up recomputation, immutable successor results, deterministic rendering, and the minimal browser slice. Screening, long-haul, and demand-pressure tools start only after that slice passes. Before the architecture gate, explicit tickets extend the intent contract, orchestrator allowlist, safe templates, client renderer, and browser proof across all four workflows.

## UI/UX Impact

Phase A is map-free: chat, result table, evidence, source/coverage status, assumptions, and result identity. A monotonic client generation increments synchronously on input, submit, and retry; both success and error handlers discard stale generations, while the prior result remains visible during pending work. Empty, clarification, partial, unavailable, metadata-missing, fixture, conflict, explanation-failure, stale-discarded, and success states are explicit. Accessibility audit precedes state-coverage audit; final web-design review closes the UI; motion/canvas review fires only when those surfaces exist. Phase B's ORBIT shell depends on the completed exam gate but remains independent of the optional globe decision.

## Profitability and Finance Boundary

The decision chain is `observed traffic -> pressure priority -> terminal intervention fit/counterevidence -> profitability evidence gap -> diligence recommendation`. A future finance scenario needs capex/phasing, attributable usable-capacity uplift, ramp/utilization, investor revenue capture, incremental opex, asset life, and discount rate. Without those inputs, the system does not imply returns. SFO returns substantive observed indicators, sourced constraint hypotheses, counterevidence, and missing demand inputs; throughput, delay, and occupancy alone yield neither a point estimate nor bounds for unmet demand.

## Source Research Decision Log

1. **BTS SODA airport resource `kfcv-nyy3`: preliminarily reachable, analytically unqualified.** Two independent probes returned HTTP 200 JSON and the requested ANC/BDL/BOS/LAX/SFO/SNA rows. Sample rows carried `eff_date` 2020-07-16. The metadata description is dated 2020-07-16. The field named `annual_ops` behaved as a date in the sampled response, so it must not be interpreted as an operations count. Coordinates were not selected and are unproven. Planned use: dated public-API identity/name metadata displayed with provenance and a `registry_metadata_missing` state.
2. **FAA CY2024 final commercial-service workbook: proposed cohort authority.** It must determine eligible-airport membership after qualification. SODA metadata is left-joined enrichment; a missing SODA row never removes an FAA airport.
3. **BTS T-100 All Carriers: proposed traffic foundation.** It is a download, not the public API requirement. Grain, service class, units, fields, identifiers, and complete CY2023/CY2024 partitions remain a gate.
4. **BTS marketing-carrier on-time monthly data: proposed operational indicators.** CY2023 and CY2024 reporting population, carrier grain, deduplication, fields, and partitions remain a gate. A month with no rows may reflect seasonality; completeness uses expected partitions and reporting coverage, not `months_with_rows` alone. Operational aggregates stay separate from T-100 aggregates; raw populations are never multiplicatively joined.
5. **FAA/airport-authority documents: proposed intervention evidence.** Each claim requires publisher, date, page/section, constraint type, counterevidence, and review status.

The plan is sound conditionally. Implementation is blocked at Gate 1 until every metric required by methodology v0 has qualified coverage and a hand calculation. A frozen/download snapshot may supplement the API but cannot satisfy the exam's API requirement by itself.

## Methodology Decisions

- Proposed primary period: CY2024; proposed growth comparison: CY2023 to CY2024. Both remain subject to complete comparable coverage.
- New England means CT, ME, MA, NH, RI, and VT; the commercial-passenger cohort comes from the qualified FAA list.
- Proposed T-100 passenger default: scheduled passenger service with seats greater than zero, subject to field/service-class verification.
- ANC defaults to passenger scheduled departures; cargo/charter are excluded unless explicitly requested and qualified.
- Long haul defaults to at least 3,000 statute miles as a visible project choice.
- Exact long-haul percentage is reported only when all eligible departure distance is known. With total departures `T`, known long-haul departures `L`, and unknown-distance departures `U`, partial coverage yields `100 * L/T` to `100 * (L+U)/T`; `T=0` is unavailable.
- Candidate pressure score for evaluation: 40% growth percentile, 30% passenger-volume percentile, 30% seat-occupancy percentile. It is not an empirically validated investment model and cannot be emitted until methodology v0 is frozen.
- Percentile ties, a one-airport cohort, and missing required values receive explicit deterministic rules. Missing values never cause renormalized weights.
- An airport may appear on a pressure watchlist without passing the terminal-intervention evidence gate. Ranking terminal projects requires that second gate.

## Session and Idempotency Semantics

- The idempotency key binds the payload hash. Same key and same payload replay the committed response; same key and different payload returns conflict.
- A request reserves a bounded `pending` turn. Model/tool retries remain within one total call cap.
- Compute uses an expected session revision. Result and new state persist atomically only if the revision still matches; a stale response is rejected and never exposed as committed.
- `failed` preserves a safe retry path. A crashed/expired `pending` reservation is reclaimed only after its lease expires and no committed result exists.
- Follow-ups create immutable successor results. A process crash cannot leave session state pointing to a nonexistent result.

## Blast-Radius Score

**Current documentation task: 2/10, DRAFT** after the inherited external-context multiplier. Only two planning documents change. The future build is broad and every implementation ticket must be rescored for API/model calls, dependencies, credentials, persistence, and deployment. This plan grants no execution authority.

## Affected Files / Agents

Planning files changed now:

- `README.md` — canonical master plan.
- `backend/docs/specs/2026-09-25-airport-investment-plan.md` — decisions, traceability, and review artifact.

Future owners: data engineer, backend engineer, analytics/research lead, frontend engineer, lead architect, and finance lead. No project-local agent definitions exist in the current baseline; these are role assignments for later orchestration.

## HITL Tier per Subtask

| Subtask group | Tier now | Future owner |
|---|---|---|
| Planning documents | DRAFT | brainstorming agent |
| Source qualification and snapshots | Rescore before work | data engineer |
| Deterministic backend and API | Rescore before work | backend engineer |
| Conversational state and model boundary | Rescore before work | backend engineer |
| Minimal UI and architecture deliverable | Rescore before work | frontend engineer / lead architect |
| Optional spatial, live, voice, hosting, finance | Separately gated | product/frontend/finance owners |

## Rollback Recipe

The original README is preserved byte-for-byte at `/tmp/deloitte-original-readme-20260925.md`, SHA-256 `06e7d27de358d0ab4f326f7ffa9f993878250deea351fe9cd73b4c5fd68cad70`. Restore it and remove this newly created spec to undo the planning change. No runtime or external state changed.

## IL Privacy Assessment

The analytical inputs are public aviation data, but user prompts may contain personal or organization-sensitive content. The exam design uses anonymous, server-issued, opaque, expiring capability sessions and session-scoped result access; it does not claim blanket privacy safety. Production hosting requires a separate privacy review covering prompt retention, logs, deletion, access, and operator obligations.

## PDF Requirement Traceability

Source artifact: `FDE Exam 2.pdf`, SHA-256 `12de252ee7ecfb43be171c19b562a392bcec1b7478c2c1f7c23a33e1bf47c1f6`. Requirements are on PDF page 2; page 3 is blank.

| PDF requirement | Plan response |
|---|---|
| Public APIs | BTS SODA identity metadata is a substantive dated API-fed display contribution; analytical downloads supplement it. |
| Rank or compare with defined KPI | Deterministic comparisons and gated methodology-v0 pressure screening. |
| Explain reasoning | Visible components, evidence/counterevidence, claim IDs, server-formatted numbers, deterministic fallback. |
| Conversational follow-ups | Typed intent, structured stored context, CAS revisions, immutable successor results. |
| Deterministic logic | All metrics and ranking run in code over pinned snapshots; no model arithmetic. |
| Chat interface | Minimal Vite chat/table/evidence screen in Phase A. |
| Assumptions, uncertainty, scope | Claim taxonomy, coverage, exclusions, unknowns, source mode, profitability boundary. |
| Source code | README tickets plan the backend/frontend build; no code is produced now. |
| Short architecture document | Future `backend/docs/ARCHITECTURE.md`, linked from the root README. |
| Scoring methodology | Frozen methodology v0 with cohort, formulas, weights, normalization, missing/tie rules, sensitivity. |
| Key tradeoffs | Three materially different architectures and the Phase A/Phase B split. |
| Where/how AI is used | AI proposes typed intent and bounded prose; code validates, calculates, formats, persists, and falls back. |

## Specialist Discussion Summary

- **Data:** the SODA endpoint reachability and sample IDs/dates were reproduced, but the 2020 metadata cannot determine the 2024 cohort or modern congestion. FAA membership is authoritative after qualification; analytical sources remain gated.
- **Backend:** FastAPI modular monolith, read-only DuckDB, immutable Parquet, local SQLite, typed intents, CAS revisions, payload-bound idempotency, immutable follow-ups.
- **AI safety:** claim IDs and server numeric formatting constrain prose; bounded retries share a total cap; deterministic templates remain available.
- **Finance:** screening prioritizes diligence and must preserve terminal fit, counterevidence, and the profitability evidence gap.
- **Frontend/product:** prove chat/table/evidence first; retain ORBIT and selective globe reuse as a later phase.
- **Architecture:** the first capability gate includes compare, stored context, follow-up recomputation, atomic persistence, and deterministic rendering; a calculation-only endpoint is a precursor, not completion.

## Risk Surface

1. **[CRITICAL] Public API does not support modern congestion.** Resolved by using it for dated identity metadata only and separately qualifying analytical downloads.
2. **[CRITICAL] “Most profitable” could become “busiest.”** Resolved by the explicit decision chain and finance evidence gap.
3. **[CRITICAL] The globe could consume the exam budget.** Resolved by map-free Phase A and independently gated Phase B.
4. **[CRITICAL] Model prose could change numbers or causality.** Resolved by deterministic tools, claims, formatting, citations, and fallback.
5. **[CRITICAL] A follow-up could compute correctly and still commit stale state.** Resolved by payload-bound idempotency, expected revision, and atomic result/state persistence.
6. **[WARNING] Missing metrics could improve a rank.** Resolved by full eligibility and `not_assessable`, never reweighting.

`[GRILL-ME: 5 CRITICAL / 1 WARN / 0 NIT — 5-critical answers captured]`

The strongest doubt is over-engineering a one-day assignment. The plan keeps one backend process, one frontend, file snapshots, and SQLite while excluding distributed machinery. The second doubt is source uncertainty; that uncertainty becomes the first blocking gate instead of a hidden assumption. **DOUBT-OVERRIDDEN** because the remaining controls protect required follow-up and reasoning behavior.

## Reality Sweep Report

### Observed

- Baseline substantive project files were `README.md` and `FDE Exam 2.pdf`; AppleDouble sidecars also exist.
- The root is not a Git repository; `git status` and `git log` fail.
- The prior README contained a 575-line, 15-phase plan.
- The SODA sample probe result described above was independently reproduced; it proves reachability and sampled fields only.

### Proposed only

- No backend/frontend implementation, dependency, analytical snapshot, methodology, test, build, browser flow, or deployment has been observed.
- All future paths and verification commands remain plans.
- No external provider review or all-agent approval is claimed.
- The unavailable `/large-task` workflow was not substituted with hidden machinery; the plan uses explicit atomic tickets instead.

### First proof

The first complete capability proof is a qualified source feeding LAX/SNA comparison, persisted structured context, “use the previous year” recomputation, atomic successor state, and deterministic numeric rendering. The first browser proof follows that backend gate.

## Acceptance & Verification Matrix

The detailed acceptance criteria and ownership of every invoked test/runner live with each numbered ticket in [README Section 8](../../../README.md#8-numbered-implementation-tickets). Early pytest checks explicitly wait for the backend test extra, and frontend TypeScript/client tests have named ownership. Commands below are prospective and were not executed during planning.

| Subtask | HITL Tier | Acceptance Criteria (outcome — brainstorming fills) | Verification Command |
| :--- | :--- | :--- | :--- |
| Source qualification | Rescore | Required API/download inputs have qualified grain, units, periods, deduplication, lineage, and hand checks; failure blocks dependents. | `python backend/scripts/build_source_matrix.py --check` |
| Immutable analytical data | Rescore | Identical inputs yield identical pinned read-only snapshots without cross-population multiplication. | `python -m pytest -q backend/tests/test_analytics_storage.py` |
| Complete comparison capability | Rescore | LAX/SNA comparison includes evidence, stored context, prior-year follow-up, atomic successor, scoped access, and deterministic rendering before other tools begin. | `python -m pytest -q backend/tests/test_comparison_journey.py` |
| Conversation concurrency | Rescore | Replay, conflict, stale revision, crash recovery, pending expiry, and bounded retries behave deterministically. | `python -m pytest -q backend/tests/test_sessions.py backend/tests/test_orchestrator.py` |
| Comparison UI | Rescore | Browser slice preserves prior result while pending and discards stale success/error responses across required states. | `npm --prefix frontend run qa:comparison` |
| Remaining deterministic tools | Rescore | Screening, long-haul percentage, and demand pressure start only after the comparison slice and preserve evidence/unknowns. | `python -m pytest -q backend/tests/test_screen.py backend/tests/test_long_haul.py backend/tests/test_demand_pressure.py` |
| All-four conversational integration | Rescore | Intent contracts, allowlist dispatch, server templates, client rendering, and browser chat proof cover all four workflows before architecture delivery. | `npx --prefix frontend playwright test frontend/qa/all-workflows.spec.ts` |
| Assignment package | Rescore | A fresh reviewer completes all workflows and traces PDF requirements to architecture and evidence. | `python -m pytest -q backend/tests/test_requirement_traceability.py` |
| Phase B | Separately gated | ORBIT shell and optional globe each depend on the completed exam gate; neither alters Phase A semantics. | Visual evidence in `frontend/docs/WORKSPACE_REVIEW.md` and `frontend/docs/GLOBE_DECISION.md`; no runner is presumed. |

## Open Questions

No question blocks review of the plan. Exact analytical endpoints, partitions, cohorts, periods, fields, methodology weights, and optional providers are named decision gates that block dependent implementation until resolved. The exact remaining clock is an execution-time phase-cut input; this plan assumes the assignment milestone precedes Phase B.

## Self-Review

`[Self-review: 14 issues found, 14 fixed, 0 remaining]`

Fixed: source/API role conflation; incorrect source date; ambiguous `annual_ops`; profitability drift; UI-first sequencing; calculation-only milestone; stale follow-up commit risk; missing-value reweighting; startup/endpoint ownership; provider boundary; session capability scoping; client response ordering; UI audit routing; and placeholder verification commands.
