# Integrated acceptance: 2026-09-29

## Revision

- `git rev-parse HEAD`: `104acfcb5c424a0f830aa735cc41b34aa0563846`. This is the merge of the verified backend branch `9066623` with the frontend snapshot `ca19241`.
- The acceptance worktree was first checked out at `ade0395`. It was moved to `104acfc` with `git reset --hard 104acfc` before any check ran. That worktree had no local changes.
- Runtime: Python 3.11 venv with pinned `backend/requirements.txt`, ruff 0.16.9, Node v22.22.2.
- Machine-readable record: [integrated-acceptance-20260929.json](integrated-acceptance-20260929.json). It holds 41 HTTP calls, their key figures, source IDs and comparisons. It contains no cookie or session values.

## Claim table

| Claim | Status on 2026-09-29 |
|---|---|
| Bundle `annual-2025-r1` packaged, registry-bound and hash-verified offline | **Locally tested: pass** |
| Merge left acquisition, manifest, calculator and source-check code unchanged | **Locally tested: pass** (empty diff) |
| Backend suite, ruff, `git diff --check`, Node UI tests | **Locally tested: pass** |
| Four workflows, default year, 2024, explain and error paths over real HTTP (uvicorn) | **Locally tested: pass** |
| Every figure identical to `recent-api-verification.json` | **Locally tested: pass, 0 discrepancies** |
| Live official-source freshness (T-100, on-time, DataSF, FAA, AIP) | **Not re-verified today: blocked.** Outbound access to official hosts returns 403 by policy. The last live check was on 2026-09-27 (see the receipt below). |
| Live model (free-text interpretation) | **No.** The model runtime is not admitted. Free text returns `503 ai_unavailable`. 0 model calls. |
| Deployed | **No.** Loopback-only local server. |

## Step 2: integrated data bundle

### Merge scope

```
git diff --stat 9066623 HEAD -- backend/app/sources backend/app/calculations backend/scripts backend/data
```

This produced empty output (exit 0). Relative to `9066623`, the only changed paths are:

- `backend/app/static/*` (UI and assets)
- `backend/tests/{ui,globe}.test.cjs`
- `docs/frontend/*`
- `backend/docs/specs/2026-09-27-recent-data-expansion.md`
- `backend/docs/evidence/recent-source-check.json` and `recent-source-qualification-20260927.md` (added evidence notes)

No Python module under `backend/app`, `backend/tests/*.py`, `requirements.txt` or `pyproject.toml` changed. **The admission gates do not need to be re-run** beyond the offline hash re-verification below.

### Offline verification through the repository's own code

The script `verify_bundle.py` is an ad hoc scratchpad script and is not committed. It imports repository functions only, and its output is stored in the JSON under `bundle_offline_verification`.

| Check (repository code path) | Result |
|---|---|
| `python -m app.sources.bundle --check-candidate annual-2025-r1` (cwd `backend`) | `{"status":"valid","manifest_sha256":"84b35d0d…d2548"}`, exit 0 |
| `load_bundle("annual-2025-r1")` | 2024 → 2025, cohort 23 (includes EWB and PVC) |
| `dispatch._read_accepted_bundle(None)` and `("annual-2025-r1")` | Both resolve to `annual-2025-r1` with sha `84b35d0deee52004071b958f4d32b64625a29f5fdffed374b9eece73a67d2548`. This equals `accepted.json` and its default. |
| Per-source manifest and data sha256, recomputed independently (`sha256` of the bytes) | datasf, t100, ontime, faa (`source.pdf`) and aip (`source.xlsx`): all 5 manifests and 5 data files match. Manifest `snapshot_id` matches. |
| Bundle evidence `evidence_2025.json` | `493397be…b5f6` matches |
| `scripts.check_source_status.verify_qualification_binding` (offline, no probe) | pass |
| `app.evidence.load_evidence(bundle=…)` | pass |
| `app.sources.source_check.validate_source_check_file(bundle, recent-source-admission.json)` | pass (receipt checked `2026-09-27T17:17:02.017315Z`, within the 7-day window) |
| `scripts.reconcile_recent.validate_reconciliation_file(bundle, recent-reconciliation.json)` | pass |
| `scripts.accept_bundle.promote_bundle(...)` against a **temporary copy** of `backend/data` | Returned `False` (already the default). The registry bytes were unchanged. The real registry was never written. |

Receipt hashes recomputed today match the activation review:

