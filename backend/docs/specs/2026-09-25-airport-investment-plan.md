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

Use a separate FastAPI modular monolith and React 19 + Vite + TypeScript frontend. React provides explicit state composition and a later Cesium path; Vite keeps bootstrap small; TypeScript guards API/result contracts. Implement `reserve -> validate -> execute -> persist -> explain`: every direct analysis route and chat invoke one common application service; it validates the capability, idempotency key/payload hash, and expected revision, calls a pure deterministic tool, then atomically persists the result, turn, lineage, and revision. Server templates render every substantive numeric/causal sentence, with a deterministic fallback on model failure. Arbitrary model prose is never trusted. DuckDB reads immutable Parquet. SQLite stores local sessions, capability metadata, idempotency reservations, results, and follow-up links.

This is the smallest design that protects analytical semantics while preserving a clean path from the exam's map-free interface to the later ORBIT/globe product. The canonical implementation sequence is the [root README](../../../README.md#9-numbered-implementation-tickets).

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
5. FastAPI owns typed configuration, package startup, `main.py`, the versioned router, and every promised endpoint; a common application service owns atomic result/session persistence for direct analysis and chat calls.
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
- `ChatSession`/`ChatTurn`: session revision, idempotency key, payload hash, reservation state, committed acknowledgement, intent, clarification or result ID.

No result or follow-up mutates an earlier result. Missing required ranking data produces `not_assessable`; it never becomes zero or triggers silent reweighting.

## API Surface

