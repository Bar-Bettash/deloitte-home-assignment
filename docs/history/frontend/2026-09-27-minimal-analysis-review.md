# Minimal analysis refinement

## User task

Help an airport-modernization analyst choose where to investigate, compare reported signals, and verify the evidence before drawing a conclusion. The analyst needs a clear question, period and population, exact measures, readable comparisons or trends, and accessible limitations. The globe provides geographic context.

This refinement uses existing response data and the existing query endpoint. Backend implementation, datasets, calculation contracts, and globe code are outside its scope.

## Baseline observed on localhost

At 1440 × 900 the comparison result repeated question selection, Analyst, Current analysis, requested scope, Current result, and completion feedback before its airport heading at approximately y=355. Evidence followed verbose coverage notes. SFO's existing monthly line had no visible date or value axis; its permanently expanded 24-row table pushed evidence further down the page.

Screenshots:

- [SFO before refinement](screenshots/minimal-analysis/before-sfo.png)
- [SFO chart before refinement](screenshots/minimal-analysis/before-sfo-detail.png)

Baseline verification: `node --test --test-reporter=dot backend/tests/ui.test.cjs backend/tests/globe.test.cjs` exited 0, 68 tests. This is baseline evidence, not verification of the upcoming changes.

## Implementation and acceptance

Pending. The approved plan is `2026-09-27-minimal-analysis-plan.md`; implementation is assigned to Luna, with independent frontend and architecture review followed by localhost QA. Final evidence will be recorded here after the candidate is frozen.
