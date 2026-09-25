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
- Retrieval: `2026-09-25`. Each partition used a fresh cookie-preserving GET, that response's `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, and `__EVENTVALIDATION`, then a POST with one state, one year, period `All`, `btnDownload=Download`, and the selected fields. Reusing stale hidden fields is invalid. Every retained response was HTTP `200`, passed ZIP-signature validation, and contained one `T_T100_SEGMENT_ALL_CARRIER.csv` member. Two earlier concurrent requests returned HTML despite HTTP `200`; the successful manifest below came from sequential fresh sessions.
- Selected measures and labels: `DEPARTURES_SCHEDULED`, `DEPARTURES_PERFORMED`, `SEATS`, `PASSENGERS`, `DISTANCE`, `UNIQUE_CARRIER_NAME`.
- Declared raw key: `UNIQUE_CARRIER`, `AIRLINE_ID`, `UNIQUE_CARRIER_ENTITY`, `REGION`, `ORIGIN_AIRPORT_ID`, `ORIGIN`, `ORIGIN_STATE_ABR`, `ORIGIN_COUNTRY`, `DEST_AIRPORT_ID`, `DEST`, `DEST_STATE_ABR`, `DEST_COUNTRY`, `AIRCRAFT_TYPE`, `AIRCRAFT_CONFIG`, `YEAR`, `MONTH`, `CLASS`, `DATA_SOURCE`. Omitting `AIRCRAFT_CONFIG` left 13 conflicting Alaska-2024 groups; the complete key produced zero within-partition duplicate keys and zero union metric conflicts.
- State-filter semantics: the Alaska-2024 extract had 54,759 rows: 51,342 with Alaska origin, 51,190 with Alaska destination, 47,773 with both, and zero with neither. State extracts therefore cover either endpoint and overlap. Union rows are deduplicated on the complete raw key. Foreign endpoint state abbreviations may be blank while country remains present.
- Observed service classes in these 16 partitions were only `F`, `G`, `L`, and `P`: across all eight state extracts there were 103,153 / 15,769 / 30,612 / 5,796 rows respectively in 2023 and 112,030 / 15,746 / 28,445 / 6,280 in 2024. The current official BTS [FMG service-class lookup](https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_fReiVPR_PYNff) defines `A` as scheduled first-class passenger/cargo, `C` as scheduled coach passenger/cargo, `E` as scheduled mixed first/coach passenger/cargo, `F` as scheduled passenger/cargo, `G` as scheduled all cargo, `L` as nonscheduled civilian passenger/cargo, and `P` as nonscheduled civilian all cargo. The general scheduled passenger-capacity predicate is `CLASS in {'A','C','E','F'} AND SEATS > 0`; within these retained fixed-period partitions it selects only `F` rows because `A`, `C`, and `E` were not observed. Thus the prior predicate selected exactly the same rows and no metrics or coverage changed.
- Correct passenger population: origin-direction records, `CLASS = 'F'`, `SEATS > 0`, and all four observed source families `DU`, `DF`, `IU`, and `IF`. Cargo-only and nonscheduled classes remain in immutable raw ingestion but are excluded from this metric. At the 26 required origins, raw class counts were `F=29,122`, `G=3,893`, `L=4,098`, `P=2,113` in 2023 and `F=31,516`, `G=4,027`, `L=3,819`, `P=2,310` in 2024. Applying `F` plus positive seats yielded 29,039 and 31,428 eligible rows. Eligible `F` rows included every observed data-source family (2023: DF 40, DU 23,416, IF 3,321, IU 2,262; 2024: DF 49, DU 25,091, IF 3,645, IU 2,643).

### Assignment-scope partition manifest

The retained temporary archives are `/tmp/deloitte-t100-<state-name>-<year>-fullgrain.zip`; `/tmp/analyze_t100_partitions.py` reads `CLASS` and `DATA_SOURCE` by header name. The observed `DATA_SOURCE` values were exactly `DF`, `DU`, `IF`, and `IU` (2023 counts 1,751 / 134,204 / 11,434 / 7,941; 2024 counts 1,692 / 139,609 / 12,264 / 8,936).

