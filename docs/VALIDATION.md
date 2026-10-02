# Validation

Compact release evidence for the submitted application.

## Final RC3 Production release

| Area | Verification | Result |
|---|---|---|
| Backend | Python suite | **971 passed, 2 skipped** (optional local-data fixtures) |
| Frontend | Node UI/globe suite | **178/178 passed** |
| Model admission | Corpus, holdout, assignment, regression, follow-up, pending-comparison and conversation sets | **108/108, 0 errors** |
| Adapter | `model_adapter.py` admission SHA-256 | `e58ea32e77ee946de036ba89d6444e664becfab6638b0d4ff7e0546dfbc2234e` |
| Bare two-airport intent | Production, real model | **40/40** expected outcome |
| Overview shortcut | 2023 “everything / full picture / all of them” flows | **24/24**, **0 Gemini calls** |
| 2023 conversation flow | Production browser flow / typed-vs-button parity | **8/8 / 5/5** |
| Production browser | Desktop / phone QA | **18/18 / 17/17** |
| Production API | Auth, analyses, follow-ups, errors, security | **59/60**; remaining miss is the known stale-result harness ordering quirk |
| Brief questions | Four assignment questions + follow-ups | **8/8 returned 200** |
| Voice | Mic, keyboard fallback, dictate/send, read-aloud start/stop, blocked-mic handling | **PASS** |
| Production log scan | 427 rows, 08:51–09:00 UTC | **0 password hits, 0 API-key patterns, all logged provider responses HTTP 200** |
| Preview protection | Temporary bypass revoked; bypass count 0 | **PASS** |

## Release facts

| Item | Value |
|---|---|
| Production URL | https://deloitte-airport-analyst.vercel.app |
| Vercel deployment | `dpl_8DPdKNGCHaKYsEtXzhwHR2CQwhmK` |
| RC3 release commit | `fe819490652cdab1a0230ea7b3f546fde21b5269` |
| Model | `gemini-3.8-flash` |
| Output-token cap | **1024** |
| Admitted adapter SHA-256 | `e58ea32e77ee946de036ba89d6444e664becfab6638b0d4ff7e0546dfbc2234e` |
| Default data bundle | `annual-2025-r1` |
| Rollback deployment | `dpl_CdrXR3QR8MHSQn7gwe6eSecQt58s` (RC2, `20304e9`) |

The 1024-token cap was retained: increasing it to 1536/2048 increased model deliberation instead of fixing the bare-pair ambiguity. The final prompt rule keeps focused bare-pair usage comfortably below the cap.

## Data / numerical checks

- The four assignment workflows were independently recomputed against the bundled source data and matched the API.
- Accepted snapshots are SHA-256 checked before use.
- FAA’s final CY2025 commercial-service file did not change the relevant New England cohort or enplanements versus the accepted frozen snapshot.
- Missing monthly coverage is unavailable, never estimated or zero-filled.

Machine-readable reconciliation and model-admission evidence remains under `docs/evidence/`.

## Limits

- With a full result context, the model-prompt budget guarantees at least a **400-character** follow-up; the measured boundary on the final prompt is about **440 characters**.
- Temporary provider overload/rate limits are returned as a retryable message with `Retry-After: 10`; provider status details stay server-side.
