# Validation

Compact release evidence for the submitted application.

## Latest hosted RC2 check

| Area | Verification | Result |
|---|---|---|
| Backend | Python suite | **908 passed, 1 skipped** |
| Frontend | Node UI/globe suite | **166/166 passed** |
| Hosted browser | 8 screen widths, 80–200% zoom, keyboard-only use, chat, clarification, voice/read-aloud, sign-out | **104/104 passed** |
| Hosted API | Auth, analyses, follow-ups, errors, security | **59/60 harness checks**; the one reported failure used an older result and the app correctly rejected it. The same follow-up against the latest result passed. |
| Accessibility | Automated contrast + keyboard/a11y probes | **PASS**, 0 contrast failures |
| Access gate | Correct/wrong password, authenticated flow, sign-out, unauthenticated blocking | **PASS** |
| Secret handling | 657 Preview log rows (13:31:07–13:44:56 UTC) scanned for the password; 0 hits, with a planted positive control | **PASS** |
| Voice | Dictation, typed fallback, follow-up, read-aloud | **PASS** |
| Model admission | Pending-comparison 10/10, new-airport follow-ups 10/10, corpus 30/30, holdout 12/12, assignment wording 8/8, chat regression 20/20 | **90/90, 0 errors** |
| Fresh review | Fresh-context review of the RC2 candidate | **0 findings** |

The hosted Preview was re-protected after QA: the automation bypass was removed and unauthenticated access again redirected to Vercel protection.

## Release facts

| Item | Value |
|---|---|
| Production URL | https://deloitte-airport-analyst.vercel.app |
| Hosted RC2 candidate | `20304e920286407dbc2f132501972b79652d22e0` |
| Model | `gemini-3.8-flash` |
| Admitted adapter SHA-256 | `1873f9e4304110be438fa69d79f0e09948f863a138a3cdd9fa1be6f327f9451d` |
| Default data bundle | `annual-2025-r1` |

## Data / numerical checks

- The four assignment workflows were independently recomputed against the bundled source data and matched the API.
- The accepted snapshots are SHA-256 checked before use.
- FAA’s final CY2025 commercial-service file did not change the relevant New England cohort or CY2025 enplanements versus the accepted preliminary snapshot.
- Missing monthly coverage is treated as unavailable, not estimated or zero-filled.

Machine-readable reconciliation and source-check artifacts remain under `docs/evidence/` because the offline verification scripts use some of them directly; the old narrative QA diaries were removed.

## Accepted limitation

At 1440 px, the temporary “Loading interactive Earth…” text can overlap the footer for about a second while the globe loads. It does not affect interaction or the rendered globe.
