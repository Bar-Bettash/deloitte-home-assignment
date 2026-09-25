# Source qualification evidence — 2026-09-25

This record contains public aviation data only. Large source payloads were inspected in `/tmp` and are identified by byte count and SHA-256; they are not committed here.

## DataSF SFO passenger statistics (`rkru-6vcg`)

- Retrieval: `2026-09-25T16:19Z`, HTTP `200`, `text/csv; charset=UTF-8`.
- Request: `GET https://data.sf.gov/resource/rkru-6vcg.csv` with:
  - `$select=activity_period,activity_period_start_date,operating_airline,operating_airline_iata_code,published_airline,published_airline_iata_code,geo_summary,geo_region,activity_type_code,price_category_code,terminal,boarding_area,passenger_count,data_as_of,data_loaded_at`
  - `$where=activity_period >= '202301' AND activity_period <= '202412'`
  - `$order=activity_period,activity_type_code,geo_summary,operating_airline,published_airline,geo_region,price_category_code,terminal,boarding_area`
  - `$limit=5000`
- Response: 761,486 bytes, 3,721 data rows plus header, SHA-256 `46d954d388e0cd2448f99e74f1a0ed1d20967f40f9a5fa0daafd9e93806b53fb`. A second identical request returned the same hash.
- Temporary raw paths were `/tmp/deloitte-sfo-raw-2023-2024.csv` and `/tmp/deloitte-sfo-raw-2023-2024-recheck.csv`. The duplicate/null/period/type checks were rerun independently against the second file and produced the same result. The local checker was `/tmp/analyze_sfo.py`; its declared dimension key and every measured output are recorded below.
- Scope: 24 distinct months (`202301`–`202412`), three activity types (`Enplaned`, `Deplaned`, `Thru / Transit`), and two geography summaries (`Domestic`, `International`). Every month/activity cell has at least one row.
- Declared raw key: activity period plus operating airline/code, published airline/code, geography summary/region, activity type, price category, terminal, and boarding area.
- Key result: 0 duplicate key groups and 0 rows beyond the first duplicate.
- Null result: 0 null/blank values in all 15 selected fields across 3,721 rows.
- Measure result: 0 noninteger or negative passenger counts.
- Source headers identify the fields and types and report `X-SODA2-Data-Out-Of-Date: false`; `Last-Modified` was `2026-09-22T22:04:57Z`.

### Independent official-total reconciliation

The API query for `activity_period` 202307–202406 and `activity_type_code = 'Enplaned'`, grouped by `geo_summary`, returned:

| Scope | API passengers | SFO audited FY2024 total | Difference |
|---|---:|---:|---:|
| Domestic enplaned | 17,983,862 | 17,983,862 | 0 |
| International enplaned | 7,531,674 | 7,531,674 | 0 |

The two-row API response is 130 bytes with SHA-256 `fb68d8be4f07c1764b2ed4b0428b21f83ec8d39a3911df33c7620cbb27f5df57`. The independent source is the Airport Commission's [FY2024 audited financial statement](https://www.flysfo.com/sites/default/files/2024-10/SFO%20-%20Financial%20Statements_PFC_2024.pdf), page 5, table “Passenger and Other Traffic Activity.” The retrieved PDF was 4,332,078 bytes with SHA-256 `81550d242c3f54116c612e5cc415d6033d0a11778e67004efb1361a888308ae1`.

An invalid-column request returned HTTP `400`, JSON error code `query.soql.no-such-column`, 394 bytes, SHA-256 `60225472dc3d80225bba766ab992633fd33fdd6357275074de26788f19d156a1`. This proves the upstream error shape only; adapter timeout, retry, schema-quarantine, and consumer behavior remain implementation gates.

**Verdict:** source data qualification passes for the bounded 2023/24 SFO enplaned trend scope. First immutable ingestion, failure handling, and the actual demand-pressure consumer remain unimplemented.

The 761,486-byte raw CSV is intentionally not retained in Git because it is a replaceable upstream source dump. The repository retains the exact request, two matching response hashes, row/field counts, declared key, validation outputs, and independent reconciliation needed to reproduce and detect drift. A future immutable ingestion snapshot belongs under the planned data manifest after the adapter exists; this planning evidence must not masquerade as that product snapshot.

## BTS T-100 Segment (All Carriers), table `FMG`

