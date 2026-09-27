"""Descriptive comparisons of four origin operational-strain indicators."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.calculations.operations import (
    DEFAULT_DATA_ROOT,
    OperationsResult,
    calculate_operations,
)

INDICATORS = (
    "cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes",
)
LIMITATION = (
    "This descriptive count is not a significance test, an overall congestion index, "
    "or evidence that a terminal caused the difference. Carrier populations may differ."
)


@dataclass(frozen=True, slots=True)
class MetricComparison:
    key: str
    unit: Literal["percent", "minutes"]
    a_value: float | None
    b_value: float | None
    a_display: str | None
    b_display: str | None
    direction: Literal["higher", "lower", "tied", "unavailable"]
    reason: str | None


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    a: OperationsResult
    b: OperationsResult
    indicators: tuple[MetricComparison, ...]
    status: Literal["ok", "partial", "insufficient_data"]
    comparable_count: int
    higher_count: int
    lower_count: int
    tied_count: int
    mixed_picture: bool
    summary: str
    limitation: str = LIMITATION


def calculate_comparison(
    airport_a: str, airport_b: str, data_root: Path = DEFAULT_DATA_ROOT, *, year: int = 2024
) -> ComparisonResult:
    """Load each origin's accepted operational result and compare its raw values."""
    return compare_operations(
        calculate_operations(airport_a, data_root, year=year),
        calculate_operations(airport_b, data_root, year=year),
    )


def compare_operations(a: OperationsResult, b: OperationsResult) -> ComparisonResult:
    """Compare raw values, retaining source results and rounding only display strings."""
    if a.airport == b.airport:
        raise ValueError("comparison requires two distinct airports")
    if a.year != b.year or a.year != 2024 or a.source.snapshot_id != b.source.snapshot_id:
        raise ValueError("comparison requires the same accepted CY2024 snapshot")
    if a.population != b.population:
        raise ValueError("comparison requires the same population definition")
    indicators = tuple(_compare_metric(key, a, b) for key in INDICATORS)
    higher = sum(item.direction == "higher" for item in indicators)
    lower = sum(item.direction == "lower" for item in indicators)
    tied = sum(item.direction == "tied" for item in indicators)
    comparable = higher + lower + tied
    mixed = higher > 0 and lower > 0
    if comparable == 0:
        status = "insufficient_data"
        summary = "Insufficient data: none of the four operational-strain indicators are comparable."
    else:
        status = "ok" if comparable == len(INDICATORS) else "partial"
        summary = (
            f"{a.airport} is higher on {higher} of {comparable} comparable "
            f"operational-strain indicators versus {b.airport}."
        )
        if mixed:
            summary += " Mixed picture: different indicators favor different airports."
        if comparable < len(INDICATORS):
            summary += f" Skipped {len(INDICATORS) - comparable} unavailable indicator pair(s)."
    return ComparisonResult(a, b, indicators, status, comparable, higher, lower, tied, mixed, summary)


def _compare_metric(key: str, a: OperationsResult, b: OperationsResult) -> MetricComparison:
    first, second = getattr(a, key), getattr(b, key)
    values: list[float | None] = []
    reasons = []
    for airport, metric in ((a.airport, first), (b.airport, second)):
        if metric.status != "ok" or metric.value is None or not math.isfinite(metric.value):
            values.append(None)
            reasons.append(f"{airport}: {metric.reason or 'metric unavailable or nonfinite'}")
        else:
            values.append(metric.value)
    av, bv = values
    direction = "unavailable"
    if av is not None and bv is not None:
        direction = "higher" if av > bv else "lower" if av < bv else "tied"
    return MetricComparison(
        key, "percent" if key.endswith("rate") else "minutes", av, bv,
        None if av is None else f"{av:.2f}", None if bv is None else f"{bv:.2f}",
        direction, "; ".join(reasons) or None,
    )