- `recent-source-admission.json`: `54e87d10…1992`
- `recent-reconciliation.json`: `7b64ca88…0887`
- `scripts/check_source_status.py`: `0623ab0e…dba6`
- `recent-api-verification.json`: `fd019a6c…fe14`

`git status` was clean after all checks.

## Step 3: acceptance commands and outcomes

| Command | Outcome |
|---|---|
| `PYTHONPATH=backend /tmp/claude-0/v/bin/python -m pytest backend/tests -q -p no:cacheprovider` | **583 passed, 2 skipped, 1 warning** in 52.86 s, exit 0 |
| `/tmp/claude-0/v/bin/ruff check backend --ignore EXE002,SIM905` | All checks passed |
| `git diff --check`, and `git diff --check 9066623 HEAD` | Clean, exit 0 |
| `node --test backend/tests/*.cjs` | **88 tests, 88 pass, 0 fail** |

Test notes:

- **Skips.** Two tests are skipped: `test_aip.py:61` and `test_ontime.py:270`. They read external raw inputs under the macOS `/private/tmp/…`, which are absent in this Linux container. The total of 585 equals the prior record of "585 passed", so no test was lost. Those two tests were not executed here.
- **Warning.** The one warning is the existing Starlette TestClient `anyio.abc.BlockingPortal` DeprecationWarning.

### Real-HTTP run

Server command:

```
python -m uvicorn app.main:app --host 127.0.0.1 --port 33783
```

- The server ran from `backend/` with an environment of only `PATH`/`HOME`. It was launched detached and its PID was recorded.
- The client was httpx with `trust_env=False`, `Origin: http://127.0.0.1:33783` and a persistent cookie jar.
- Afterwards the server was stopped with `kill <PID>`. The log shows a clean shutdown and the port is closed.
- The UI also loaded over HTTP: `GET /`, `/static/app.js`, `globe.js`, `styles.css` and an Earth texture all returned 200.

**All 41 HTTP calls returned their expected status.**

| Workflow | Explicit 2025 + bundle | Year omitted (default) | Explicit 2024, no bundle (historical path) | 2024 + `bundle_id` |
|---|---|---|---|---|
| New England screen | 200 `partial`, 22 ranked | identical to explicit | 200 `partial`, 21 ranked (2024 cohort, no EWB), PVC excluded | 422 `unsupported_scope` (comparison-year-only) |
| LAX vs SNA congestion | 200 `ok` | identical | 200 `ok`, ontime `2024` | 422 `unsupported_scope` |
| ANC long-haul ≥3000 mi | 200 `ok` | identical | 200 `ok`, 950 / 40,017 | 200 `ok`, 950 / 40,017 (same value from both snapshots) |
| SFO pressure | 200 `ok` | identical | 200 `ok` | 422 `unsupported_scope` |

In the table above, "identical" means that after removing `request_id` and `result_id`, the default and explicit-2025 responses match byte-for-byte. The default resolves to `year: 2025` and `bundle_id: annual-2025-r1`.

### Key figures, identical to `recent-api-verification.json`

- **ANC 2025:** long-haul share = 999 / 36,040 = **2.771920088790233 %**. The lower and upper bounds are equal and `unknown_distance_departures` is 0.
  - Source: `t100-db5d86dd3af352f0a645`, snapshot `t100-6bedff87…`, period `2024-2025`.
- **New England 2025:** status `partial`, **22 assessable** airports. Exclusion: `PVC: 2024 coverage is incomplete (missing months: 12)`. PVC is absent from the rows. **EWB is included** (rank 17).
  - Top ranks: HVN 1 (74.76); BGR and PWM tied 2; BOS 4.
  - HVN passengers 737,565, growth 25.342 %, occupancy 737,565 / 970,305.
- **LAX/SNA 2025:** on-time source `ontime-93a752a176debb909834`, snapshot `ontime-04fee95d…`, period `2025`. Both airports have 12 of 12 months.

  | Measure | LAX | SNA |
  |---|---|---|
  | Cancellation | 1,315 / 190,472 = 0.690 % | 471 / 44,997 = 1.047 % |
  | Diversion | 579 / 190,472 | 131 / 44,997 |
  | Mean departure delay | 13.80 min (n = 188,578) | 15.17 min (n = 44,395) |
  | Mean taxi-out | 17.73 min | 16.05 min |