- Form URL: `GET/POST https://www.transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=&gnoyr_VQ=FMG`.
- Retrieval: `2026-09-25T16:22Z`.
- Reproduction contract: start a cookie-preserving session with a GET, extract that response's `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, and `__EVENTVALIDATION`, then POST those current values with geography `Alaska`, year `2024`, period `1`, `btnDownload=Download`, and the selected fields below. Reusing stale hidden fields is invalid.
- Selected fields: `DEPARTURES_SCHEDULED`, `DEPARTURES_PERFORMED`, `SEATS`, `PASSENGERS`, `DISTANCE`, `UNIQUE_CARRIER`, `AIRLINE_ID`, `UNIQUE_CARRIER_NAME`, `ORIGIN_AIRPORT_ID`, `ORIGIN`, `DEST_AIRPORT_ID`, `DEST`, `AIRCRAFT_TYPE`, `YEAR`, `MONTH`, `CLASS`, `DATA_SOURCE`.
- Response: HTTP `200`, `application/zip`, 48,584 bytes, SHA-256 `d449cbdc0386f276299ed2ed3e1b9034e2cf8172b7f347976f9ff516ebb59a33`.
- CSV: one member `T_T100_SEGMENT_ALL_CARRIER.csv`, 397,268 bytes, 3,922 data rows plus header, SHA-256 `0247e27229a239a36bb18c32530aa0a1816df7fcf204aeb0f3802a8464d35e5a`.
- Temporary artifacts were `/tmp/deloitte-t100-post-body.bin`, `/tmp/deloitte-t100-alaska-2024-01.csv`, and `/tmp/t100_probe.sh`. The exact bounded request was Alaska / 2024 / January with the 17 selected fields listed above.
- Observed schema contains all selected fields. The first rows confirm aggregate segment records can include zero distance and same-airport origin/destination, so qualification/ingestion must preserve and explicitly disposition those cases rather than assuming every row is a flown point-to-point passenger segment.
- Two additional bounded full-year 2023 state extracts succeeded for Alaska (522,450-byte ZIP, SHA-256 `5c4cc6758e92c6cfa99dfb082a94922ea401623103837c024b10248f7a048d15`) and Connecticut (48,355-byte ZIP, SHA-256 `d7f082b2f5a8c4d2ea058425b71cfea64489ddfb92e24c8ded6f311e1f5460e6`). Concurrent Maine and Massachusetts requests returned HTML rather than ZIP, demonstrating that content type/signature checks are mandatory even on HTTP `200`.

**Verdict:** generated extraction is reproducible and the earlier extraction blocker is closed. The required geographic set is AK, CA, CT, ME, MA, NH, RI, and VT for both 2023 and 2024. Only full-year AK-2023 and CT-2023 plus the bounded AK January 2024 sample completed. The other 14 state-years, the state-filter direction semantics, cross-state union/deduplication, class/data-source disposition, and total reconciliation remain open; traffic, ANC share, and screening calculations remain blocked.

## BTS Reporting Carrier On-Time Performance, table `FGJ`

- Index URL: `GET https://transtats.bts.gov/PREZIP/`.
- Retrieval: `2026-09-25T16:24Z`, 163,596 bytes, SHA-256 `bd39cbf70abe16fc8d8cb851e4bab0f53756c4469b9fe14b27fad926cffef542`.
- Inventory: the official index lists one canonical `On_Time_Reporting_Carrier_On_Time_Performance_1987_present_YYYY_M.zip` for every month of 2023 and 2024 (24 canonical files). It also lists a parenthesized-name alias for December 2023.
- Inventory method: replace the index's `<br>` separators with newlines, select filenames matching `On_Time_Reporting_Carrier_On_Time_Performance.*_202(3|4)_[0-9]{1,2}.*\.zip`, sort unique names, then require exactly months 1–12 for each year after alias normalization. The downloaded index was `/tmp/deloitte-prezip-index.html`.
- December alias resolution: HEAD requests to both December 2023 URLs returned HTTP `200`, `application/x-zip-compressed`, 28,818,502 bytes, the same `Last-Modified` timestamp, and the same ETag. Treat them as aliases for one partition, never two inputs.
- Boundary check: the canonical December 2024 URL returned HTTP `200`, `application/x-zip-compressed`, 30,150,370 bytes, byte-range support, and `Last-Modified: 2025-03-14T16:19:19Z`.

**Verdict:** the exact 24-file reporting-carrier inventory and December alias are resolved. Full bytes/checksums, extraction, schema/row counts, duplicate counts, reporting population, and per-field coverage were not collected, so the operational-data gate remains blocked.
