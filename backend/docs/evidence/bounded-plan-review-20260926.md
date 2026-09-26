# Bounded prototype plan review — 2026-09-26

**Scope: author-side written-plan review and local deterministic checks. No independent-agent approval or application execution is claimed.** The owner asked for a sensible home assignment with bounded inputs and clear UI errors rather than exhaustive recovery machinery.

Input revision: `48dda0ce21f6c432e746e663b56a17214452cfdc`.

## Review and fix passes

1. Applied the prior static findings: separate ignore checks; behavioral API/UI acceptance; substantive evidence/corpus validation; shared baseline/candidate evaluator ownership; bootstrap checks that do not lint absent source; correctly classified live final acceptance.
2. Made the failure contract explicit: limited airports/years, one action, two-airport comparisons, one active query, bounded model/refresh work, no automatic retry/provider switching, and safe UI errors that preserve a labeled previous result. Missing long-haul distance now returns insufficient data instead of a range; the successful ANC demo still requires a numeric answer.
3. Re-read dependent declarations. Corrected the first API test-file scope and corresponding graph reads; kept preset integration independent of successful model admission while retaining admission before final AI acceptance; corrected the DataSF command's root-relative Python path. Refreshed the graph's dependency read scopes. No new runtime service or agent was added.

The written scope is now suitable for a bounded prototype in this author's assessment. No further material contradiction was identified in this pass; this is not a guarantee that implementation will expose no new issue. Actual runner-dependent checks stay in TODO. Prior formal review receipts remain historical.

## Measured checks on the candidate bytes

- 42 individually headed steps matched 42 graph nodes on goals, dependencies, file scopes and human-gate flags.
- 75 dependency references resolved; the local checker detected no cycles. This is not the native full-PlanGraph validator.
- Six deliberately invalid graph variants were rejected: missing dependency, cycle, duplicate node, escaping path, wrong human-gate type and changed goal.
- 33 shell command snippets passed `bash -n`. This proves shell syntax only, not execution or behavioral adequacy. Source/browser review descriptions were not misrepresented as shell commands.
- The corrected ignore command failed in isolated raw-only and env-only fixtures (rc=1), and passed with both paths ignored (rc=0).
- Written arithmetic was recalculated: ranking 30/50/70, SFO growth gap +5 percentage points, exact known-distance long-haul example 8/20 = 40%.

## Content binding

| File | SHA-256 | Git blob SHA |
|---|---|---|
| README.md | bf37fca23f36f1db0ad316bb34dac816357e7a7c60902dc07f39c7e05616549c | 528c6c9ae8adf032513dd4d762ec21376171beb3 |
| docs/PLAN_GRAPH.json | 987021f4ed16d45f37b9dcc93bfad940f3087774478d9732e781036c5b12a150 | a0ea9d9a2bc1b31c2a98a29f631037db6d4d7916 |
| TODO.md | 3af12a1e78a48ebab08a6c025a8caa96dd9ff12bc552754481053b02c07b2603 | 22db340c395476589b8515daf5dd64bb175465fb |

The execution-mode file is unchanged (blob `d47c4f026662d3f91c8fa170f93e49cefee70f6e`); its 42-unit decision still matches the graph. This receipt excludes itself to avoid self-reference. Publication must verify remote file blobs against these bytes and preserve unrelated existing files.

## Not run

Application tests, source reacquisition, live model evaluation, browser execution, native full-PlanGraph validation and independent model/agent reviews. An error is an acceptable response for an unsupported or failed request, but cannot replace every required successful demonstration. Do not mark these execution TODOs complete from this document.
