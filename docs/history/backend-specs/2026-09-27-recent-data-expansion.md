# Backend completion: recent data and assignment workflows

> Remaining execution order is now defined by [Backend completion plan](2026-09-27-backend-completion-plan.md). Source qualification and bundle contracts below remain supporting specifications; review status is recorded with the new plan.


Status: execution amendment dated 2026-09-27, architecture approved after review corrections. Source acquisition qualification is complete; recent application ingestion and live AI are not yet proven. This plan supersedes the earlier execution order in this file. Deployment is outside this task.

## Required outcome

The local backend must answer the four assignment workflows with real, qualified data, deterministic calculations, clear limitations, and successful natural-language follow-ups through the actual HTTP API. Finish implementation, meaningful tests, independent arithmetic reconciliation and architect review before claiming readiness. A missing model credential leaves live proof explicitly incomplete; it does not stop deterministic backend work.

The current accepted CY2023/24 snapshots and saved explanations remain reproducible. The new annual comparison uses jointly acquired CY2024/CY2025 DataSF and T-100, CY2025 reporting-carrier on-time records, the preliminary FAA CY2025 New England cohort, and separately labeled FY2025 AIP award context. Newer 2026 partial periods are disclosed; they are not annualized or mixed into annual rankings. Profitability, quantitative unmet demand and investment returns remain unidentifiable from these inputs.

## Source findings

The acquisition facts and requests are in [qualification](../evidence/recent-source-qualification-20260927.md), its [machine receipt](../evidence/recent-source-qualification-20260927.json), and the [release comparison](../evidence/recent-source-check.json). These receipts are evidence only, not accepted application manifests or model admission.

- DataSF: 4,075 paired 2024/25 rows and all 48 Enplaned month/geography cells. Selected source hashes were rechecked.
- T-100: 16 state/year exports, exact source grain, zero conflicting raw keys. Fresh 2024 CSV content equals the old content despite regenerated ZIP packaging. The historical 26-origin 2025 count is 31,814 eligible rows; EWB contributes 25 additional rows, separate from that count.
- FAA: preliminary 2025 cohort has 23 New England airports, including EWB. Preserve the historical 22-airport cohort for 2024.
- PVC: complete Massachusetts 2025 acquisition has no eligible PVC class-F rows in January-April or December. Conservatively exclude PVC from annual ranking; do not infer zero total service or fill missing eligible months with zero.
- On-time: all twelve 2025 ZIPs, 7,001,619 national rows and 398,824 unique scoped ANC/LAX/SFO/SNA flights; complete daily/monthly coverage, zero identity conflicts, delay/cancellation semantics checked.
- AIP FY2025: 66 awards totaling $259,112,386 across 20 of the 23 New England airports, including two EWB awards/$295,000. Awards do not establish project completion, remaining need, ROI or airport profitability.

## Shared implementation contract

Freeze this contract before parallel implementation. `app/sources/bundle.py` imports neither calculations nor dispatch. Its immutable `SnapshotRef` contains `source`, `snapshot_id`, `manifest_sha256`, `content_sha256`, `manifest_path`, `data_path`, `years`, `vintage_id`, and `publication_status`. `BundleContext` contains `bundle_id`, `manifest_sha256`, `baseline_year`, `comparison_year`, `cohort`, `sources`, `evidence_path`, and `evidence_sha256`. Paths are server-derived under the data root and cannot come from HTTP/model input. Acquisition, import, publication and source-check dates retain their distinct meanings.

Capture the historical bundle before any new import. Importers create immutable snapshots with `publish_current=False` for candidates. They must not replace legacy `current.json` pointers. The final application selection changes only by atomic bundle promotion after source, arithmetic and evidence validation. A failed import or promotion preserves the prior accepted release.

Calculators retain current direct-call arguments/defaults for historical tests and add keyword-only `bundle: BundleContext | None = None`. With a bundle, they use its exact source paths, periods and cohort without consulting mutable current pointers. HTTP dispatch always resolves one context and passes it through every nested calculation/evidence call. Missing/corrupt/incompatible bundle data returns a typed unavailable response; it never silently falls back.

