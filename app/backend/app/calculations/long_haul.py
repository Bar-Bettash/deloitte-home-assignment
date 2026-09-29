"""Performed-departure weighted long-haul share from accepted T-100 data."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import duckdb
from app.calculations.traffic import (
    DEFAULT_DATA_ROOT,
    AnnualCoverage,
    T100SnapshotSource,
    TrafficCalculationError,
    _nonnegative_integer,
    _validated_airport,
    load_t100_snapshot,
)
from app.sources.bundle import BundleContext


@dataclass(frozen=True, slots=True)
class LongHaulResult:
    airport: str
    year: int
    threshold_miles: float
    source: T100SnapshotSource
    coverage: AnnualCoverage
    long_haul_departures: int | None
    total_departures: int | None
    share_percent: float | None
    status: Literal["ok", "insufficient_data", "unavailable"]
    reason: str | None
    unknown_distance_departures: int | None = None
    lower_percent: float | None = None
    upper_percent: float | None = None


def calculate_long_haul_share(
    airport: str,
    year: int,
    threshold_miles: float,
    data_root: Path = DEFAULT_DATA_ROOT,
    *,
    bundle: BundleContext | None = None,
) -> LongHaulResult:
    """Return an exact or bounded performed-departure long-haul share."""
    supported_years = (
        (bundle.baseline_year, bundle.comparison_year) if bundle is not None else (2023, 2024)
    )
    if year not in supported_years:
        raise TrafficCalculationError(
            f"long-haul year must be {supported_years[0]} or {supported_years[1]}"
        )
    if not isinstance(threshold_miles, (int, float)) or isinstance(threshold_miles, bool):
        raise TrafficCalculationError("long-haul threshold must be a finite nonnegative number")
    threshold = float(threshold_miles)
    if not math.isfinite(threshold) or threshold < 0:
        raise TrafficCalculationError("long-haul threshold must be a finite nonnegative number")

    manifest, parquet_path, source = load_t100_snapshot(Path(data_root), bundle=bundle)
    airport_id = _validated_airport(airport, manifest)
    rows = _query_rows(parquet_path, airport_id, year)
    observed = tuple(sorted({row[0] for row in rows if isinstance(row[0], int)}))
    missing = tuple(month for month in range(1, 13) if month not in observed)
    coverage = AnnualCoverage(year, observed, missing, not missing and len(observed) == 12)
    if not coverage.complete:
        reason = f"{year} coverage is incomplete; missing months: {', '.join(map(str, missing))}"
        return LongHaulResult(
            airport_id, year, threshold, source, coverage, None, None, None, "insufficient_data", reason
        )

    total = 0
    long_haul = 0
    unknown_distance = 0
    for _, performed_raw, distance_raw in rows:
        performed = _nonnegative_integer(performed_raw)
        if performed is None:
            return _insufficient(
                airport_id, year, threshold, source, coverage, "performed departures contain an invalid count"
            )
        distance = _distance(distance_raw)
        if performed > 0 and distance_raw is not None and distance is None:
            return _insufficient(
                airport_id,
                year,
                threshold,
                source,
                coverage,
                "distance contains an invalid value for a row with positive performed departures",
            )
        total += performed
        if performed > 0 and distance_raw is None:
            unknown_distance += performed
        elif performed > 0 and distance is not None and distance >= threshold:
            long_haul += performed

    if total == 0:
        return LongHaulResult(
            airport_id,
            year,
            threshold,
            source,
            coverage,
            0,
            0,
            None,
            "unavailable",
            "total performed departures are zero; long-haul share is undefined",
            0,
            None,
            None,
        )
    lower = 100.0 * long_haul / total
    upper = 100.0 * (long_haul + unknown_distance) / total
    return LongHaulResult(
        airport_id,
        year,
        threshold,
        source,
        coverage,
        long_haul,
        total,
        lower if unknown_distance == 0 else None,
        "ok",
        None,
        unknown_distance,
        lower,
        upper,
    )


def _distance(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return float(number)


def _insufficient(
    airport: str,
    year: int,
    threshold: float,
    source: T100SnapshotSource,
    coverage: AnnualCoverage,
    reason: str,
) -> LongHaulResult:
    return LongHaulResult(
        airport, year, threshold, source, coverage, None, None, None, "insufficient_data", reason
    )


def _query_rows(parquet_path: Path, airport: str, year: int) -> list[tuple[object, ...]]:
    connection = duckdb.connect()
    try:
        return connection.execute(
            "SELECT month, departures_performed, distance FROM read_parquet(?) "
            "WHERE origin = ? AND year = ? ORDER BY month",
            [str(parquet_path), airport, year],
        ).fetchall()
    except duckdb.Error as exc:
        raise TrafficCalculationError("accepted T-100 snapshot could not be read") from exc
    finally:
        connection.close()
