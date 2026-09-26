# Execution TODOs

**Status: TODO — NOT RUN.** Recorded at the owner's request on 2026-09-26: “Ok keep it as a todo, everything that needs a real code to run”.

This is the execution checklist for the [master plan](README.md), recorded against plan commit `69511e480b7187deebbede7e0ec0061098dd7d9d` (README blob `06d6a5868610a0ea89f829ff793b376bd26fc0c3`). The README and its task graph are unchanged by this checklist. Existing historical review/check records retain their original scope and hashes.

Execution-dependent work stays pending until the corresponding implementation or runner exists. It does not prevent further work on the written plan. Deferral is not a passing test, reviewer approval, a waiver of a gate, or authorization to implement/deploy. No background execution or repeated runner-availability checks are scheduled.

## Needs a review/validation runner, not the airport application

- [ ] Run native full-PlanGraph validation and the execution-mode validator against the bound README/graph/mode files; record actual outputs and file identities. Do not reuse earlier candidate checks as evidence for changed bytes.
- [ ] Execute the applicable planning skills and actual domain-agent review, independent Codex/Gemini review, and final `best`-model review in the plan's required order. Apply valid findings and re-review affected revisions within the existing bounds. Record unavailable or unexecuted stages as pending, never approved. Resume when the authorized runners are available; no new infrastructure is required merely to keep this TODO list.

## Needs implemented code or a running application

| TODO | When it can run | Evidence needed before closing |
|---|---|---|
| Toolchain, lint, typecheck and test baselines | Installation/configuration exists; source checks begin only after their targets exist | Actual command, exit code, nonzero checked-file/test count; failing baseline where applicable. Empty targets or collection/configuration errors are not successful verification. |
| Source acquisition and snapshot validation | DataSF, FAA, T-100 and on-time adapters exist | Real DataSF API output consumed by SFO; qualified source periods/populations; remaining CY2024 on-time months; duplicate, missing-month, invalid-record and failed-refresh cases. |
| Deterministic calculations | Calculation functions and tests exist | Hand-checked ranking, performed departures, long-haul weighting/bounds, congestion indicators and SFO proxy; meaningful boundary/failure tests and targeted wrong-implementation controls. |
| First SFO API and browser slice | Endpoint and UI exist, plan steps 2.4–2.5 | Behavioral API assertions plus browser interaction/screenshot evidence showing accepted snapshot values. `git diff --check` proves neither outcome. |
| Intent and follow-up evaluation | Model adapter, validated dispatcher and session handling exist; authorized model access is available | Fixed evaluation cases and baseline/candidate results; four examples plus nearby questions; context preservation/recomputation, unsupported input and model-failure behavior; observed usage and latency within declared limits. |
| UI quality and state checks | The real results interface is running | Applicable accessibility, data-state, responsive and visual audits, with functional evidence and screenshots. Do not add canvas/motion tooling when that surface is absent. |
| Integrated acceptance and reproducibility | Required application components are integrated | Real API/model/browser flows, a supported follow-up, missing-data and model-failure cases, and a fresh-environment setup run. Separate authorized live/metered execution from documentation-only evidence recording. |

## Written-plan corrections remain distinct

The last audit also identified three corrections that do **not** require a working application to specify. They remain open until the plan itself is amended; their execution evidence belongs to the TODOs above:

- [ ] Replace whitespace-only verification targets in steps 2.4–2.5 with the actual API/UI evidence requirements and consistent file scopes. Documentation steps 0.1 and 6.2 likewise need substantive review/setup evidence beyond whitespace checking.
- [ ] Correct step 1.0 so dependency/configuration bootstrap does not require linting source directories before they exist. Keep installation/configuration validation distinct from later source lint/typecheck.
- [ ] Correct step 6.3's live-acceptance scope: distinguish paid/live execution from evidence-only recording and declare the actual checks, budget and authorization boundary.

## Closure

Close an item only after recording what ran, the observed result and the exact assessed revision/snapshot. A new plan revision requires rebinding the affected checks and reviews. The absence of runtime evidence while planning is expected; the absence of evidence at final acceptance is not a pass. Formal plan approval remains pending, and application implementation is not claimed.
