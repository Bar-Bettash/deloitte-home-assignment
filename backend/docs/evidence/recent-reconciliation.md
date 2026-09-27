# M3 recent arithmetic reconciliation

Date: 2026-09-27. Base revision: `08107ceeebd161c1d2613ef7e8bb09a082232ddc`.

Status: recent and historical deterministic reconciliation PASS. Milestone review and final full-suite result are recorded below when complete. The recent bundle remains a private candidate; this is not M4 source admission or API acceptance.

## Reproduction

```sh
PYTHONPATH=backend python backend/scripts/reconcile_recent.py --bundle annual-2025-r1 --raw-reference /private/tmp/deloitte-2025-independent/reference.json --output backend/docs/evidence/recent-arithmetic-reference.json
```

The raw reference is an external acquisition input. Its hash is bound in the committed output; this temporary input path is not a serving dependency. Rebuilding that raw reference requires the qualified original acquisition inputs. The durable output contains all expected values, source identities and historical controls. A missing or truncated raw reference fails the full CLI; expected-only mode is diagnostic.

## Scope and limits

Expected arithmetic uses independent SQL over the hash-verified packaged Parquet; it does not import application calculators. It is independent arithmetic, not independent ingestion. Recent results are additionally checked against the preserved raw CSV/ZIP reference for the matching source populations. The raw reference covers 23 regional airports, ANC long haul, three operational origins and 24 DataSF monthly totals. Packaged calculations cover 27 T100 origins and four operational origins; different population totals are not presented as equal.

The artifact retains T100 monthly traffic and operational monthly numerators/denominators. T100 and operations application APIs expose annual metrics, so their application comparison is annual with coverage checks; it does not claim a monthly application response. DataSF monthly totals are compared directly; operational monthly counts are checked against the staged source manifest. Historical parity uses historical packaged sources and independent arithmetic, without a new raw-source acquisition claim.

The comparison layer calls the real SFO pressure result and actual screen passenger/year fields. Mutation tests prove that altered SFO gap, screen values and operational arithmetic fail comparison. Tests also cover absent/truncated raw controls, CLI writing and calculator-import independence.

## Controls

| Workflow | Verified result |
|---|---|
| ANC CY2025, >=3000 miles | 999 / 36,040 performed departures; unknown distance 0; 2.771920088790233% |
| Regional screen | 23 selected airports; 22 assessable; EWB included; PVC excluded for partial annual coverage |
| Screen competition ranks | HVN 1; BGR 2; PWM 2; BOS 4 |
| SFO DataSF enplaned | 26,054,586 in 2024; 27,250,806 in 2025 |
| SFO T100 passengers | 25,288,609 → 26,477,602 |
| SFO T100 seats | 30,369,317 → 32,169,113 |
| LAX / SNA operational scheduled rows | 190,472 / 44,997 |
| Historical ANC CY2024 | 950 / 40,017 performed departures |

Recent and historical mismatches are empty in the artifact. Terminal need, profitability and unmet demand are not established by this arithmetic.

## Artifact and code identity

- Arithmetic artifact SHA256: `de52802e8fd674c7134508225d9a3440d853cb1d2310f55413988816fbaa2c9b`
- Candidate manifest SHA256: `f951dd50d254c3376c6b2f739fa79d711dbaa14e1fc6d21834d836012aad1b4d`
- Raw reference SHA256: `9c8d610c329abc471d78066996558bbdb8823e7c056dc13b365671f33a5f8efc`

| File | SHA256 |
|---|---|
| `backend/app/calculations/comparison.py` | `d102981a31c82b9e6d0a5841b5f94cb7f8487bf5123fd74c2f0acb10d8a2c541` |
| `backend/app/calculations/long_haul.py` | `b6e8df0415f799b7998191b98b6a5139f45fa672cb6342c321af4bca44d0c2e1` |
| `backend/app/calculations/operations.py` | `e6b4e6a325b02da031a06fbc84c6166b934c4ba659f38c5d4269d498f5337286` |
| `backend/app/calculations/screen.py` | `21e8764344334bd4414894e2d8fab25f01722b1bae050270fde56550453d620a` |
| `backend/app/calculations/sfo.py` | `fca3074e2c22032f73e2013d68dee12e8b636714e4544ac2dd465ed8cd09c074` |
| `backend/app/calculations/traffic.py` | `f64b8566bfc77e44e7bda3759033ac89e0f00bba879cc14a183850da08df3b10` |
| `backend/scripts/reconcile_recent.py` | `39df9c68f603b13fec3e4244892af55621d0651d0e6c05df78a0d1820f397461` |
| `backend/tests/test_reconcile_recent.py` | `ffb0145e4183266bf3289c6986692b6061b750f822458f9f11e00a045a2fd970` |

## Verification

- Reconciliation focused tests: 14 passed.
- Exact backend Ruff gate: passed.
- Full backend suite: 431 passed in 89.20 seconds; one upstream Starlette/AnyIO deprecation warning.
- Independent lead-architect and data-engineer reviews: APPROVED, zero unresolved M3 findings. Both reproduced reconciliation checks; architect ran 111 calculator tests plus 14 reconciliation tests.
- Reviewed calculator diff SHA256: `04f360ea44c23de75081af1c4494eb4e156d38ffd9f90570bda88940d2a87ff0`.
- JSON parsing and git diff --check: passed.
