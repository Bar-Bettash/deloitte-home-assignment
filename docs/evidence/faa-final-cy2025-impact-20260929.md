# FAA final CY2025 file: impact on the New England cohort (2026-09-29)

Read-only check, `www.faa.gov` only, run 2026-09-29T13:45Z from a local Mac. No bundle was built, promoted or changed.

## Why this check exists

`python -m scripts.check_source_status --bundle annual-2025-r1` exited 1 with `official source check blocked admission: faa`. It writes no receipt when blocked, so the FAA observation was reproduced directly.

## What the FAA site serves

| | Value |
|---|---|
| URL recorded in the qualification (requested) | `https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger/arp-cy2025-commercial-service-enplanements-preliminary.pdf` |
| Observed | HTTP 200 after **1 redirect** to `…/passenger/arp-cy2025-commercial-service-enplanements.pdf` (no `-preliminary`), `application/pdf`, 5,334,824 bytes |
| Recorded sha256 (preliminary, packaged snapshot `faa-834930a0…`, 1,708,683 bytes) | `e88218c048d512e3e702922768df8c82961d8b81faaa4e359c0360f20cc1f583` |
| Observed sha256 (both URLs return the same file) | `169efd0fadab1a80b78cbc048f1a61c9d8cbb371dde3efd2103bcef768a7d08f` |
| New file's title | `Final CY2025 Enplanements at All Airports by State and Airport`, dated September 24, 2026 |
| Index page CY2025 links | `arp-cy2025-commercial-service-enplanements.pdf/.xlsx`, `arp-cy2025-all-enplanements.pdf/.xlsx`, `ARP-CY2025-all-cargo-airports.pdf/.xlsx`. No preliminary link. |

The checker's FAA probe returned `proof_mode: selected_edition_ambiguous_or_superseded` because the index no longer lists the qualified URL (`scripts/check_source_status.py`, `_document_probe`).

Reproduce:

```sh
curl -sSL -o faa.pdf -w '%{http_code} %{url_effective} %{num_redirects}\n' \
  https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger/arp-cy2025-commercial-service-enplanements-preliminary.pdf
shasum -a 256 faa.pdf
```

## Does the New England cohort change?

The screen score uses only T-100 inputs (passengers, seats, occupancy = passengers / seats; `app/calculations/screen.py`, `app/calculations/traffic.py`). FAA supplies only the cohort: rows with region office `NE` in the commercial-service table (`app/sources/faa.py`, `parse_faa_preliminary_2025_cohort`). So the final file can change scores only if cohort membership changes.

Method: `pdftotext -layout` on both files, then rows matched on rank, region, state, LOCID, service level (`P`/`CS`), hub, CY25, CY24 and % change. The final file's layout differs (hub column renders as `e` for non-hub airports, and it covers **all** airports, not only commercial service), so the repository's strict preliminary parser rejects it (`missing required document markers`) and a layout-tolerant pattern was used for the comparison only.

| | Preliminary (packaged) | Final (live) |
|---|---|---|
| `NE` rows with service level `P` or `CS` | 23 | 23 (16 `P` + 7 `CS`) |
| Additional `NE` rows | none | 60 with service level `A` (not commercial service) |
| Airports only in one file | — | none |
| CY2025 enplanements changed for any of the 23 | — | none |
| Other changes | — | national rank +1 for AUG, PVC, RUT (rank is not used) |

Cohort, both files: ACK AUG BDL BGR BHB BID BOS BTV EWB HVN HYA LEB MHT MVY ORH PQI PSM PVC PVD PWM RKD RUT WST.

**Result: cohort membership is unchanged, so screen scores and ranks for `annual-2025-r1` would be unchanged by the final FAA file.**

Caveat for any rebuild: the final file lists every airport, so a cohort rule of `RO == NE` alone would select 83 airports. A rebuilt FAA snapshot must also filter service level to `P`/`CS`, and needs a parser for the final layout; the preliminary parser correctly refuses it.

## Decision left to the owner

1. Keep serving `annual-2025-r1` built on the frozen preliminary file (already accepted; serving is unaffected), and record that the final file matches its cohort; or
2. Build a new bundle from the final file (new parser, new manifest, new freshness and reconciliation receipts, then promotion).

This check does not choose between them.
