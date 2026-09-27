"""Deterministic New England traffic screen with frozen-cohort normalization."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

from app.calculations.traffic import (
    DEFAULT_DATA_ROOT,
    AnnualTraffic,
    TrafficResult,
    calculate_traffic_batch,
)
from app.sources.bundle import BundleContext

NEW_ENGLAND_AIRPORTS = frozenset(
    ["BDL", "HVN", "PWM", "BGR", "PQI", "RKD", "BHB", "AUG", "BOS", "ACK", "ORH", "MVY", "HYA", "PVC", "MHT", "PSM", "LEB", "PVD", "WST", "BID", "BTV", "RUT"]
)
SortBy = Literal["screen_score", "passenger_growth"]


@dataclass(frozen=True, slots=True)
class ScreenRow:
    airport: str
    passengers: int | float
    passenger_year: int
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
    passengers: int
    growth: float
    occupancy: float


def calculate_screen(
    traffic_results: Iterable[TrafficResult] | None = None,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    selected_airports: Sequence[str] | None = None,
    sort_by: SortBy = "screen_score",
    bundle: BundleContext | None = None,
) -> ScreenResult:
    """Score the full supplied reference cohort, then optionally filter display rows.

    Omitted ``traffic_results`` loads the selected bundle cohort from one verified
    snapshot with one batch query. Supplied results remain the frozen reference
    population; pass the complete cohort even when the caller displays a subset.
    An airport contributes only when both selected years have complete coverage,
    valid counts, positive baseline passengers and positive comparison-year seats.
    """
    if sort_by not in ("screen_score", "passenger_growth"):
        raise ValueError("sort_by must be screen_score or passenger_growth")

    years = _selected_years(bundle)
    expected_cohort = set(bundle.cohort) if bundle is not None else set(NEW_ENGLAND_AIRPORTS)
    if traffic_results is None:
        loaded = (
            calculate_traffic_batch(sorted(expected_cohort), data_root, bundle=bundle)
            if bundle is not None
            else calculate_traffic_batch(sorted(expected_cohort), data_root)
        )
        results = tuple(loaded.values())
    else:
        results = tuple(traffic_results)
    airport_ids = [result.airport.strip().upper() for result in results]
    if len(airport_ids) != len(set(airport_ids)):
        raise ValueError("reference cohort contains duplicate airport results")
    if any(airport not in expected_cohort for airport in airport_ids):
        raise ValueError("reference cohort must contain New England airports only")
    if bundle is not None:
        _validate_bundle_results(results, airport_ids, bundle, years, expected_cohort)

    selected = _selected_set(selected_airports, set(airport_ids))
    eligible: list[_Candidate] = []
    exclusions: list[ScreenExclusion] = []
    for result, airport in sorted(zip(results, airport_ids), key=lambda pair: pair[1]):
        candidate, reason = _eligible_candidate(result, airport, years)
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
    volume_percentiles = _percentiles([(candidate.airport, candidate.passengers) for candidate in eligible])
    occupancy_percentiles = _percentiles([(candidate.airport, candidate.occupancy) for candidate in eligible])
    score_by_airport: dict[str, float] = {}
    candidate_by_airport = {candidate.airport: candidate for candidate in eligible}
    for candidate in eligible:
        score_by_airport[candidate.airport] = float(
            100
            * (
                Fraction(2, 5) * growth_percentiles[candidate.airport]
                + Fraction(3, 10) * volume_percentiles[candidate.airport]
                + Fraction(3, 10) * occupancy_percentiles[candidate.airport]
            )
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
            passengers=candidate.passengers,
            passenger_year=years[1],
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
    years: tuple[int, int] = (2023, 2024),
) -> tuple[_Candidate | None, str | None]:
    by_year = {annual.year: annual for annual in result.annual}
    if set(by_year) != set(years):
        return None, f"both {years[0]} and {years[1]} traffic results are required"
    baseline = by_year[years[0]]
    comparison = by_year[years[1]]
    for annual in (baseline, comparison):
        if not _complete_coverage(annual):
            missing = ", ".join(map(str, annual.coverage.missing_months)) or "unknown"
            return None, f"{annual.year} coverage is incomplete (missing months: {missing})"

    baseline_passengers = _available_measure(baseline.passengers)
    comparison_passengers = _available_measure(comparison.passengers)
    baseline_seats = _available_measure(baseline.seats)
    comparison_seats = _available_measure(comparison.seats)
    if any(
        value is None
        for value in (
            baseline_passengers,
            comparison_passengers,
            baseline_seats,
            comparison_seats,
        )
    ):
        return None, "passenger or seat totals are unavailable or invalid"
    if baseline_passengers <= 0:
        return None, f"{years[0]} passenger baseline must be positive"
    if comparison_seats <= 0:
        return None, f"{years[1]} seats must be positive for occupancy"

    growth = (comparison_passengers - baseline_passengers) / baseline_passengers * 100.0
    occupancy = comparison_passengers / comparison_seats * 100.0
    if not math.isfinite(growth) or not math.isfinite(occupancy):
        return None, "growth or occupancy is non-finite"
    return (
        _Candidate(airport, comparison.passengers.value, growth, occupancy),
        None,
    )


def _selected_years(bundle: BundleContext | None) -> tuple[int, int]:
    return (2023, 2024) if bundle is None else (bundle.baseline_year, bundle.comparison_year)


def _validate_bundle_results(
    results: tuple[TrafficResult, ...],
    airport_ids: list[str],
    bundle: BundleContext,
    years: tuple[int, int],
    expected_cohort: set[str],
) -> None:
    ref = bundle.sources.get("t100")
    if ref is None or ref.years != years:
        raise ValueError("bundle T-100 source does not match the screen period")
    if set(airport_ids) != expected_cohort or len(airport_ids) != len(expected_cohort):
        raise ValueError("bundle screen results must contain the exact reference cohort")
    for result in results:
        result_years = {annual.year for annual in result.annual}
        growth_years = (result.growth.baseline_year, result.growth.comparison_year)
        if (
            result.source.snapshot_id != ref.snapshot_id
            or result.source.period != f"{years[0]}-{years[1]}"
            or result_years != set(years)
            or growth_years != years
        ):
            raise ValueError("bundle screen results contain mixed source or period data")


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


def _percentiles(airport_values: list[tuple[str, int | float]]) -> dict[str, Fraction]:
    values = [value for _airport, value in airport_values]
    count = len(values)
    percentiles: dict[int | float, Fraction] = {}
    for value in set(values):
        lower = sum(item < value for item in values)
        tied = sum(item == value for item in values)
        percentiles[value] = Fraction(2 * lower + tied - 1, 2 * (count - 1))
    return {airport: percentiles[value] for airport, value in airport_values}
