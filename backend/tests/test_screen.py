from datetime import datetime, timezone

from app.calculations.screen import NEW_ENGLAND_AIRPORTS, calculate_screen
from app.calculations.traffic import (
    AnnualCoverage,
    AnnualTraffic,
    MetricResult,
    PassengerGrowth,
    T100SnapshotSource,
    TrafficResult,
)


def test_readme_fixture_produces_screen_scores_30_50_70():
    result = calculate_screen(_readme_fixture())

    assert result.status == "ok"
    assert {row.airport: row.screen_score for row in result.rows} == {
        "BOS": 30.0,
        "PVD": 50.0,
        "BTV": 70.0,
    }
    assert [row.airport for row in result.rows] == ["BTV", "PVD", "BOS"]
    assert [row.rank for row in result.rows] == [1, 2, 3]


def test_snapshot_screen_batches_full_cohort_once_preserving_scores_and_exclusions(monkeypatch, tmp_path):
    fixture = {item.airport: item for item in _readme_fixture()}
    for airport in NEW_ENGLAND_AIRPORTS - fixture.keys():
        fixture[airport] = _traffic(airport, 100, 110, 100, 110, missing_2024=(12,))
    expected = calculate_screen(fixture.values(), selected_airports=["BOS"])
    calls = []

    def batch(airports, data_root):
        calls.append((airports, data_root))
        return fixture

    monkeypatch.setattr("app.calculations.screen.calculate_traffic_batch", batch)
    actual = calculate_screen(data_root=tmp_path, selected_airports=["BOS"])

    assert calls == [(sorted(NEW_ENGLAND_AIRPORTS), tmp_path)]
    assert actual == expected
    assert actual.rows[0].screen_score == 30.0
    assert actual.reference_cohort == ("BOS", "BTV", "PVD")
    assert len(actual.exclusions) == 19


def test_injected_screen_results_do_not_load_snapshot(monkeypatch):
    def unexpected_batch(*args):
        raise AssertionError("injected results must not recheck the snapshot")

    monkeypatch.setattr("app.calculations.screen.calculate_traffic_batch", unexpected_batch)
    assert calculate_screen(_readme_fixture()).status == "ok"


def test_equal_component_values_use_average_rank_and_airport_id_for_display():
    same = _traffic("BOS", 1000, 1100, 1000, 1100)
    same2 = _traffic("PVD", 1000, 1100, 1000, 1100)

    result = calculate_screen([same, same2])

    assert [row.airport for row in result.rows] == ["BOS", "PVD"]
    assert [row.screen_score for row in result.rows] == [50.0, 50.0]
    assert [row.rank for row in result.rows] == [1, 1]


def test_single_eligible_airport_returns_insufficient_data_without_score():
    result = calculate_screen([_traffic("BOS", 1000, 1100, 1000, 1100)])

    assert result.status == "insufficient_data"
    assert result.rows == ()
    assert result.reference_cohort == ("BOS",)
    assert "two numerically eligible" in (result.reason or "")


def test_ineligible_airports_are_excluded_with_explicit_reason():
    complete = _traffic("PVD", 1000, 1100, 1000, 1100)
    zero_baseline = _traffic("BOS", 0, 100, 0, 100)

    result = calculate_screen([complete, zero_baseline])

    assert result.status == "insufficient_data"
    assert result.reference_cohort == ("PVD",)
    assert result.exclusions[0].airport == "BOS"
    assert "baseline must be positive" in result.exclusions[0].reason


def test_fractional_traffic_count_is_ineligible():
    complete = _traffic("PVD", 1000, 1100, 1000, 1100)
    fractional = _traffic("BOS", 1000.5, 1100, 1000, 1100)

    result = calculate_screen([complete, fractional])

    assert result.status == "insufficient_data"
    assert result.exclusions[0].airport == "BOS"
    assert "unavailable or invalid" in result.exclusions[0].reason


def test_incomplete_year_coverage_excludes_airport_instead_of_imputing_zero():
    complete = _traffic("PVD", 1000, 1100, 1000, 1100)
    incomplete = _traffic("BOS", 1000, 1100, 1000, 1100, missing_2024=(12,))

    result = calculate_screen([complete, incomplete])

    assert result.status == "insufficient_data"
    assert result.reference_cohort == ("PVD",)
    assert "2024 coverage is incomplete" in result.exclusions[0].reason


def test_raw_growth_sort_is_not_composite_sort_and_ranks_use_requested_order():
    candidates = [
        _traffic("BOS", 1000, 1300, 2000, 2600),
        _traffic("PVD", 2000, 2200, 2000, 2200),
        _traffic("BTV", 2000, 2100, 2500, 2625),
    ]
    composite = calculate_screen(candidates)
    raw_growth = calculate_screen(candidates, sort_by="passenger_growth")

    assert [row.airport for row in composite.rows] == ["PVD", "BOS", "BTV"]
    assert [row.airport for row in raw_growth.rows] == ["BOS", "PVD", "BTV"]
    assert [row.rank for row in raw_growth.rows] == [1, 2, 3]
    assert {row.airport: row.screen_score for row in raw_growth.rows} == {
        row.airport: row.screen_score for row in composite.rows
    }


def test_filtering_preserves_full_reference_cohort_scores():
    full = calculate_screen(_readme_fixture())
    filtered = calculate_screen(_readme_fixture(), selected_airports=["BOS"])

    assert filtered.reference_cohort == full.reference_cohort == ("BOS", "BTV", "PVD")
    assert len(filtered.rows) == 1
    assert filtered.rows[0].airport == "BOS"
    assert filtered.rows[0].screen_score == 30.0


def test_selected_airport_must_belong_to_reference_cohort():
    import pytest

    with pytest.raises(ValueError, match="frozen reference cohort"):
        calculate_screen(_readme_fixture(), selected_airports=["ANC"])


def _readme_fixture() -> list[TrafficResult]:
    # Passenger baselines reproduce 5%, 10%, and 15% growth while keeping
    # the fixture's P24 volume and 2024 occupancy component orderings.
    return [
        _traffic("BOS", 60, 63, 60, 70),
        _traffic("PVD", 140, 154, 140, 220),
        _traffic("BTV", 80, 92, 80, 115),
    ]


def _traffic(
    airport: str,
    passengers_2023: float,
    passengers_2024: float,
    seats_2023: float,
    seats_2024: float,
    *,
    missing_2024: tuple[int, ...] = (),
) -> TrafficResult:
    def annual(year: int, passengers: float, seats: float, missing: tuple[int, ...] = ()) -> AnnualTraffic:
        months = tuple(month for month in range(1, 13) if month not in missing)
        coverage = AnnualCoverage(year, months, missing, not missing)
        return AnnualTraffic(
            year=year,
            coverage=coverage,
            passengers=MetricResult(passengers, "ok", None),
            seats=MetricResult(seats, "ok", None),
            performed_departures=MetricResult(12, "ok", None),
            occupancy_percent=(
                MetricResult(100 * passengers / seats, "ok", None)
                if seats > 0
                else MetricResult(None, "unavailable", "seats are zero")
            ),
        )

    return TrafficResult(
        airport=airport,
        source=T100SnapshotSource(
            snapshot_id="fixture", table="FMG", name="Fixture", url="https://fixture.invalid",
            period="2023-2024", imported_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        ),
        annual=(
            annual(2023, passengers_2023, seats_2023),
            annual(2024, passengers_2024, seats_2024, missing_2024),
        ),
        growth=PassengerGrowth(2023, 2024, 0.0, "ok", None),
    )