- **SFO 2025:**

  | Measure | Value |
  |---|---|
  | DataSF enplaned | 27,250,806 |
  | T-100 passengers | 26,477,602 |
  | Seats | 32,169,113 |
  | Departures | 187,325 |
  | Growth | 1,188,993 / 25,288,609 = 4.70 % |
  | Occupancy | 82.31 % |
  | Cancellation | 1,080 / 144,495 |
  | Diversion | 442 / 144,495 |
  | Mean departure delay | 14.22 min |
  | Mean taxi-out | 21.20 min |
  | `sfo_pressure` | −1.2247 pp |

  - The limitations state *"Profitability and quantitative unmet demand are not_identifiable."* The summary states *"precise unmet demand is not identifiable"*.
  - Terminal evidence (`sfo_t3_current`, `sfo_cip_2025`) is labelled "Status unknown".
  - Sources: `datasf-c647cd19b7e499067ce5`, `t100-db5d86dd3af352f0a645` and `bts_ontime-93a752a176debb909834`.
- **Terminal status** for HVN, BGR and PWM in the screen evidence: every claim starts with "Status unknown".
- **Cross-snapshot consistency:** the 2025 SFO growth denominator (25,288,609) equals the 2024 historical SFO T-100 passenger figure. ANC 2024 is 950 / 40,017 in both the historical snapshot and the bundle's baseline year.
- **PVD passengers:** the omitted-year request returns 2025 / bundle / 2,113,478. Explicit 2024 returns the historical path with no bundle: 1,983,074, source `t100-e046736d0936743c3f6d`.
- **Referential integrity:** every metric `source_ids` entry in every 200 response resolves to that response's `sources` or `evidence`.

The six evidence-comparable calls were compared after removing `request_id` and `result_id`: New England, LAX/SNA, ANC, SFO, omitted-year PVD and 2024 PVD. **0 differences** against `recent-api-verification.json`.

### Follow-up and error behaviour, over real HTTP

| Case | Status / code |
|---|---|
| Explain with `context_result_id` of the latest ANC result | 200. Rows, sources and scope are identical to the stored result; the top-level keys match the evidence explain call. |
| Explain with a random UUID (valid session) | 409 `result_mismatch` |
| Explain with a stale ID (after a newer SFO result) | 409 `result_mismatch` |
| Explain with no session cookie | 409 `session_expired` |
| Free-text follow-up with a stale context ID | 409 `result_mismatch` |
| `Origin: http://evil.example` | 400 `invalid_request` |
| `Host: evil.example` | 400 `invalid_request` |
| Origin port mismatch | 400 `invalid_request` |
| `Content-Type: text/plain` | 415 `unsupported_media_type` |
| Malformed JSON | 400 `invalid_json` |
| Unknown field; both `message` and `analysis`; neither | 422 `invalid_request` |
| Explain without context | 422 `invalid_request` |
| Out-of-scope airport (JFK) | 422 `invalid_request` (rejected by the contract before dispatch) |
| Year 2023 with the bundle | 422 `unsupported_scope` |
| Unaccepted bundle ID | 422 `unsupported_scope` |
| Free-text `message` (model not admitted) | 503 `ai_unavailable`, 0 model calls |
| Blank message | 422 `invalid_request` |

## Findings

**No data or figure discrepancies.** None of the following is a regression against the evidence.

1. **Minor wording, LAX/SNA summary.** In `backend/app/dispatch.py:815`, when each airport is higher on some indicators, `_comparison_summary` returns: "Mixed picture: indicators favor LAX and SNA across 4 comparable operational measures."
   - "Favor" is ambiguous for strain indicators, where higher is worse.
   - The per-metric `comparison_direction` values are correct.
   - Expected: wording such as "LAX is higher on 2 and SNA on 2 of 4 operational-strain indicators."
2. **One snapshot, two source IDs.** The same on-time snapshot `ontime-04fee95d…` appears under two IDs:
   - `ontime-93a752a176debb909834` in operations responses (`dispatch.py:844-845`).
   - `bts_ontime-93a752a176debb909834` in SFO pressure (`dispatch.py:855`, lineage ID `bts_ontime` from `calculations/sfo.py:296`).

   Each response is internally consistent, but a client joining across responses by `id` would treat them as different sources.
3. **Admission receipt expiry.** The source-admission receipt expires for promotion purposes after `2026-10-04T17:17:02Z`, because of the 7-day `_MAX_AGE` in `app/sources/source_check.py:33`. Serving does not depend on it. Any re-promotion after that date needs a live recheck, which cannot run from this environment.
4. **Suite skips.** Two tests are environment-dependent skips (see Step 3). Their coverage is still only from the original author machine.