Planned routes are in [README Section 6](../../../README.md#6-api-and-state-contracts). `POST /api/v1/sessions` issues an anonymous, opaque, expiring capability in an HttpOnly SameSite cookie; `POST /api/v1/sessions/reconcile` accepts the original key/hash and returns canonical revision/result plus that reservation’s committed/pending/failed/absent status, lease generation, and owner activity. Every analysis, chat, result, and evidence route is session-scoped; IDs alone grant no access. Direct analysis routes use the same application service, persistence, idempotency, CAS, and lineage contract as chat, so their results support follow-ups. Pure tools are internal. Backend and frontend package setup begin in parallel with source qualification; screening, long-haul, and demand-pressure tools begin when their own backend/source prerequisites pass and do not wait for comparison browser QA. Final browser proof still covers all four workflows.

## UI/UX Impact

Phase A is map-free: chat, result table, evidence, source/coverage status, assumptions, and result identity. Draft text is independent from transaction state. Every committed acknowledgement is retained in history; only a newer canonical revision updates the current-result pointer, so a late r+1 response cannot overwrite r+2. Draft edits remain intact. The client reconciles unknown responses by original key/hash using the state rules below, preserving prior results while pending.

## Profitability and Finance Boundary

The decision chain is `observed traffic -> pressure priority -> terminal intervention fit/counterevidence -> profitability evidence gap -> diligence recommendation`. A future finance scenario needs capex/phasing, attributable usable-capacity uplift, ramp/utilization, investor revenue capture, incremental opex, asset life, and discount rate. Without those inputs, profitability is returned as `not_supported`. SFO returns substantive observed indicators, sourced constraint hypotheses, counterevidence, and missing demand inputs; throughput, delay, and occupancy alone yield neither a point estimate nor bounds for unmet demand, so that quantity is also `not_supported`. The plan explicitly delivers evidence-gap assessments for those two requested business quantities and does not claim full quantitative coverage of them.

## Source Research Decision Log

1. **BTS SODA airport resource `kfcv-nyy3`: reachable sample, still unqualified.** Live calls returned HTTP 200 JSON, a 19,850 count, distinct bounded pages, and the requested ANC/BDL/BOS/LAX/SFO/SNA rows. Sample rows carried `eff_date` 2020-07-16. This does not prove full pagination, uniqueness/null rates, schema/failure behavior, or that dated enrichment is substantive enough for the assignment. The field named `annual_ops` behaved as a date and must not be interpreted as an operations count. Gate 1 must qualify a meaningful displayed field or select a stronger API.
2. **FAA CY2024 final commercial-service workbook: qualified cohort authority.** The observed workbook passed integrity/content checks, produced 513 unique Locids and a reproducible 22-airport New England cohort, including all six named targets. The ingestion manifest must preserve its recorded checksum, retrieval date, counts, and cohort rule. SODA remains left-joined enrichment.
3. **BTS T-100 All Carriers: blocked traffic candidate.** Official lookup meanings were resolved and an earlier bounded extract exists as historical evidence, but three fresh generated-download attempts failed. Current extraction, complete comparable CY2023/CY2024 coverage, grain, direction, units, identifiers, deduplication, and reconciliation remain unqualified.
4. **Official BTS on-time PREZIP family: exact table/family unresolved.** Index/link naming suggested marketing-carrier data while the observed January archive contained a `Reporting_Carrier` CSV. Only a bounded archive prefix was inspected. Qualification must verify the official table identity, carrier semantics, aliases, all required full files, checksums, row counts, reporting population, deduplication, and field coverage before operational metrics are enabled.
5. **FAA/airport-authority documents: proposed intervention evidence.** Each claim requires publisher, date, page/section, constraint type, counterevidence, and review status.

The plan is sound conditionally. Source-independent package and contract work may start, but every metric-dependent workflow remains blocked until its inputs have qualified coverage and a hand calculation. A frozen/download snapshot may supplement the API but cannot satisfy the exam's API requirement by itself.

## Methodology Decisions

- Proposed primary period: CY2024; proposed growth comparison: CY2023 to CY2024. Both remain subject to complete comparable coverage.
- New England means CT, ME, MA, NH, RI, and VT; the commercial-passenger cohort comes from the qualified FAA list.
- Proposed T-100 passenger default: scheduled passenger service with seats greater than zero, subject to field/service-class verification.
- ANC defaults to passenger scheduled departures; cargo/charter are excluded unless explicitly requested and qualified.
- Long haul defaults to at least 3,000 statute miles as a visible project choice.
- Exact long-haul percentage is reported only when all eligible departure distance is known. With total departures `T`, known long-haul departures `L`, and unknown-distance departures `U`, partial coverage yields `100 * L/T` to `100 * (L+U)/T`; `T=0` is unavailable.
- Pressure score: 40% adjusted-growth percentile, 30% passenger-volume percentile, and 30% seat-occupancy percentile. Adjusted growth is `(P2024-P2023)/max(P2023, 10,000)`; the 10,000-passenger floor is a provisional convention to damp small-base explosions, not an empirical or profitability threshold. Display raw growth beside it; when `P2023 = 0`, raw growth is `not_supported` while adjusted growth remains defined by the floor.
- When at least 20 airports are fully assessable, winsorize adjusted growth at the 5th/95th percentiles; otherwise do not winsorize. This is a provisional tail convention. Normalize all dimensions with ascending midrank `(average_rank-1)/(n-1)`. Equal values share average rank; fewer than two assessable airports produces no ranking; missing required values produce `not_assessable` with no weight renormalization. Scores equal within `1e-12` share competition rank (1, 1, 3); all rows with rank <=3 count as top-three, including ties. Canonical ID only stabilizes row order.
- Run exactly 24 scenarios: four weight cases (base `40/30/30`, equal `1/3` each, growth-heavy `60/20/20`, and scale/utilization-heavy `20/40/40`) × three prior-passenger floors (`1`, `10,000`, `25,000`) × two tail treatments (5th/95th winsorization and none). When fewer than 20 airports are assessable, the winsorized treatment is explicitly recorded as disabled and duplicates the untreated result. Report each airport's full rank range. Label it `robust_top_three` only if, for every one of the six floor/tail pairs, it is top-three in at least three of four weight cases and its maximum rank minus minimum rank across all 24 scenarios is at most two.
- Terminal evidence is a separate gate. `eligible` requires a complete score and current reviewed airport-specific support for a passenger-terminal capacity/processing constraint, with all material counterclaims reviewed and no unresolved conflict. `excluded` requires reviewed evidence that a nonterminal cause binds or the intervention does not address it. Missing, stale, unreviewed, or materially conflicting evidence is `not_assessable`. Only eligible airports are ordered by pressure score; all other rows remain visible with reasons and counterevidence. Evidence never changes the numeric score.

## Session and Idempotency Semantics

- The idempotency key binds the payload hash. Same key and same payload replay the committed response; same key and different payload returns conflict.
- A request reserves a bounded `pending` turn. Model/tool retries remain within one total call cap.
- Compute uses an expected session revision. Result and new state persist atomically only if the revision still matches; a stale response is rejected and never exposed as committed.
- Reservations have an opaque server owner and monotonically increasing lease generation. Only the active owner/generation can commit; expired lease recovery atomically fences the former generation before another worker resumes, retaining the reservation’s total call cap.
- Follow-ups create immutable successor results. A process crash cannot leave session state pointing to a nonexistent result.
- Direct analysis and chat calls use the same application service and create identical follow-up-capable result lineage.
- Reconcile by original key/hash: `committed` permits identical replay; `pending` permits bounded polling or same-key replay only; `failed` permits a new key only after atomic fencing deactivates the former owner; `absent` permits same-key, identical-payload retry with the original expected revision so a delayed original cannot create a second reservation. A later revision conflict requires explicit rebase confirmation before a new transaction.
- Client revisions/current-result pointers are monotonic; older committed acknowledgements remain in history, never overwrite newer canonical state, and never replace edited draft text. Every follow-up uses the latest canonical revision/result.

## 24-Hour Execution and Failure Branch

Hours 0–2 run contract definition, backend/frontend package setup, and all source qualification in parallel. At hour 2, T-100 must reproduce or move to a documented official alternate with equivalent grain, fields, and CY2023/CY2024 coverage. Hours 2–6 build source-independent schemas, startup/session/UI skeletons and snapshots only for passed sources. The analytics store accepts available qualified manifests; traffic and long-haul require T-100, operational metrics require on-time, and screening requires FAA/T-100/reviewed terminal evidence. Execution contracts and the common service do not depend on comparison or source qualification. Hours 6–14 build the common service and each tool whose own prerequisites passed; no tool waits for comparison browser QA. Hours 14–20 integrate routes, reconciliation, rendering, and client state. Hours 20–24 produce browser evidence, architecture, traceability, and rehearse the demo.

Phase B, globe, polish, live context, voice, hosting, and finance scenarios are cut first. Historical extracts and synthetic fixtures cannot replace failed qualification. If meaningful API use, T-100 reproducibility, the exact on-time family, intervention evidence, or any required workflow remains unresolved at the cutoff, the deliverable is explicitly incomplete and names the failed gates; a degraded display is not treated as assignment compliance.

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
| Public APIs | SODA is a tested candidate for a dated API-fed field; full qualification and reviewer-substantive value remain gates, with replacement required if it is merely decorative. |
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

- **Data:** SODA reachability and bounded samples were reproduced but remain short of full qualification; FAA CY2024 workbook content qualifies the cohort; T-100 is currently unreproducible; the on-time archive/table family remains unresolved beyond a January prefix.
- **Backend:** FastAPI modular monolith, read-only DuckDB, immutable Parquet, local SQLite, one common persisted application service, typed intents, CAS revisions, payload-bound idempotency, immutable follow-ups, and reconciliation.
- **AI safety:** claim IDs and server numeric formatting constrain prose; bounded retries share a total cap; deterministic templates remain available.
- **Finance:** screening prioritizes diligence and must preserve terminal fit, counterevidence, and the profitability evidence gap.
- **Frontend/product:** prove chat/table/evidence first; retain ORBIT and selective globe reuse as a later phase.
- **Architecture:** the first capability gate includes compare, stored context, follow-up recomputation, atomic persistence, and deterministic rendering; a calculation-only endpoint is a precursor, not completion.

## Risk Surface

1. **[CRITICAL] Public API does not support modern congestion.** Resolved by using it for dated identity metadata only and separately qualifying analytical downloads.
2. **[CRITICAL] “Most profitable” could become “busiest.”** Resolved by the explicit decision chain and finance evidence gap.
3. **[CRITICAL] The globe could consume the exam budget.** Resolved by map-free Phase A and independently gated Phase B.
4. **[CRITICAL] Model prose could change numbers or causality.** Resolved by deterministic tools, claims, formatting, citations, and fallback.
5. **[CRITICAL] A follow-up could compute correctly and still commit stale state.** Resolved by payload-bound idempotency, expected revision, atomic result/state persistence, committed acknowledgements, and reconciliation.
6. **[CRITICAL] Direct routes could bypass follow-up lineage.** Resolved by routing every analysis/chat execution through the common application service.
7. **[WARNING] Missing metrics could improve a rank.** Resolved by full eligibility and `not_assessable`, never reweighting.

`[GRILL-ME: 5 CRITICAL / 1 WARN / 0 NIT — 5-critical answers captured]`

The strongest doubt is over-engineering a one-day assignment. The plan keeps one backend process, one frontend, file snapshots, and SQLite while excluding distributed machinery. The second doubt is source uncertainty; that uncertainty becomes the first blocking gate instead of a hidden assumption. **DOUBT-OVERRIDDEN** because the remaining controls protect required follow-up and reasoning behavior.

## Reality Sweep Report

### Observed

- Baseline substantive project files were `README.md` and `FDE Exam 2.pdf`; AppleDouble sidecars also exist.
- The root is a Git repository on `main`; planning evidence was committed before this correction pass.
- The FAA CY2024 workbook evidence recorded in the README qualifies the cohort content and New England membership rule, subject to preservation in the future ingestion manifest.
- The SODA probe proves reachability, a count, bounded pagination, and sampled fields only. T-100 fresh extraction failed, and on-time inspection covered a bounded January archive prefix with unresolved family semantics.

### Proposed only

- No backend/frontend implementation, dependency, analytical snapshot, methodology, test, build, browser flow, or deployment has been observed.
- All future paths and verification commands remain plans.
- No external provider review or all-agent approval is claimed.
- The unavailable `/large-task` workflow was not substituted with hidden machinery; the plan uses explicit atomic tickets instead.

### First proof

The first complete capability proof is a qualified source feeding LAX/SNA comparison, persisted structured context, “use the previous year” recomputation, atomic successor state, and deterministic numeric rendering. The first browser proof follows that backend gate.

## Acceptance & Verification Matrix

The detailed acceptance criteria and ownership of every invoked test/runner live with each numbered ticket in [README Section 9](../../../README.md#9-numbered-implementation-tickets). Early pytest checks explicitly wait for the backend test extra, and frontend TypeScript/client tests have named ownership. Commands below are prospective and were not executed during planning.

| Subtask | HITL Tier | Acceptance Criteria (outcome — brainstorming fills) | Verification Command |
| :--- | :--- | :--- | :--- |
| Source qualification | Rescore | Required API/download inputs have qualified grain, units, periods, deduplication, lineage, and hand checks; failure blocks dependents. | `python backend/scripts/build_source_matrix.py --check` |
| Immutable analytical data | Rescore | Identical inputs yield identical pinned read-only snapshots without cross-population multiplication. | `python -m pytest -q backend/tests/test_analytics_storage.py` |
| Complete comparison capability | Rescore | LAX/SNA comparison includes evidence, stored context, prior-year follow-up, atomic successor, scoped access, and deterministic rendering; other tools may progress independently on their own passed prerequisites. | `python -m pytest -q backend/tests/test_comparison_journey.py` |
| Conversation concurrency | Rescore | Direct/chat persistence parity, replay, conflict, stale revision, committed acknowledgement after draft edits, reconciliation after unknown response, crash recovery, pending expiry, and bounded retries behave deterministically. | `python -m pytest -q backend/tests/test_sessions.py backend/tests/test_orchestrator.py` |
| Comparison UI | Rescore | Browser slice preserves prior result while pending, applies committed revision/result state despite later draft edits, and recovers lost responses through reconciliation. | `npm --prefix frontend run qa:comparison` |
| Remaining deterministic tools | Rescore | Screening, long-haul percentage, and demand pressure start from their own backend/source prerequisites and preserve evidence/unknowns through the common persisted service. | `python -m pytest -q backend/tests/test_screen.py backend/tests/test_long_haul.py backend/tests/test_demand_pressure.py` |
| All-four conversational integration | Rescore | Intent contracts, allowlist dispatch, server templates, client rendering, and browser chat proof cover all four workflows before architecture delivery. | `npx --prefix frontend playwright test frontend/qa/all-workflows.spec.ts` |
| Assignment package | Rescore | A fresh reviewer completes all workflows and traces PDF requirements to architecture and evidence. | `python -m pytest -q backend/tests/test_requirement_traceability.py` |
| Phase B | Separately gated | ORBIT shell and optional globe each depend on the completed exam gate; neither alters Phase A semantics. | Visual evidence in `frontend/docs/WORKSPACE_REVIEW.md` and `frontend/docs/GLOBE_DECISION.md`; no runner is presumed. |

## Open Questions

No question blocks review of the plan. Exact T-100 extraction, on-time table/family semantics and full coverage, intervention documents, and final API acceptance are external qualification gates that block only their dependents. Methodology v0 supplies executable provisional conventions plus sensitivity disclosure; Ticket 7 freezes or replaces them before ranking. The exact remaining clock controls the documented cutoff branch, never a compliance workaround.

## Self-Review

`[Self-review: 23 issues found, 23 fixed, 0 remaining]`

Fixed: source/API role conflation; public-API overclaim; FAA status drift; T-100 and on-time qualification overclaim; on-time family mismatch; profitability and unmet-demand coverage; methodology defaults, base effects, ties, missingness, sensitivity, and terminal eligibility; direct-route persistence; lost-response reconciliation; client draft/commit ordering; parallel setup; independent workflow dependencies; 24-hour cutoffs; incomplete-delivery rules; stale Git status; and section links.