`AnalysisRequest` gains optional `bundle_id` and allows an omitted year until deterministic server resolution. Explicit 2023/2024 without a bundle selects and discloses the historical bundle. Explicit 2025 selects the accepted recent bundle. A 2024 baseline from the new acquisition requires the recent bundle ID. Omitted year selects the newest accepted complete scope for that workflow. Store concrete resolved year/bundle with each result and follow-up context. Explain reuses stored provenance/evidence without loading current files again.

Coverage states distinguish failed/incomplete acquisition, complete source with no eligible rows, and complete eligible annual coverage. The selected cohort validates membership; region filtering does not change the reference cohort for percentiles.

| Workflow | Required inputs |
|---|---|
| New England screen | FAA cohort, paired T-100, bundle-bound terminal evidence |
| LAX/SNA congestion | Selected-year FGJ reporting-carrier flights |
| ANC long-haul | Selected-year T-100 performed departures and distances |
| SFO pressure | Paired DataSF, paired T-100, selected-year FGJ, SFO evidence |

AIP is separately labeled evidence context required for the final planned recent bundle, never part of the traffic formulas.

## Phases and atomic substeps

Each worker owns explicit files and preserves concurrent edits. Use small compatibility-preserving steps with meaningful focused tests. Each actual implementation dispatch changes at most two code/test files and at most 150 added/changed lines; larger workstreams split into compatibility-preserving dispatches before editing. Each dispatch reports `[Atomicity self-check: PASS]` with its file count, line count and focused command. Coherent phase commits follow the full phase gate. No unrelated refactor, extra service, queue, agent framework, or deployment work. After each phase run the full backend suite, UI regression suite, Ruff with existing exclusions, JSON/syntax checks, and an architectural review of integration changes. Commit only tested phases; the already pending scanner permission affects commits, not authorized implementation/testing.

### Phase 1 — source qualification (complete, documentation staged)

1. Record exact official requests, response sizes/hashes, fields, periods, populations and preliminary status.
2. Qualify paired FMG/DataSF and full FGJ coverage; reconcile FAA/AIP cohorts and counts.
3. Record release/revision comparison separately from application admission.
4. Record PVC's conservative exclusion and EWB's additional rows without overclaiming service coverage.

Evidence: architect approved source documentation; 302 backend tests and 8 UI tests passed. No new application snapshot was accepted by this phase.

### Phase 2 — immutable bundle foundation

Owner: `sources/bundle.py`, `sources/source_check.py`, their tests and generated bundle manifests. Each bundle dispatch owns only `backend/app/sources/bundle.py` and `backend/tests/test_bundle.py` and runs `PYTHONPATH=backend python -m pytest backend/tests/test_bundle.py -q`. Each receipt dispatch owns only `backend/app/sources/source_check.py` and `backend/tests/test_source_check.py` and runs `PYTHONPATH=backend python -m pytest backend/tests/test_source_check.py -q`. Split a listed substep further before exceeding the dispatch bound.

1. Implement strict manifest parsing, confined paths and checksum verification.
2. Capture historical source/evidence identities in an addressable bundle.
3. Implement candidate validation and atomic promotion, preserving old selection on failure.
4. Validate source-check receipt identity, age, period/population and outcome separately from bundle integrity.
5. Test corrupt hashes, missing sources, mixed year pairs, path escapes, stale checks and failed promotion.

### Phase 3 — stage recent sources and generalize calculations

Source owner: `sources/{datasf,t100,ontime,faa,aip}.py`, respective source tests and generated snapshot artifacts. Calculation owner: `calculations/` and respective calculation tests. These owners may work in parallel after Phase 2 freezes its interface.

1. Parameterize DataSF supported year pairs; preserve bounded count/page/schema/coverage/deadline checks and stage the new pair.
2. Import qualified T-100 2024/25 state exports, preserving AIRCRAFT_CONFIG/raw keys, exact overlaps, EWB and explicit PVC coverage status.
3. Import all twelve qualified FGJ 2025 archives, validating identities/flags/dates and preserving cancellation/diversion/null semantics.
4. Parse FAA preliminary CY2025 with the exact 23-ID cohort, preserving historical CY2024.
5. Parse the exact AIP workbook with bounded ZIP/XML validation, unique award keys and amount reconciliation; preserve fiscal-year labels.
6. Pass bundle sources/years into traffic and ANC calculations, preserving performed-departure weighting, missing-distance intervals and zero-denominator outcomes.
7. Generalize screening cohort/year and all internal year-specific fields. Preserve 40/30/30 score weights, full-reference-cohort percentiles, raw-growth ranking and fewer-than-two-eligible behavior.
8. Generalize operational comparisons to selected year, origin filtering and reporting-carrier population. Use DepDelayMinutes and per-metric eligible denominators.
9. Generalize SFO trend and matched-population growth gap; keep DataSF, T-100 and operational populations separate.
10. Reconcile all four 2025 workflows independently against raw source data and preserve historical outputs.

