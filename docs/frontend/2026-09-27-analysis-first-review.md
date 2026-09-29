# Analysis-first correction review — 2026-09-27

Status: IMPLEMENTED. Frontend code review, architecture code review and observed localhost QA approved. Full runtime acceptance remains limited by the unverified checks below.

Bound plan: `docs/frontend/2026-09-27-analysis-first-correction.md`, SHA-256 `25215e9f80489d71d221c8569a00b8b701766361f573ede2be552eceefda7a7f`.

Latest user direction supersedes the earlier globe-first composition: analysis occupies approximately 60% of the desktop workspace; Earth supports it in the remaining 40%. Decorative flights are deferred. Backend code and contracts are outside frontend ownership.

## Observed on localhost during implementation

- Real LAX/SNA request succeeds with four measures and a mixed backend conclusion. The interface does not substitute mockup values or claim LAX is worse on every measure.
- At 1440 × 900, the revised two-column layout exposes all four comparison indicators without scrolling. Initial typography and disclosure issues were returned to the implementer.
- Zoom controls reach 120% and 150%; at 150% the full globe limb remains visible. These observations used globe SHA-256 `996b4689d372eacf4b458fe576c6548bd1c14e2c3ffeb2530028a8e9ec955de9`, before subsequent accessibility/detail fixes.
- Initial loading copy now omits the previous-result claim when no previous result exists.
- Screenshots were viewed through CUA in the conversation; no durable screenshot artifact is claimed.

## Findings closed by the final correction

- Preserve expanded scope/follow-up controls and focus when draft changes mark an existing result previous.
- Expose a usable “Try another analysis” disclosure after success.
- Give result title, conclusion and key values stronger visual hierarchy; remove duplicate conclusion.
- Preserve complete per-metric provenance in expandable methodology for specialized views.
- Use one desktop analysis scroll region; preserve mobile composer → result → Earth order and a direct globe anchor.
- Preserve complete globe at every zoom; support keyboard rotation, safe hidden-marker focus, fallback cleanup and truthful returned-data labels.

Final evidence: 68/68 Node tests pass; JavaScript syntax and scoped diff checks pass. Root independently verified that localhost serves byte-identical HTML, CSS, app.js and globe.js for the final hashes below. Frontend reviewer approved the final focus/navigation delta with zero findings. The UI tester's final targeted reload confirmed the Scope target is 44×44px, no enabled visible control falls below 44×44px, and the 390px viewport has no horizontal overflow. See [localhost QA](2026-09-27-analysis-first-qa.md) for workflow and viewport observations.

The observed browser matrix is approved. Actual 200% browser zoom and a live reduced-motion preference remain UNVERIFIED because the available browser harness did not provide reliable controls for those settings. This is not a claim of 100% acceptance or release readiness. No backend files were changed by the frontend workers; no commit, deployment or source-data expansion was performed.

## Final architecture code review — correction and focus repair

Code-review verdict: APPROVED. Scope: the frozen frontend implementation and its architecture, not complete runtime acceptance or release readiness.

Reviewer: independent lead-architect implementation review (the reviewer authored the correction plan but did not implement the application). capability_achieved: static. commands_run: yes.

| Artifact | SHA-256 |
|---|---|
| index.html | 056a997aa469ec9feab9c14276f980ffd30b47ac5df6394dc9e9ecba8761f01a |
| styles.css | 2115ff41b945ae81aec328a56b2dc2636cdfe7a1164640d0af8c6f2784c6300d |
| app.js | b647e85fdbf4bd68bdee8b44ddc89fb255d4d1e103ff1dbcedee0ed015c5d0d5 |
| globe.js | 55fc1a3f5c03b191108a51f718e2dd1d59ac9a8678d107a83ae6d58bfdfef33f |
| ui.test.cjs | c8cacbaf8dca5d1a080edb54176f777eaeef33793760c09f906a79367bfba2f6 |
| globe.test.cjs | 373e534c9d81eb693b3fa58ed56d82f6270779e7d3dbec61bd482613dbb4f681 |

`node --test backend/tests/ui.test.cjs backend/tests/globe.test.cjs` ran independently: exit 0, 68 tests passed. Hashes above were reread from the actual files. Two isolated negative mutations in temporary copies confirmed the repair regressions: replacing `disclosureContainsFocus` with false caused the focused-success test to fail; omitting setup-open from `openSetupAndScope` caused recovery-action tests to fail. Both produced assertion failures/exit 1. Repository implementation files were not mutated by this review.

Closed architectural findings: successful responses preserve any disclosure containing current focus; both empty-result and missing-source recovery open the complete setup/scope ancestry before focusing the action field. Specialized result views retain per-metric lineage. The browser formats backend-returned metrics without calculating airport KPIs. Globe projection and shader share radius/zoom, maximum silhouette radius is 45% of viewport minimum dimension, and interaction remains bounded/event-driven. The 60/40 workspace follows README's airport-screening mission.

No open code-review findings. Parent independently reports the four served frontend byte streams match these files; this reviewer did not itself drive a browser. Localhost observations and the final nav-target retest belong to `2026-09-27-analysis-first-qa.md`, which must be read at its latest revision before runtime acceptance. Earlier QA recorded four real workflows, 60/40 geometry and complete globe at 150%; these observations are not attributed to this static review.

Runtime acceptance remains PARTIAL until its explicit unverified criteria are resolved. Actual 200% browser zoom and live reduced-motion preference testing were not established by the available browser harness; deterministic checks are not substitutes for those live claims. This code verdict does not assert 100% acceptance, physical-device validation, deployment readiness, or accepted 2025 data support. No Git/merge approval is issued.
