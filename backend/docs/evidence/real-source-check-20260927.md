# Real source and API check — 2026-09-27

The [machine-readable receipt](real-source-check-20260927.json) records the exact live HTTP requests, accepted snapshot hashes, API request/response objects, and the tested app identity. Verification began at `2026-09-27T07:51:10Z`.

## Live public-source calls

- **DataSF Socrata (`rkru-6vcg`):** the pre-count, bounded page request, and post-count each returned HTTP 200. The window contained 3,721 rows and all 48 required Enplaned month/geography cells. Independent aggregation of the live CSV matched all 24 monthly values in both the local calculation and real app route. Annual Enplaned passengers were 24,992,086 (2023) and 26,054,586 (2024), for 4.251345806% growth.
- **FAA CY2024 commercial-service airport-enplanement PDF:** fresh HTTP 200 response, 369,732 bytes. SHA-256 matched the retained accepted snapshot (`458273de65b91eb0e7c26cc372616f812320b2dad163909edefd89c13cdff251`); 513 table rows and the exact 22-airport New England cohort validated.

## BTS snapshots and API behavior

- **T-100:** rechecked the accepted local snapshot checksum and row count. Its manifest records 16 archives validated during the earlier import; archive bytes were not re-downloaded or revalidated in this check. The snapshot contains 60,467 rows; PVC lacks December 2024.
- **Reporting Carrier On-Time:** verified the accepted local snapshot with 372,550 rows, 36 airport-month cells and 12 archives. February–December were acquired for this snapshot; January was retained/reused from a prior qualified archive, and its acquisition timestamp was not recovered. This was not a fresh on-time download.
- The real app `TestClient` returned HTTP 200 for all eight supported structured cases using accepted local snapshots, without provider or data mocks. PVC-only 2024 passenger ranking returned HTTP 422 `insufficient_data`.
- Direct DuckDB calculations matched ANC long-haul at 950/40,017 and all eight LAX/SNA operational indicator numerators, denominators and values.
- Source `retrieved_at` reports source acquisition time only. For retained bulk data with no known acquisition time it remains null; local import time is separate metadata and is not projected as retrieval time.

This verifies the tested live public sources, local snapshots and structured API behavior. It does not enable or validate model-backed free text; that path remains disabled.

After correcting the provenance projection, all nine app cases were rerun at `2026-09-27T07:57:55Z`; numeric values, scope and exclusions remained identical. The full backend suite then passed 212 tests with one Starlette deprecation warning. The receipt includes the final application-file checksums and response objects.
