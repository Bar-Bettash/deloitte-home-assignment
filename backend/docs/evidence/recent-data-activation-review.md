# Recent data activation — 2026-09-27

## Scope and result

The annual CY2024 → CY2025 bundle is accepted locally. This verifies the
deterministic backend; it does not claim deployment or live model integration.
Chat remains last. No new API spending-limit implementation is in scope.

## Live official source evidence

- All 16 selected T-100 year/state exports matched their extracted CSV hashes.
- All 12 on-time monthly archives matched their extracted CSV hashes.
- Selected DataSF passenger records matched the qualified responses.
- Selected FAA PDF and AIP workbook matched; their official edition links were checked.
- No selected-period revision or newer complete period was found.
- Partial 2026 data exists for DataSF, T-100, on-time performance and AIP.
  These partial periods are disclosed and excluded from the annual calculations.

The bulk run exited 0 at `2026-09-27T17:17:02.017315Z`. Its source receipt is
[recent-source-admission.json](recent-source-admission.json), SHA-256
`54e87d1043b0bc6734be581b3f84bd0a381797858c3658a18e0f443f774a1992`.
The bulk process started before the final annual-document classification fix.
Fresh targeted FAA/AIP calls under the final code confirmed exactly the same
hashes and period classifications; the bulk downloads were not repeated.

## Exact activation inputs

- Bundle manifest: `84b35d0deee52004071b958f4d32b64625a29f5fdffed374b9eece73a67d2548`.
- Final reconciliation: `7b64ca88cf09047f731932c499b8f185ebf47076da9e9f539d1c8a9804720887`.
- Source checker: `0623ab0ef432cac34e38a496b12cad2a82d2cba80ab649ef0a7dd09c7567dba6`.
- Acceptance coordinator exited 0 and registered the exact bundle as the default.

## API and review evidence

[Seven unpatched ASGI API calls](recent-api-verification.json) passed: New England
screen, LAX/SNA operations, ANC long-haul share, SFO pressure, omitted-year default,
explicit historical 2024 and saved-result explanation. This exercised the actual
FastAPI route and accepted registry through TestClient, not a deployed server.
API evidence SHA-256:
`fd019a6c68fe8b52a5151d9c4a13dc7513354a723474928129a4a019a65afe14`.

The screen correctly returns partial status with 22 assessable airports and PVC
excluded for incomplete coverage. ANC is 999 / 36,040, or 2.771920088790233%.
Terminal constraints remain unknown; profitability and precise unmet demand remain
not identifiable from these public sources.

The lead architect approved the bounded architecture/correctness review after the
annual-source classification fix (33 focused tests). The data engineer approved
the source, reconciliation, activation and API evidence with no unresolved data
findings. Both reviewers belong to the same agent system as the authors.

Final post-activation full suite: **585 passed** in 130.88 seconds; one existing
Starlette/AnyIO deprecation warning. Ruff and `git diff --check` passed.
Two stale test assumptions were corrected: 2025 is now supported, and the
missing-registry test must isolate its data root after local activation.