| Year | State | ZIP bytes | ZIP SHA-256 | CSV rows | Raw-key duplicates | Scheduled passenger rows at required origins |
|---|---|---:|---|---:|---:|---:|
| 2023 | AK | 565,872 | `28006a1578d864c62cdf281ec2e1957741c54aff73674cf66e67e81faebce542` | 52,541 | 0 | 1,342 |
| 2023 | CA | 986,411 | `59137e86d53197a0da3ea9d76c3f51a027f27dc33c5c88fa6fbe6632f5506468` | 74,692 | 0 | 17,061 |
| 2023 | CT | 52,267 | `1998197ac494419053cef4bd09d998904bb7cb4a9c2b3fe7fb2b88c34e2f7a98` | 4,526 | 0 | 1,741 |
| 2023 | MA | 198,455 | `e9384213417e3df406e77bd6063e000f4d1f42afb65f4923be8bb5902464687b` | 15,894 | 0 | 6,171 |
| 2023 | ME | 32,160 | `9b6f3956d215534233bbe8f9deea7f50a14a89351014ba56cf9ac0c9949f3249` | 2,555 | 0 | 889 |
| 2023 | NH | 17,607 | `1645ea7a21bf4f71f76ac0f3b4be1162f25ec0287d846744d3f5d36e01edb653` | 1,485 | 0 | 422 |
| 2023 | RI | 30,557 | `3ed3828343cd0deb4570ca29c00f3a0f40cf79deef7b1470df964ccdd660889e` | 2,519 | 0 | 962 |
| 2023 | VT | 13,564 | `f21f88fbf7d7a8a7be229df8bb2b0e1f8c7522d255a0488678baadde425803d1` | 1,118 | 0 | 451 |
| 2024 | AK | 589,484 | `98b07e096577a7a23aa0e11a8a176db090b7de04094dc96456b55f7b805e1b92` | 54,759 | 0 | 1,446 |
| 2024 | CA | 1,021,860 | `38368bbfe9d811beabcc882ce66f4b0d6fe06ff582ac1b0eb48f2a41bc86baef` | 77,193 | 0 | 18,159 |
| 2024 | CT | 57,669 | `2f857138640dcfb0a74ff818ea4c0f9b5ec2d766ac1a656ab83a3e2997e0e558` | 5,064 | 0 | 2,019 |
| 2024 | MA | 212,165 | `0849df3c15c305fb03408b893f497a0e09e308e854aa14025d4b8fc604299242` | 17,116 | 0 | 6,821 |
| 2024 | ME | 36,243 | `ff1e7732241b541b858b653195b9aa030a49dd6e7fca9558cbb710df9a51006a` | 2,912 | 0 | 1,061 |
| 2024 | NH | 18,514 | `f8044a53d06ff10780233381dd15bd8d614bace46b94e69f3bbc49d7c41dcb7d` | 1,542 | 0 | 408 |
| 2024 | RI | 31,529 | `d37c0c2614d7cbd33ccabc4ba67a84310577c0fe8e6a599cc36d5c8e67ce1475` | 2,636 | 0 | 992 |
| 2024 | VT | 15,562 | `1d4781ebaf535faf28408aac65d5e296cd701daa9b04531004d71e1bae68e4c2` | 1,279 | 0 | 522 |

The 2023 union had 155,330 input rows, 153,307 unique full-key rows, 2,023 exact cross-state overlaps, and zero metric conflicts. The 2024 union had 162,501 input rows, 160,404 unique rows, 2,097 exact overlaps, and zero metric conflicts. All 26 required origin airports had eligible records in both years: `ANC`, `LAX`, `SFO`, `SNA`, and the 22-airport FAA New England cohort (`BDL`, `HVN`, `PWM`, `BGR`, `PQI`, `RKD`, `BHB`, `AUG`, `BOS`, `ACK`, `ORH`, `MVY`, `HYA`, `PVC`, `MHT`, `PSM`, `LEB`, `PVD`, `WST`, `BID`, `BTV`, `RUT`). Each covered months 1–12 except `PVC` in 2024, which had eligible records in months 1–11; the Massachusetts partition itself contained all 12 months, so December is a scoped no-row month rather than a missing partition.

### FAA reasonableness reconciliation