### Phase 4 — API, evidence and recent bundle acceptance

Owner: `contracts.py`, `dispatch.py`, `evidence.py`, corresponding tests and `data/evidence_2025.json`.

1. Extend supported scope/result contracts and resolve request defaults/bundle once in dispatch.
2. Pass exact context to every nested calculation. Test cross-bundle/year rejection and no fallback after integrity failure.
3. Review actual new top-three airport terminal evidence, dates, superseding/completed projects and counterevidence. Explicit unassessable notes are allowed; missing evidence cannot produce a favorable expansion claim.
4. Bind evidence to the exact recent traffic bundle; attach FY2025 AIP context without conflating it with profitability or annual traffic.
5. Verify saved historical explanations remain stable after recent bundle selection.
6. Atomically accept the fully reconciled recent bundle. Structured 2025 and default-period responses must now succeed with exact provenance.

### Phase 5 — model interpretation and freshness consumers

Owner: `main.py`, `model_adapter.py`, `intent.py`, `scripts/{eval_intents,check_source_status}.py`, corresponding tests and separate 2025 evaluation corpus.

1. Implement bounded official release/revision checking, writing receipt atomically for exact accepted bundle/source identities. No network call per user query or daily bulk job.
2. Before provider/budget work, preflight all enabled demo workflows. Source receipt age must be at most seven days, relevant IDs/hashes/periods/populations must match, and required checks must succeed. New partial years are disclosed; a newer complete comparable year or selected-period revision blocks a current-data claim until refresh.
3. Add bundle identity and supported period/default metadata to the actual model adapter schema/input. Explain assignment terminology: terminal expansion screening, LA/LAX, Santa Ana/SNA, long-haul thresholds and SFO demand-pressure proxy. Unsupported profit/unmet-demand quantities stay unsupported.
4. Preserve deterministic validation after model interpretation and concrete session-bound follow-up context. The model cannot choose arbitrary URLs, SQL, citations or session identities.
5. Update deterministic text parsing and freeze a separate 2025/default-year corpus before paid evaluation; retain historical evaluation evidence.
6. Apply the same freshness/admission checks to direct live evaluator entry points. Test zero provider calls on stale/failed/mismatched checks and budget/deadline failures.

### Phase 6 — end-to-end acceptance

1. Run full tests and independent source/API reconciliation with the exact accepted artifacts and current code; audit failure behavior and privacy/cost boundaries.
2. Verify current provider model/prices, configure the existing bounded adapter, and perform the real frozen candidate evaluation within the configured $0.02/request and $2/process limits. Missing credentials are a recorded blocker, never a pass.
3. Require at least 29/30 historical candidate semantic outcomes plus all declared demo/safety cases; require every 2025 required demonstration/follow-up to succeed. Record exact model, adapter/prompt/corpus identities, usage, cost, latency and outcomes.
4. After successful model admission, start the real local HTTP server. Natural-language New England, LAX/SNA, ANC and SFO questions must return numeric 2025 results with source/evidence limits.
5. Exercise same-session follow-ups: cancellations only; ANC threshold 2,500 miles; rank by passenger growth; explain. Check clarification, unsupported scope, wrong-session context, timeouts, failed refresh and preserved prior results.
6. Update README, TODO, architecture and API docs to match observed capability and any blocker. Final architect/security review must have no unresolved required-flow findings. Deployment remains deferred.

## Acceptance boundary

Source qualification, passing unit tests and an HTTP health response do not prove complete application behavior. Completion requires accepted recent artifacts, correct deterministic API outputs, useful current evidence or explicit unavailable conclusions, successful live AI and contextual HTTP demonstrations, and recorded review/test evidence. Historical presets remain available and truthfully labeled when current-data/model admission is unavailable.
