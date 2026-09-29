# Planning package publication — 2026-09-26

**Status: PUBLISHED_NOT_APPROVED.** This commit publishes the previously prepared standards-alignment candidate directly to `main`, at the owner's explicit request. It does not record a new independent review, formal plan approval, application implementation, or runtime acceptance.

## Contents and provenance

The master plan is `/README.md`; its topology and execution-mode decision are `/docs/PLAN_GRAPH.json` and `/docs/execution-mode.json`.

`REVIEW_HANDOFF.md`, `REVIEW_STATE.json`, `candidate-check-results.json`, and `native-mode-results.json` in this directory are unmodified historical records from the candidate-authoring session. Statements in those records that publication was blocked, no remote files changed, or the candidate commit was null describe that earlier session, not the current repository. Do not interpret their historical status as a fresh tool-availability check or approval.

The only change from the archived candidate README is the publication-status sentence: it now says published planning candidate, with formal approval still pending. Its analytical rules, steps and review gates are unchanged. The two graph/decision files are byte-identical to the prepared originals. Earlier check outputs bind to the original candidate hashes recorded inside them; they are not relabeled as fresh independent verification.

Publication base: `7995983c8b4d81c830c244255a3f322c6099f6f8`.
Original README SHA-256: `e2115328b1a9cb7dc39ec2a3ad35ab0318578fe17d21240b3a675570af7a60e4`.
Source ZIP SHA-256: `6b6a5639f79bd3b99056ee06a91519f09dcd250dd455de8116dde88cd5ac651c`.
Apply-only patch SHA-256: `f17057101a7c20b135a2f9738555358e8d83a90d3a754ffd9fe994d4c0d83616`. The patch is a transfer vehicle, not a project dependency; its three-file changes are incorporated in this commit rather than stored as a duplicate patch file.

## Published file identities

| Repository path | Git blob SHA | SHA-256 |
|---|---|---|
| `README.md` | `06d6a5868610a0ea89f829ff793b376bd26fc0c3` | `9dd6e207f3ab4ad8f1715bb73554a071fdd1dbf8c3783699c526502658b5257d` |
| `backend/docs/evidence/standards-20260926/REVIEW_HANDOFF.md` | `76ac930b4b68f2cf4a0afbf043eb98e4e181f177` | `bb159942342b59ae049ce5ce1f56eafb21fd86c4df9fe42e5304eb6a67dceb31` |
| `backend/docs/evidence/standards-20260926/REVIEW_STATE.json` | `cc8c5b19ac6945baf4088ddf8bdff6b769b33d93` | `13c256479b609188441f127f80b970fdbbeed22e38889f295a0cb7a9a655f512` |
| `backend/docs/evidence/standards-20260926/candidate-check-results.json` | `5a47fd48a30bb5cae631ec20419ce28952b38a4e` | `7d40cbe25ffea4f1ba19f7ff50453e0d0ae8e021213d250f0fca52735e9845c3` |
| `backend/docs/evidence/standards-20260926/native-mode-results.json` | `09b3658a900eae0e43846c40761f16697b8a508d` | `4524cec6522d311258d40d97583b2774919f611c0058716032b020f596127c6c` |
| `docs/PLAN_GRAPH.json` | `ce7a4ac89cf42feefa992bfcefc68b2601ea651b` | `9ce4f4f6e987be21e90651bf21a2f7a2bbb81b36a55dfb536b6281ebdb46a5cd` |
| `docs/execution-mode.json` | `d47c4f026662d3f91c8fa170f93e49cefee70f6e` | `3402d4f30ab36f75e233402136e5940d01d8858d1431867246b99326a1d53e38` |

This manifest excludes itself to avoid a self-referential hash. Publication verification compares the remote tree's exact file blobs with the prepared local bytes and checks that pre-existing unrelated files are unchanged. That proves storage integrity only, not semantic plan correctness.

## Remaining review work

The domain-agent stage, independent external stage, and final `best`-model stage remain uncompleted for this candidate. Native full-PlanGraph validation, semantic atomicity and executable implementation checks are not established by this publication. Preserve that status and bind any future review to the actual published revision and document hashes. Do not fabricate reviewer verdicts or start application implementation under a claim that the plan is already approved.