For the 22-airport New England cohort, scheduled T-100 `F` origin passengers totaled 28,560,209 in 2023 versus 28,569,243 FAA enplanements (difference -9,034, -0.0316%), and 30,571,687 in 2024 versus 30,426,788 FAA enplanements (difference +144,899, +0.4762%). The official FAA CY2024 commercial-service PDF inspected locally was 369,732 bytes with SHA-256 `458273de65b91eb0e7c26cc372616f812320b2dad163909edefd89c13cdff251`. Exact equality is not an acceptance rule because FAA ACAIS enplanements and the scheduled T-100 segment population have different reporting scope and revision timing; resolving individual differences requires an explicit comparison that also tests nonscheduled class `L` and records the applicable ACAIS and T-100 revision definitions.

**Verdict:** source qualification passes for the assignment's T-100 CY2023/24 population and all 26 required origin airports. Immutable ingestion, raw-to-metric disposition logging, and application calculation tests remain implementation gates. The documented `PVC` December-2024 no-row month must remain missing rather than being silently imputed as zero.

## BTS Reporting Carrier On-Time Performance, table `FGJ`

- Index URL: `GET https://transtats.bts.gov/PREZIP/`.
- Retrieval: `2026-09-25T16:24Z`, 163,596 bytes, SHA-256 `bd39cbf70abe16fc8d8cb851e4bab0f53756c4469b9fe14b27fad926cffef542`.
- Inventory: the official index lists one canonical `On_Time_Reporting_Carrier_On_Time_Performance_1987_present_YYYY_M.zip` for every month of 2023 and 2024 (24 canonical files). It also lists a parenthesized-name alias for December 2023.
- Inventory method: replace the index's `<br>` separators with newlines, select filenames matching `On_Time_Reporting_Carrier_On_Time_Performance.*_202(3|4)_[0-9]{1,2}.*\.zip`, sort unique names, then require exactly months 1–12 for each year after alias normalization. The downloaded index was `/tmp/deloitte-prezip-index.html`.
- December alias resolution: HEAD requests to both December 2023 URLs returned HTTP `200`, `application/x-zip-compressed`, 28,818,502 bytes, the same `Last-Modified` timestamp, and the same ETag. Treat them as aliases for one partition, never two inputs.
- Boundary check: the canonical December 2024 URL returned HTTP `200`, `application/x-zip-compressed`, 30,150,370 bytes, byte-range support, and `Last-Modified: 2025-03-14T16:19:19Z`.
- Bounded content check: the canonical January 2024 archive was reconstructed from four explicit byte ranges after an ordinary resume response produced an invalid oversized file. Each range returned HTTP `206`; concatenation produced the exact advertised 27,573,265 bytes, SHA-256 `fe089b45523f9d4ac0ccd0e176a543d274e914ee5ae5bd384dbaafc7dc06ebfd`, and passed `unzip -t`. The CSV member is 246,830,488 bytes with SHA-256 `e92febedf6cd51bd87eca0be2fba3f5c1f383b1da41119db7f1cc1a26c4d3286` and 547,271 data rows.
- The January 2024 CSV contains every required identity, schedule, delay, cancellation, diversion, taxi, airtime, flight-count, and distance field. Origin-scoped coverage was 15,228 LAX rows, 10,133 SFO rows, and 3,642 SNA rows; each airport covered all 31 flight dates. The declared scheduled-flight identity `(FlightDate, Reporting_Airline, Flight_Number_Reporting_Airline, OriginAirportID, DestAirportID, CRSDepTime)` had zero duplicate rows across the 29,003 scoped origin rows.
- Missing values are conditional rather than an ingestion failure: cancellation code was blank on 27,919 scoped rows; the five cause-delay fields were blank on 23,474; departure measures had 1,064–1,073 blanks; arrival/taxi/airtime measures had 1,114–1,162 blanks. Metrics must therefore use their documented eligible-flight and non-null denominators and return field coverage, never coerce these blanks to zero.

**Verdict:** the exact 24-file reporting-carrier inventory and December alias are resolved, and one canonical archive proves the required schema and LAX/SFO/SNA source grain. The other 23 archive bytes/checksums and month/airport coverage remain uncollected, so complete CY2023/24 operational coverage is still blocked. Ordinary `curl -C -` is unsafe against this source unless the returned `Content-Range` is validated; explicit ranges produced a correct archive. No operational comparison should ship from this single-month probe.
