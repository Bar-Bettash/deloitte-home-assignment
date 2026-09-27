"""Deterministic New England traffic screen with frozen-cohort normalization."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.calculations.traffic import (
    DEFAULT_DATA_ROOT,
    AnnualTraffic,
    TrafficResult,
    calculate_traffic_batch,
)

NEW_ENGLAND_AIRPORTS = frozenset(
    ["BDL", "HVN", "PWM", "BGR", "PQI", "RKD", "BHB", "AUG", "BOS", "ACK", "ORH", "MVY", "HYA", "PVC", "MHT", "PSM", "LEB", "PVD", "WST", "BID", "BTV", "RUT"]
)
SortBy = Literal["screen_score", "passenger_growth"]


@dataclass(frozen=True, slots=True)
class ScreenRow:
    airport: str
    passengers_2024: int | float
    passenger_growth_percent: float
    seat_occupancy_percent: float
    screen_score: float
    rank: int


@dataclass(frozen=True, slots=True)
class ScreenExclusion:
    airport: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScreenResult:
    status: Literal["ok", "insufficient_data"]
    reference_cohort: tuple[str, ...]
    rows: tuple[ScreenRow, ...]
    exclusions: tuple[ScreenExclusion, ...]
    reason: str | None
    sort_by: SortBy


@dataclass(frozen=True, slots=True)
class _Candidate:
    airport: str
    passengers_2024: int
    growth: float
    occupancy: float


def calculate_screen(
    traffic_results: Iterable[TrafficResult] | None = None,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    selected_airports: Sequence[str] | None = None,
    sort_by: SortBy = "screen_score",
) -> ScreenResult:
    """Score the full supplied reference cohort, then optionally filter display rows.

    Omitted ``traffic_results`` loads all 22 airports from one verified snapshot
    with one batch query. Supplied results remain the frozen reference population;
    pass all available New England airports even when the caller displays a subset.
    An airport-year contributes only when both years have complete coverage, valid
    nonnegative passenger/seat totals, positive 2023 passengers and positive 2024 seats.
    """
    if sort_by not in ("screen_score", "passenger_growth"):
        raise ValueError("sort_by must be screen_score or passenger_growth")

    results = tuple(
        calculate_traffic_batch(sorted(NEW_ENGLAND_AIRPORTS), data_root).values()
        if traffic_results is None else traffic_results
    )
    airport_ids = [result.airport.strip().upper() for result in results]
    if len(airport_ids) != len(set(airport_ids)):
        raise ValueError("reference cohort contains duplicate airport results")
    if any(airport not in NEW_ENGLAND_AIRPORTS for airport in airport_ids):
        raise ValueError("reference cohort must contain New England airports only")

    selected = _selected_set(selected_airports, set(airport_ids))
    eligible: list[_Candidate] = []
    exclusions: list[ScreenExclusion] = []
    for result, airport in sorted(zip(results, airport_ids), key=lambda pair: pair[1]):
        candidate, reason = _eligible_candidate(result, airport)
        if candidate is None:
            exclusions.append(ScreenExclusion(airport, reason or "required traffic data is unavailable"))
        else:
            eligible.append(candidate)

    if len(eligible) < 2:
        return ScreenResult(
            status="insufficient_data",
            reference_cohort=tuple(candidate.airport for candidate in eligible),
            rows=(),
            exclusions=tuple(exclusions),
            reason="At least two numerically eligible New England airports are required for a normalized score.",
            sort_by=sort_by,
        )

    growth_percentiles = _percentiles([(candidate.airport, candidate.growth) for candidate in eligible])
    volume_percentiles = _percentiles([(candidate.airport, candidate.passengers_2024) for candidate in eligible])
    occupancy_percentiles = _percentiles([(candidate.airport, candidate.occupancy) for candidate in eligible])
    score_by_airport: dict[str, float] = {}
    candidate_by_airport = {candidate.airport: candidate for candidate in eligible}
    for candidate in eligible:
        score_by_airport[candidate.airport] = 100.0 * (
            0.40 * growth_percentiles[candidate.airport]
            + 0.30 * volume_percentiles[candidate.airport]
            + 0.30 * occupancy_percentiles[candidate.airport]
        )

    if sort_by == "screen_score":
        sorted_candidates = sorted(
            eligible,
            key=lambda candidate: (-score_by_airport[candidate.airport], candidate.airport),
        )
        order_values = score_by_airport
    else:
        sorted_candidates = sorted(eligible, key=lambda candidate: (-candidate.growth, candidate.airport))
        order_values = {candidate.airport: candidate.growth for candidate in eligible}

    rows = tuple(
        ScreenRow(
            airport=candidate.airport,
            passengers_2024=candidate.passengers_2024,
            passenger_growth_percent=candidate.growth,
            seat_occupancy_percent=candidate.occupancy,
            screen_score=score_by_airport[candidate.airport],
            rank=1 + sum(
                other_value > order_values[candidate.airport]
                for airport, other_value in order_values.items()
                if airport != candidate.airport
            ),
        )
        for candidate in sorted_candidates
        if selected is None or candidate.airport in selected
    )
    return ScreenResult(
        status="ok",
        reference_cohort=tuple(sorted(candidate_by_airport)),
        rows=rows,
        exclusions=tuple(exclusions),
        reason=None,
        sort_by=sort_by,
    )


def _selected_set(selected_airports: Sequence[str] | None, reference_ids: set[str]) -> set[str] | None:
    if selected_airports is None:
        return None
    normalized = [airport.strip().upper() for airport in selected_airports]
    if len(normalized) != len(set(normalized)):
        raise ValueError("selected airports must be unique")
    if not set(normalized) <= reference_ids:
        raise ValueError("selected airports must belong to the frozen reference cohort")
    return set(normalized)


def _eligible_candidate(
    result: TrafficResult,
    airport: str,
) -> tuple[_Candidate | None, str | None]:
    by_year = {annual.year: annual for annual in result.annual}
    if set(by_year) != {2023, 2024}:
        return None, "both 2023 and 2024 traffic results are required"
    annual_2023 = by_year[2023]
    annual_2024 = by_year[2024]
    for annual in (annual_2023, annual_2024):
        if not _complete_coverage(annual):
            missing = ", ".join(map(str, annual.coverage.missing_months)) or "unknown"
            return None, f"{annual.year} coverage is incomplete (missing months: {missing})"

    p23 = _available_measure(annual_2023.passengers)
    p24 = _available_measure(annual_2024.passengers)
    s23 = _available_measure(annual_2023.seats)
    s24 = _available_measure(annual_2024.seats)
    if any(value is None for value in (p23, p24, s23, s24)):
        return None, "passenger or seat totals are unavailable or invalid"
    if p23 <= 0:
        return None, "2023 passenger baseline must be positive"
    if s24 <= 0:
        return None, "2024 seats must be positive for occupancy"

    growth = (p24 - p23) / p23 * 100.0
    occupancy = p24 / s24 * 100.0
    if not math.isfinite(growth) or not math.isfinite(occupancy):
        return None, "growth or occupancy is non-finite"
    return (
        _Candidate(airport, annual_2024.passengers.value, growth, occupancy),
        None,
    )


def _complete_coverage(annual: AnnualTraffic) -> bool:
    return (
        annual.coverage.complete
        and annual.coverage.observed_months == tuple(range(1, 13))
        and not annual.coverage.missing_months
    )


def _available_measure(metric) -> int | None:
    if metric.status != "ok":
        return None
    value = metric.value
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _percentiles(airport_values: list[tuple[str, int | float]]) -> dict[str, float]:
    values = [value for _airport, value in airport_values]
    count = len(values)
    percentiles: dict[int | float, float] = {}
    for value in set(values):
        lower = sum(item < value for item in values)
        tied = sum(item == value for item in values)
        average_rank = lower + (tied + 1) / 2
        percentiles[value] = (average_rank - 1) / (count - 1)
    return {airport: percentiles[value] for airport, value in airport_values}
