"""Independent arithmetic reference and application reconciliation for a data bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import duckdb
from app.sources.bundle import BundleContext, SnapshotRef, load_bundle

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
DEFAULT_THRESHOLD = 3_000.0


class ReconciliationError(RuntimeError):
    """Independent controls or application results do not reconcile."""


def _sha256(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise ReconciliationError(f"required artifact is unavailable: {path}") from exc


def _query(path: Path, sql: str, parameters: Sequence[object] = ()) -> list[tuple]:
    try:
        with duckdb.connect() as connection:
            return connection.execute(sql, [str(path), *parameters]).fetchall()
    except duckdb.Error as exc:
        raise ReconciliationError(f"independent query failed for {path.name}") from exc


def _source_identity(bundle: BundleContext) -> dict[str, object]:
    return {
        name: {
            "snapshot_id": ref.snapshot_id,
            "manifest_sha256": ref.manifest_sha256,
            "content_sha256": ref.content_sha256,
            "years": list(ref.years),
        }
        for name, ref in sorted(bundle.sources.items())
    }


def load_historical_context(data_root: Path = DEFAULT_DATA_ROOT) -> BundleContext:
    """Reconstruct the immutable 2023/24 context selected by legacy pointers."""
    refs = {
        name: _legacy_ref(data_root / "raw" / name, name, years)
        for name, years in {
            "datasf": (2023, 2024), "t100": (2023, 2024), "ontime": (2024,)
        }.items()
    }
    faa_root = data_root / "raw" / "faa"
    pointer = _json_file(faa_root / "current.json")
    faa_manifest_path = faa_root / pointer["manifest"]
    faa_manifest = _json_file(faa_manifest_path)
    cohort_rows = faa_manifest.get("cohort")
    cohort = (
        [item.get("locid") for item in cohort_rows]
        if isinstance(cohort_rows, list) and all(isinstance(item, dict) for item in cohort_rows)
        else None
    )
    if not isinstance(cohort, list) or not all(isinstance(item, str) for item in cohort):
        raise ReconciliationError("historical FAA cohort is invalid")
    identity = json.dumps(
        {name: ref.manifest_sha256 for name, ref in sorted(refs.items())},
        sort_keys=True,
    ).encode()
    return BundleContext(
        bundle_id="historical-2024-reconciliation",
        manifest_sha256=hashlib.sha256(identity).hexdigest(),
        baseline_year=2023,
        comparison_year=2024,
        cohort=tuple(cohort),
        sources=refs,
        evidence_path=data_root / "evidence.json",
        evidence_sha256="0" * 64,
    )


def _legacy_ref(root: Path, source: str, years: tuple[int, ...]) -> SnapshotRef:
    pointer = _json_file(root / "current.json")
    snapshot_id, relative = pointer.get("snapshot_id"), pointer.get("manifest")
    if not isinstance(snapshot_id, str) or relative != f"snapshots/{snapshot_id}/manifest.json":
        raise ReconciliationError(f"historical {source} pointer is invalid")
    manifest_path = root / relative
    manifest = _json_file(manifest_path)
    filename = manifest.get("parquet_file")
    hash_key = "content_sha256" if source == "datasf" else "parquet_sha256"
    content_hash = manifest.get(hash_key)
    data_path = manifest_path.parent / filename if isinstance(filename, str) else Path()
    if (
        manifest.get("snapshot_id") != snapshot_id
        or not isinstance(content_hash, str)
        or _sha256(data_path) != content_hash
    ):
        raise ReconciliationError(f"historical {source} snapshot identity is invalid")
    return SnapshotRef(
        source=source, snapshot_id=snapshot_id, manifest_sha256=_sha256(manifest_path),
        content_sha256=content_hash, manifest_path=manifest_path, data_path=data_path,
        years=years, vintage_id=f"historical-{years[-1]}", publication_status="accepted",
    )


def _json_file(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReconciliationError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ReconciliationError(f"JSON artifact must be an object: {path}")
    return value


def _traffic_expected(bundle: BundleContext) -> dict[str, object]:
    ref = bundle.sources["t100"]
    rows = _query(
        ref.data_path,
        "SELECT origin, year, month, sum(passengers), sum(seats), "
        "sum(departures_performed) FROM read_parquet(?) "
        "WHERE year IN (?, ?) GROUP BY ALL ORDER BY origin, year, month",
        (bundle.baseline_year, bundle.comparison_year),
    )
    monthly: dict[str, dict[str, dict[str, object]]] = {}
    for origin, year, month, passengers, seats, departures in rows:
        monthly.setdefault(origin, {}).setdefault(str(year), {})[str(month)] = {
            "passengers": int(passengers),
            "seats": int(seats),
            "performed_departures": int(departures),
        }
    airports: dict[str, object] = {}
    for origin, years in monthly.items():
        annual: dict[str, object] = {}
        for year in (bundle.baseline_year, bundle.comparison_year):
            cells = years.get(str(year), {})
            totals = {
                key: sum(int(cell[key]) for cell in cells.values())
                for key in ("passengers", "seats", "performed_departures")
            }
            annual[str(year)] = {
                "months": sorted(map(int, cells)),
                **totals,
                "occupancy_percent": (
                    100.0 * totals["passengers"] / totals["seats"]
                    if totals["seats"] else None
                ),
            }
        baseline = annual[str(bundle.baseline_year)]["passengers"]
        comparison = annual[str(bundle.comparison_year)]["passengers"]
        airports[origin] = {
            "annual": annual, "monthly": years,
            "growth_percent": 100.0 * (comparison - baseline) / baseline if baseline else None,
        }
    return {"airports": airports, "monthly_grain": "origin/year/month"}


def _long_haul_expected(bundle: BundleContext, threshold: float) -> dict[str, object]:
    year = bundle.comparison_year
    rows = _query(
        bundle.sources["t100"].data_path,
        "SELECT month, departures_performed, distance FROM read_parquet(?) "
        "WHERE origin = 'ANC' AND year = ? ORDER BY month",
        (year,),
    )
    months = sorted({int(month) for month, _departures, _distance in rows})
    total = sum(int(departures) for _month, departures, _distance in rows)
    missing = sum(
        int(departures) for _month, departures, distance in rows
        if distance is None and int(departures) > 0
    )
    known = sum(
        int(departures) for _month, departures, distance in rows
        if distance is not None and float(distance) >= threshold
    )
    return {
        "airport": "ANC", "year": year, "threshold_miles": threshold, "months": months,
        "total_performed_departures": total,
        "known_long_haul_performed_departures": known,
        "missing_distance_performed_departures": missing,
        "lower_bound_percent": 100.0 * known / total if total else None,
        "upper_bound_percent": 100.0 * (known + missing) / total if total else None,
    }


def _operations_expected(bundle: BundleContext) -> dict[str, object]:
    ref = bundle.sources["ontime"]
    year = bundle.comparison_year
    rows = _query(
        ref.data_path,
        "SELECT origin, month, count(*) FILTER (WHERE cancelled IN (0,1) AND diverted IN (0,1) "
        "AND flights=1), sum(cancelled) FILTER (WHERE cancelled IN (0,1) AND diverted IN (0,1) "
        "AND flights=1), sum(diverted) FILTER (WHERE cancelled IN (0,1) AND diverted IN (0,1) "
        "AND flights=1), sum(dep_delay_minutes) FILTER (WHERE cancelled=0 AND diverted=0), "
        "count(dep_delay_minutes) FILTER (WHERE cancelled=0 AND diverted=0), "
        "sum(taxi_out) FILTER (WHERE cancelled=0 AND diverted=0), "
        "count(taxi_out) FILTER (WHERE cancelled=0 AND diverted=0), "
        "count(DISTINCT reporting_airline) FILTER (WHERE cancelled IN (0,1) AND diverted IN (0,1) "
        "AND flights=1) FROM read_parquet(?) WHERE year=? GROUP BY origin, month ORDER BY origin, month",
        (year,),
    )
    monthly: dict[str, dict[str, dict[str, object]]] = {}
    for row in rows:
        origin, month, valid, cancelled, diverted, delay_sum, delay_n, taxi_sum, taxi_n, carriers = row
        monthly.setdefault(origin, {})[str(month)] = {
            "scheduled_count": int(valid), "cancelled": int(cancelled), "diverted": int(diverted),
            "delay_sum": float(delay_sum or 0), "delay_denominator": int(delay_n),
            "taxi_sum": float(taxi_sum or 0), "taxi_denominator": int(taxi_n),
            "carrier_count": int(carriers),
        }
    airports: dict[str, object] = {}
    for origin, cells in monthly.items():
        total = sum(cell["scheduled_count"] for cell in cells.values())
        cancelled = sum(cell["cancelled"] for cell in cells.values())
        diverted = sum(cell["diverted"] for cell in cells.values())
        delay_sum = sum(cell["delay_sum"] for cell in cells.values())
        delay_n = sum(cell["delay_denominator"] for cell in cells.values())
        taxi_sum = sum(cell["taxi_sum"] for cell in cells.values())
        taxi_n = sum(cell["taxi_denominator"] for cell in cells.values())
        airports[origin] = {
            "months": sorted(map(int, cells)), "scheduled_count": total,
            "cancelled": cancelled, "diverted": diverted,
            "cancellation_percent": 100.0 * cancelled / total,
            "diversion_percent": 100.0 * diverted / total,
            "departure_delay_minutes": {"sum": delay_sum, "denominator": delay_n,
                                         "average": delay_sum / delay_n},
            "taxi_out_minutes": {"sum": taxi_sum, "denominator": taxi_n,
                                  "average": taxi_sum / taxi_n},
            "monthly": cells,
        }
    manifest = _json_file(ref.manifest_path)
    manifest_coverage = manifest.get("coverage")
    derived_coverage = {
        f"{origin}-{int(month):02d}": cell["scheduled_count"]
        for origin, cells in monthly.items() for month, cell in cells.items()
    }
    if manifest_coverage != derived_coverage:
        raise ReconciliationError("on-time monthly controls do not match the staged manifest")
    return {"year": year, "airports": airports, "monthly_grain": "origin/month"}


def _datasf_expected(bundle: BundleContext) -> dict[str, object]:
    ref = bundle.sources["datasf"]
    rows = _query(
        ref.data_path,
        "SELECT activity_period, geo_summary, sum(passenger_count), count(*) "
        "FROM read_parquet(?) WHERE activity_type_code='Enplaned' "
        "GROUP BY ALL ORDER BY activity_period, geo_summary",
    )
    cells = {
        f"{period}/{geo}": {"passengers": int(passengers), "source_rows": int(count)}
        for period, geo, passengers, count in rows
    }
    monthly = {
        period: sum(cell["passengers"] for key, cell in cells.items() if key.startswith(f"{period}/"))
        for period in sorted({key.split("/", 1)[0] for key in cells})
    }
    annual = {
        str(year): sum(value for period, value in monthly.items() if period.startswith(str(year)))
        for year in (bundle.baseline_year, bundle.comparison_year)
    }
    baseline, comparison = annual[str(bundle.baseline_year)], annual[str(bundle.comparison_year)]
    return {
        "cells": cells, "monthly_enplaned": monthly, "annual_enplaned": annual,
        "growth_percent": 100.0 * (comparison - baseline) / baseline,
    }


def _average_ranks(values: Mapping[str, float]) -> dict[str, float]:
    ordered = sorted(set(values.values()))
    return {
        airport: 1.0 + sum(other < value for other in values.values())
        + (sum(other == value for other in values.values()) - 1) / 2.0
        for airport, value in values.items()
        if value in ordered
    }


def _screen_expected(bundle: BundleContext, traffic: Mapping[str, object]) -> dict[str, object]:
    candidates: dict[str, dict[str, float | int]] = {}
    exclusions: dict[str, str] = {}
    baseline, comparison = str(bundle.baseline_year), str(bundle.comparison_year)
    airports = traffic["airports"]
    for airport in bundle.cohort:
        item = airports.get(airport)
        if item is None:
            exclusions[airport] = "no eligible T-100 rows"
            continue
        first, second = item["annual"][baseline], item["annual"][comparison]
        missing = [year for year, annual in ((baseline, first), (comparison, second))
                   if annual["months"] != list(range(1, 13))]
        if missing:
            exclusions[airport] = f"incomplete eligible coverage: {','.join(missing)}"
            continue
        if first["passengers"] <= 0 or second["seats"] <= 0:
            exclusions[airport] = "nonpositive growth or occupancy denominator"
            continue
        candidates[airport] = {
            "passengers": second["passengers"],
            "growth_percent": 100.0 * (second["passengers"] - first["passengers"])
            / first["passengers"],
            "occupancy_percent": 100.0 * second["passengers"] / second["seats"],
        }
    if len(candidates) < 2:
        return {"status": "insufficient_data", "rows": [], "exclusions": exclusions}
    n = len(candidates)
    ranks = {
        key: _average_ranks({airport: float(row[key]) for airport, row in candidates.items()})
        for key in ("growth_percent", "passengers", "occupancy_percent")
    }
    scores = {
        airport: 100.0 * (
            0.40 * (ranks["growth_percent"][airport] - 1) / (n - 1)
            + 0.30 * (ranks["passengers"][airport] - 1) / (n - 1)
            + 0.30 * (ranks["occupancy_percent"][airport] - 1) / (n - 1)
        )
        for airport in candidates
    }
    rows = [
        {
            "airport": airport, **candidates[airport], "screen_score": scores[airport],
            "rank": 1 + sum(other > scores[airport] for other in scores.values()),
            "passenger_year": bundle.comparison_year,
        }
        for airport in sorted(candidates, key=lambda value: (-scores[value], value))
    ]
    return {
        "status": "ok", "comparison_year": bundle.comparison_year,
        "reference_cohort": sorted(candidates), "rows": rows, "exclusions": exclusions,
    }


def build_expected(
    bundle: BundleContext, *, raw_reference_path: Path | None = None,
    threshold_miles: float = DEFAULT_THRESHOLD,
) -> dict[str, object]:
    """Build controls directly from hash-verified Parquet, without app calculators."""
    if not math.isfinite(threshold_miles) or threshold_miles < 0:
        raise ReconciliationError("long-haul threshold must be finite and nonnegative")
    traffic = _traffic_expected(bundle)
    operations = _operations_expected(bundle)
    datasf = _datasf_expected(bundle)
    expected = {
        "schema_version": 1,
        "identity": {
            "bundle_id": bundle.bundle_id,
            "bundle_manifest_sha256": bundle.manifest_sha256,
            "generator_sha256": _sha256(Path(__file__)),
            "sources": _source_identity(bundle),
            "raw_reference_sha256": _sha256(raw_reference_path) if raw_reference_path else None,
        },
        "independence": {
            "expected_values_import_application_calculators": False,
            "method": "independent DuckDB SQL over immutable hash-verified bundle Parquet",
            "limit": "independent arithmetic over staged data, not independent ingestion",
            "raw_source_reference_checked": raw_reference_path is not None,
            "monthly_validation": {
                "datasf": "preserved raw reference when provided; otherwise bundle Parquet only",
                "t100": "retained source-to-reference controls; app exposes annual totals only",
                "ontime": "exact source-to-reference counts match staged manifest; app exposes annual totals only",
            },
        },
        "period": {"baseline_year": bundle.baseline_year,
                   "comparison_year": bundle.comparison_year},
        "traffic": traffic,
        "long_haul": _long_haul_expected(bundle, threshold_miles),
        "screen": _screen_expected(bundle, traffic),
        "operations": operations,
        "sfo": {
            "datasf": datasf,
            "t100": traffic["airports"]["SFO"],
            "passenger_minus_seat_growth_gap_percentage_points": _sfo_gap(
                traffic["airports"]["SFO"], bundle
            ),
        },
        "comparison": _comparison_expected(operations, "LAX", "SNA"),
    }
    if raw_reference_path:
        _check_preserved_raw_reference(expected, raw_reference_path)
    return expected


def _sfo_gap(sfo: Mapping[str, object], bundle: BundleContext) -> float:
    first = sfo["annual"][str(bundle.baseline_year)]
    second = sfo["annual"][str(bundle.comparison_year)]
    passenger_growth = (second["passengers"] - first["passengers"]) / first["passengers"]
    seat_growth = (second["seats"] - first["seats"]) / first["seats"]
    return 100.0 * (passenger_growth - seat_growth)


def _comparison_expected(operations: Mapping[str, object], a: str, b: str) -> dict[str, object]:
    first, second = operations["airports"][a], operations["airports"][b]
    values = {
        "cancellation_rate": {a: first["cancellation_percent"], b: second["cancellation_percent"]},
        "diversion_rate": {a: first["diversion_percent"], b: second["diversion_percent"]},
    }
    for key in ("departure_delay_minutes", "taxi_out_minutes"):
        values[key] = {a: first[key]["average"], b: second[key]["average"]}
    directions = {
        key: "higher" if pair[a] > pair[b] else "lower" if pair[a] < pair[b] else "tied"
        for key, pair in values.items()
    }
    return {"airports": [a, b], "values": values, "directions": directions}


def _check_preserved_raw_reference(expected: Mapping[str, object], path: Path) -> None:
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ReconciliationError("preserved raw reference is invalid") from exc
    if not isinstance(raw, dict):
        raise ReconciliationError("preserved raw reference must be an object")
    mismatches: list[str] = []
    datasf = raw.get("datasf", {})
    raw_monthly = datasf.get("monthly_enplaned", {}) if isinstance(datasf, dict) else {}
    expected_monthly = expected["sfo"]["datasf"]["monthly_enplaned"]
    if set(raw_monthly) != set(expected_monthly):
        mismatches.append("raw DataSF exact 24-month set")
    for period, total in expected_monthly.items():
        item = raw_monthly.get(period, {})
        if item.get("total_enplaned_passengers") != total:
            mismatches.append(f"raw DataSF {period} total")

    raw_t100 = raw.get("t100", {})
    raw_airports = raw_t100.get("new_england", {}).get("airports", {})
    expected_airports = expected["traffic"]["airports"]
    expected_cohort = set(expected["screen"]["reference_cohort"]) | set(
        expected["screen"]["exclusions"]
    )
    if set(raw_airports) != expected_cohort:
        mismatches.append("raw T-100 exact New England cohort")
    for airport, raw_airport in raw_airports.items():
        if airport not in expected_airports:
            continue
        for year in ("2024", "2025"):
            expected_year = expected_airports[airport]["annual"][year]
            raw_year = raw_airport[year]
            for key in ("passengers", "seats"):
                if raw_year.get(key) != expected_year[key]:
                    mismatches.append(f"raw T-100 {airport}/{year}/{key}")

    raw_anc = raw_t100.get("anc_long_haul", {}).get("2025", {}).get("3000", {})
    long_haul = expected["long_haul"]
    if int(float(raw_anc.get("known_long_haul_performed_departures", -1))) != long_haul[
        "known_long_haul_performed_departures"
    ]:
        mismatches.append("raw ANC known long-haul departures")
    if int(float(raw_anc.get("total_performed_departures", -1))) != long_haul[
        "total_performed_departures"
    ]:
        mismatches.append("raw ANC total performed departures")

    raw_operations = raw.get("ontime", {}).get("airports", {})
    expected_operations = expected["operations"]["airports"]
    if set(raw_operations) != {"LAX", "SFO", "SNA"}:
        mismatches.append("raw on-time exact three-airport scope")
    for airport, item in raw_operations.items():
        if airport not in expected_operations:
            continue
        actual = expected_operations[airport]
        checks = {
            "all_valid_rows_denominator": actual["scheduled_count"],
            "cancelled_rows": actual["cancelled"],
            "diverted_rows": actual["diverted"],
        }
        for key, value in checks.items():
            if item.get(key) != value:
                mismatches.append(f"raw on-time {airport}/{key}")
        for raw_key, expected_key in (
            ("dep_delay_minutes", "departure_delay_minutes"),
            ("taxi_out_minutes", "taxi_out_minutes"),
        ):
            if item.get(raw_key, {}).get("non_null_denominator") != actual[expected_key][
                "denominator"
            ]:
                mismatches.append(f"raw on-time {airport}/{raw_key} denominator")

    ranks = {row["airport"]: row["rank"] for row in expected["screen"]["rows"]}
    if ranks.get("BGR") != 2 or ranks.get("PWM") != 2:
        mismatches.append("BGR/PWM competition rank 2/2")
    if mismatches:
        raise ReconciliationError("preserved raw controls disagree: " + ", ".join(mismatches))


def compare_values(expected: object, actual: object, *, tolerance: float = 1e-8) -> list[str]:
    """Return stable mismatch paths; numeric values use a strict absolute tolerance."""
    mismatches: list[str] = []

    def walk(left: object, right: object, path: str) -> None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            if set(left) != set(right):
                mismatches.append(f"{path}: keys {sorted(left)} != {sorted(right)}")
                return
            for key in sorted(left):
                walk(left[key], right[key], f"{path}.{key}")
        elif isinstance(left, list) and isinstance(right, (list, tuple)):
            if len(left) != len(right):
                mismatches.append(f"{path}: length {len(left)} != {len(right)}")
                return
            for index, (first, second) in enumerate(zip(left, right)):
                walk(first, second, f"{path}[{index}]")
        elif isinstance(left, (int, float)) and not isinstance(left, bool) \
                and isinstance(right, (int, float)) and not isinstance(right, bool):
            if not math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance):
                mismatches.append(f"{path}: {left!r} != {right!r}")
        elif left != right:
            mismatches.append(f"{path}: {left!r} != {right!r}")

    walk(expected, actual, "$")
    return mismatches


def _expected_application_controls(expected: Mapping[str, object]) -> dict[str, object]:
    traffic = {}
    for airport, item in expected["traffic"]["airports"].items():
        annual = {}
        for year, values in item["annual"].items():
            annual[year] = dict(values)
            if values["months"] != list(range(1, 13)):
                annual[year].update(
                    passengers=None, seats=None, performed_departures=None, occupancy_percent=None
                )
        complete = all(values["months"] == list(range(1, 13)) for values in item["annual"].values())
        traffic[airport] = {
            "annual": annual, "growth_percent": item["growth_percent"] if complete else None,
        }
    operation_airports = {
        airport: {key: value for key, value in item.items() if key != "monthly"}
        for airport, item in expected["operations"]["airports"].items()
    }
    sfo_datasf = expected["sfo"]["datasf"]
    return {
        "traffic": traffic,
        "long_haul": expected["long_haul"],
        "screen": {
            **{key: expected["screen"][key]
               for key in ("status", "comparison_year", "reference_cohort", "rows")},
            "excluded_airports": sorted(expected["screen"]["exclusions"]),
        },
        "operations": {"year": expected["operations"]["year"], "airports": operation_airports},
        "sfo": {
            "datasf": {key: value for key, value in sfo_datasf.items() if key != "cells"},
            "t100": traffic["SFO"],
            "passenger_minus_seat_growth_gap_percentage_points": expected["sfo"]
            ["passenger_minus_seat_growth_gap_percentage_points"],
        },
        "comparison": expected["comparison"],
    }


def collect_application_controls(
    bundle: BundleContext, expected: Mapping[str, object], *, bundled: bool = True
) -> dict[str, object]:
    """Run app calculators separately from expected-value generation."""
    from app.calculations.comparison import compare_operations
    from app.calculations.long_haul import calculate_long_haul_share
    from app.calculations.operations import calculate_operations
    from app.calculations.screen import calculate_screen
    from app.calculations.sfo import (
        calculate_sfo_enplaned_trend,
        calculate_sfo_pressure,
    )
    from app.calculations.traffic import calculate_traffic_batch

    airport_ids = sorted(expected["traffic"]["airports"])
    bundle_kw = {"bundle": bundle} if bundled else {}
    traffic_results = calculate_traffic_batch(airport_ids, **bundle_kw)
    traffic: dict[str, object] = {}
    for airport, result in traffic_results.items():
        annual = {
            str(item.year): {
                "months": list(item.coverage.observed_months),
                "passengers": item.passengers.value,
                "seats": item.seats.value,
                "performed_departures": item.performed_departures.value,
                "occupancy_percent": item.occupancy_percent.value,
            }
            for item in result.annual
        }
        traffic[airport] = {"annual": annual, "growth_percent": result.growth.percent}

    long_result = calculate_long_haul_share(
        "ANC", bundle.comparison_year, DEFAULT_THRESHOLD, **bundle_kw
    )
    long_haul = {
        "airport": long_result.airport, "year": long_result.year,
        "threshold_miles": long_result.threshold_miles,
        "months": list(long_result.coverage.observed_months),
        "total_performed_departures": long_result.total_departures,
        "known_long_haul_performed_departures": long_result.long_haul_departures,
        "missing_distance_performed_departures": long_result.unknown_distance_departures,
        "lower_bound_percent": long_result.lower_percent,
        "upper_bound_percent": long_result.upper_percent,
    }

    screen_result = calculate_screen(
        (traffic_results[airport] for airport in bundle.cohort), **bundle_kw
    )
    screen = {
        "status": screen_result.status, "comparison_year": bundle.comparison_year,
        "reference_cohort": list(screen_result.reference_cohort),
        "rows": [
            {"airport": row.airport, "passengers": row.passengers,
             "growth_percent": row.passenger_growth_percent,
             "occupancy_percent": row.seat_occupancy_percent,
             "screen_score": row.screen_score, "rank": row.rank,
             "passenger_year": row.passenger_year}
            for row in screen_result.rows
        ],
        "excluded_airports": sorted(item.airport for item in screen_result.exclusions),
    }

    operations_results = {
        airport: calculate_operations(airport, year=bundle.comparison_year, **bundle_kw)
        for airport in expected["operations"]["airports"]
    }
    operations = {
        "year": bundle.comparison_year,
        "airports": {airport: _operations_app_control(result) for airport, result
                     in operations_results.items()},
    }
    trend = calculate_sfo_enplaned_trend(**bundle_kw)
    pressure = calculate_sfo_pressure(**bundle_kw)
    comparison = compare_operations(operations_results["LAX"], operations_results["SNA"])
    return {
        "traffic": traffic, "long_haul": long_haul, "screen": screen,
        "operations": operations,
        "sfo": _sfo_app_control(trend, pressure, traffic["SFO"]),
        "comparison": {
            "airports": ["LAX", "SNA"],
            "values": {
                item.key: {"LAX": item.a_value, "SNA": item.b_value}
                for item in comparison.indicators
            },
            "directions": {item.key: item.direction for item in comparison.indicators},
        },
    }


def _operations_app_control(result: object) -> dict[str, object]:
    return {
        "months": list(result.observed_months),
        "scheduled_count": result.scheduled_count,
        "cancelled": result.cancellation_rate.numerator,
        "diverted": result.diversion_rate.numerator,
        "cancellation_percent": result.cancellation_rate.value,
        "diversion_percent": result.diversion_rate.value,
        "departure_delay_minutes": {
            "sum": result.departure_delay_minutes.numerator,
            "denominator": result.departure_delay_minutes.denominator,
            "average": result.departure_delay_minutes.value,
        },
        "taxi_out_minutes": {
            "sum": result.taxi_out_minutes.numerator,
            "denominator": result.taxi_out_minutes.denominator,
            "average": result.taxi_out_minutes.value,
        },
    }


def _sfo_app_control(
    trend: object, pressure: object, traffic: Mapping[str, object]
) -> dict[str, object]:
    monthly = {point.period: point.passengers for point in trend.series}
    annual = {str(item.year): item.passengers for item in trend.annual_totals}
    return {
        "datasf": {
            "monthly_enplaned": monthly, "annual_enplaned": annual,
            "growth_percent": trend.growth.percent,
        },
        "t100": traffic,
        "passenger_minus_seat_growth_gap_percentage_points": pressure.growth_gap_pp.value,
    }


def reconcile(
    bundle: BundleContext, expected: Mapping[str, object], *, bundled: bool = True
) -> dict[str, object]:
    actual = collect_application_controls(bundle, expected, bundled=bundled)
    mismatches = compare_values(_expected_application_controls(expected), actual)
    return {
        "status": "pass" if not mismatches else "fail",
        "bundle_id": bundle.bundle_id,
        "bundle_manifest_sha256": bundle.manifest_sha256,
        "mismatches": mismatches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile recent bundle arithmetic")
    parser.add_argument("--bundle", "--bundle-id", dest="bundle_id", default="annual-2025-r1")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--raw-reference", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-only", action="store_true")
    parser.add_argument("--skip-historical-parity", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.expected_only and args.raw_reference is None:
            raise ReconciliationError("full reconciliation requires --raw-reference")
        bundle = load_bundle(args.bundle_id, data_root=args.data_root)
        expected = build_expected(bundle, raw_reference_path=args.raw_reference)
        if args.expected_only:
            result: Mapping[str, object] = expected
            status = "pass"
        else:
            recent = reconcile(bundle, expected)
            if args.skip_historical_parity:
                result = {**expected, "reconciliation": recent}
                status = recent["status"]
            else:
                historical_context = load_historical_context(args.data_root)
                historical_expected = build_expected(historical_context)
                historical = reconcile(
                    historical_context, historical_expected, bundled=False
                )
                status = "pass" if recent["status"] == historical["status"] == "pass" else "fail"
                result = {
                    **expected,
                    "reconciliation": {
                        "status": status, "recent": recent, "historical": historical,
                        "historical_controls": historical_expected,
                    },
                }
    except (ReconciliationError, KeyError, TypeError, ValueError) as exc:
        parser.exit(1, f"recent reconciliation failed: {exc}\n")
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
