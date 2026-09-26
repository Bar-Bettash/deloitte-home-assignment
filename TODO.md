# Execution TODOs

**Runtime work: TODO — NOT RUN.** This checklist follows README plan revision 4, the bounded local-demo scope. It is not a second specification. Earlier review receipts remain historical; no missing run has been converted into a pass.

## Written corrections completed in revision 3

- [x] Define bounded scope and safe UI errors instead of automatic retries, alternate providers or open-ended recovery.
- [x] Check `.env` and raw-data ignore rules separately; one ignored path cannot make the other pass.
- [x] Replace whitespace-only API/UI acceptance with behavioral checks and actual browser evidence.
- [x] Require content/coverage checks and source review for evidence/corpus files; JSON syntax alone is not acceptance.
- [x] Assign baseline and candidate evaluation modes, acceptance handling and output recording to the shared evaluator step.
- [x] Separate dependency bootstrap checks from lint/typecheck over source files that exist later.
- [x] Classify the final live demo as an authorized execution, not a documentation-only act.
- [x] Bound long-haul analysis to complete distance coverage; otherwise return insufficient data. No uninformative range counts as the ANC demonstration.
- [x] Keep deterministic preset integration possible even if the model is not admitted. Final AI acceptance still requires a successful real model evaluation.

These are changes to the plan, not completed application features or tests.

## Connection design completed in revision 4

- [x] Research the applicable API/module/error/UI owners in claude-code-config and record their pinned source references plus explicit English-message/route-version adaptations.
- [x] Add the endpoint → backend handler → frontend consumer map to README; define the wire request/result/error examples in docs/API_UI_MAP.md.
- [x] Assign contract, session, route and rendering ownership to the existing implementation steps; keep one analysis endpoint and no browser-driven source refresh.

These checkmarks describe written design, not running routes, verified model calls or independent approval.

## Needs implementation or a running application

- [ ] Install the pinned toolchain in an isolated environment; verify config, then lint/typecheck existing source targets and run behavioral tests.
- [ ] Acquire/validate the qualified DataSF, FAA, T-100 and CY2024 on-time data. Respect the declared scope, limits, duplicates and missing-month rules. Prove the real DataSF API output feeds SFO.
- [ ] Verify deterministic metrics with hand-checked fixtures: ranking, performed departures, long-haul counts, operational indicators and the SFO proxy. Missing values must not become zeros.
- [ ] Implement and test docs/API_UI_MAP.md in contracts.py, main.py and app.js: exact request union, bare success, non-2xx safe errors, typed units, opaque-cookie/result binding and one query route. Verify malformed, foreign/stale, busy and timeout cases without provider/state side effects.
- [ ] Run the first SFO API/browser slice; record GET /, GET /static/app.js, one GET /health and POST /api/query in the browser trace. Compare displayed values/source IDs with the returned object; exercise errors and confirm source-detail expansion sends no request.
- [ ] Review the top-three terminal evidence notes and SFO note against actual official passages; validate the file's expected coverage and record honest unknowns.
- [ ] Freeze/check the 30-case intent corpus. Run the shared evaluator in baseline and authorized candidate modes; record failures, usage, costs and latency. Keep AI disabled if it fails.
- [ ] Exercise supported follow-ups, unsupported scopes, busy/timeouts, expired sessions and provider errors. Preserve a clearly labeled previous result; do not return fabricated or stale success.
- [ ] Test keyboard/narrow-screen use, readable tables and UI result/error states; capture actual browser evidence.
- [ ] Run the final real API/model/browser demonstration protocol and a clean-environment setup. Required supported examples must succeed; safe errors cover failures, not the whole submission.

## Needs actual review/validation runners

- [ ] Native PlanGraph/execution-mode checks against the current files, with actual outputs and revision identity. Local author-side graph checks are not native factory validation.
- [ ] Applicable real domain-agent, independent Codex/Gemini and final-model reviews when the authorized environment is available. Record findings and dispositions; do not claim a missing or author-only review is independent approval.

Do not repeatedly probe unavailable runners or provision infrastructure just for this checklist. Their absence does not prevent further written-plan work. No background runs are scheduled.

## Closing an execution item

Record what ran, its actual result, and the assessed commit/snapshot. An error on an out-of-scope request can be correct behavior. An error on a required supported demo is incomplete delivery. A candidate or source change requires the affected checks to run again.
