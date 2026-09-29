# Standards-alignment candidate — not approved

The requested main-branch publication and real reviewer loop could not execute in the authoring session. Its GitHub actions exposed reads only; `gh`, `claude`, `codex`, and `gemini` were absent from PATH. No credentials were read and no remote file was changed. No reviewer identity has been simulated.

## Prepared files

- `README.md`: departures semantics; per-step declarations; owners, scopes, budgets and refusal conditions; evaluation and UI-review requirements; explicit pending status.
- `docs/PLAN_GRAPH.json`: 42-node native-shape graph with 74 dependency references.
- `docs/execution-mode.json`: explicit graph-mode decision.

The application architecture remains a local FastAPI/static-UI application. The graph is a build artifact, not a runtime engine. All application functions, tests and data-qualification commands remain planned. The unknown-resolution table explicitly distinguishes prospective probes from commands already executable; the candidate does not assert that requirement has passed.

## Actual verification

The exact upstream execution-mode validator was reproduced locally and its Git blob checked against `5d99e0d4bd574e8d70f0fbc99ed5fb3153ab9560`. Six cases returned expected outcomes, including four malformed/contradictory cases and a genuine sequential case. This proves only execution-mode record validation. The native full PlanGraph inspector was not run.

Local structural checks found 42 matching step headings/nodes and 74 backward-resolving dependency references. Five intentionally malformed graph projections were rejected. This is not semantic or independent review. `git diff --check` passed on a packaging fixture. The patch was applied to a second fixture and all three files matched exactly.

## Resume

In an authenticated product checkout, verify the base and existing changes before applying `main-standards.patch`. The patch expects the README blob `67f0d7847bab6198d6b9a9d202f418e2dfabfcd2`; do not overwrite concurrent work. Run the pinned native PlanGraph inspector and execution-mode validator, then the complete owner-defined review sequence. Bind actual verdicts to the published candidate SHA and document hashes. Retain all findings and fix only validated defects; never reword tool absence as approval.

The current candidate has not satisfied formal approval. Do not start application implementation from this handoff. No background work is scheduled.
