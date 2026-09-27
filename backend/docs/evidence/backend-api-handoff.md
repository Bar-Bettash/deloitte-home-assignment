# Backend API handoff

## Release state

`annual-2025-r1` is accepted locally for **CY2024 → CY2025** analysis. The acceptance coordinator validated the live source-admission and final reconciliation receipts and registered the exact manifest in `backend/data/bundles/accepted.json`. Omitted-year requests now default to 2025 in this backend worktree; this is not a deployed release.

Live source verification passed on 2026-09-27: all 16 selected T-100 exports, all 12 on-time archives, selected DataSF records, and FAA/AIP editions matched. No newer complete period was found. Partial 2026 data is available for DataSF, T-100, on-time performance and AIP; FAA has no newer partial period in this check. Those partial periods are disclosed, not included in the annual calculations. See [source admission evidence](recent-source-admission.json).

Data verification and deterministic structured requests come first. Free-text model interpretation is the last integration step. No API spend-limit feature is part of this prototype handoff.

## Structured API requests

Send `POST /api/query` with `Content-Type: application/json`. These examples explicitly select the accepted annual bundle.

### 1. New England investment screen

```json
{
  "analysis": {
    "action": "rank",
    "region": "new_england",
    "metric": "screen_score",
    "year": 2025,
    "bundle_id": "annual-2025-r1"
  }
}
```

The frozen cohort includes **EWB**. **PVC is excluded from scoring** because it lacks complete eligible T-100 coverage for both 2024 and 2025. A screen score is a traffic-pressure heuristic, not proof of terminal congestion or investment success.

### 2. LAX versus SNA operations

```json
{
  "analysis": {
    "action": "compare",
    "airports": ["LAX", "SNA"],
    "metric": "congestion",
    "year": 2025,
    "bundle_id": "annual-2025-r1"
  }
}
```

This compares the qualified origin-departure population using cancellation, diversion, departure-delay-minutes, and taxi-out indicators with their own denominators.

### 3. ANC long-haul share

```json
{
  "analysis": {
    "action": "metric",
    "airports": ["ANC"],
    "metric": "long_haul_share",
    "year": 2025,
    "threshold_miles": 3000,
    "bundle_id": "annual-2025-r1"
  }
}
```

The candidate control is 999 known qualifying departures out of 36,040 performed departures. No performed departures have missing distance, so the lower and upper bounds are both **2.7719200888%**. The API still returns typed lower and upper bounds so uncertainty remains explicit when missing-distance departures exist.

### 4. SFO pressure summary

```json
{
  "analysis": {
    "action": "metric",
    "airports": ["SFO"],
    "metric": "sfo_pressure",
    "year": 2025,
    "bundle_id": "annual-2025-r1"
  }
}
```

This combines qualified DataSF enplanements, T-100 transported traffic and seats, operational indicators, and reviewed evidence. It does not quantify unmet demand.

## Period and business interpretation rules

- Once accepted, an omitted year and bundle resolve to the accepted 2025 comparison year. Acceptance is the only action that changes the default.
- Explicit `year: 2023` or `year: 2024` requests without a bundle retain the historical path. The recent screen and other comparison-year-only workflows require the recent bundle comparison year.
- Reviewed terminal status for HVN, BGR, PWM, and SFO remains **unknown**. Project planning or AIP funding context is not evidence of a current terminal deficit.
- Profitability and precise SFO unmet demand are **not identifiable** from the qualified public data. Responses must preserve those limitations instead of presenting inferred business quantities.
