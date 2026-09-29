from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.calculations.screen import NEW_ENGLAND_AIRPORTS, calculate_screen
from app.calculations.traffic import (
    DEFAULT_DATA_ROOT,
    AnnualCoverage,
    AnnualTraffic,
    MetricResult,
    PassengerGrowth,
    T100SnapshotSource,
    TrafficResult,
)
from app.sources.bundle import BundleContext, SnapshotRef, load_bundle


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


def test_exclusion_reason_lists_missing_months_for_every_incomplete_year():
    complete = _traffic("PVD", 1000, 1100, 1000, 1100)
    incomplete = _traffic(
        "PVC", 1000, 1100, 1000, 1100, missing_baseline=(12,), missing_2024=(1, 2, 3, 4, 12)
    )

    result = calculate_screen([complete, incomplete])

    assert result.exclusions[0].reason == (
        "2023 coverage is incomplete (missing months: 12); "
        "2024 coverage is incomplete (missing months: 1, 2, 3, 4, 12)"
    )


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
    with pytest.raises(ValueError, match="frozen reference cohort"):
        calculate_screen(_readme_fixture(), selected_airports=["ANC"])


def test_bundle_requires_exact_population_source_and_period_and_excludes_pvc() -> None:
    bundle = _bundle(("BOS", "PVC", "EWB"))
    results = [
        _traffic("BOS", 100, 110, 100, 120, years=(2024, 2025), snapshot_id="recent"),
        _traffic(
            "PVC",
            100,
            110,
            100,
            120,
            missing_2024=(12,),
            years=(2024, 2025),
            snapshot_id="recent",
        ),
        _traffic("EWB", 50, 75, 100, 125, years=(2024, 2025), snapshot_id="recent"),
    ]

    result = calculate_screen(results, bundle=bundle)

    assert result.reference_cohort == ("BOS", "EWB")
    assert result.exclusions[0].airport == "PVC"
    assert all(row.passenger_year == 2025 for row in result.rows)
    assert {row.airport: row.passengers for row in result.rows} == {"BOS": 110, "EWB": 75}

    with pytest.raises(ValueError, match="exact reference cohort"):
        calculate_screen(results[:-1], bundle=bundle)
    mixed = [*results[:-1], _traffic("EWB", 50, 75, 100, 125, years=(2024, 2025))]
    with pytest.raises(ValueError, match="mixed source or period"):
        calculate_screen(mixed, bundle=bundle)


def test_bundle_loader_batches_all_23_then_filters_display(monkeypatch, tmp_path: Path) -> None:
    cohort = tuple(sorted((*NEW_ENGLAND_AIRPORTS, "EWB")))
    bundle = _bundle(cohort)
    fixture = {
        airport: _traffic(
            airport,
            100,
            110,
            100,
            120,
            missing_2024=((12,) if airport == "PVC" else ()),
            years=(2024, 2025),
            snapshot_id="recent",
        )
        for airport in cohort
    }
    calls = []

    def batch(airports, data_root, *, bundle):
        calls.append((airports, data_root, bundle.bundle_id))
        return fixture

    monkeypatch.setattr("app.calculations.screen.calculate_traffic_batch", batch)
    result = calculate_screen(data_root=tmp_path, selected_airports=["EWB"], bundle=bundle)

    assert calls == [(sorted(cohort), tmp_path, "recent-bundle")]
    assert len(result.reference_cohort) == 22
    assert len(result.rows) == 1 and result.rows[0].airport == "EWB"
    assert tuple(item.airport for item in result.exclusions) == ("PVC",)


def _bundle(cohort: tuple[str, ...]) -> BundleContext:
    return BundleContext(
        bundle_id="recent-bundle",
        manifest_sha256="0" * 64,
        baseline_year=2024,
        comparison_year=2025,
        cohort=cohort,
        sources={
            "t100": SnapshotRef(
                source="t100",
                snapshot_id="recent",
                manifest_sha256="1" * 64,
                content_sha256="2" * 64,
                manifest_path=Path("manifest.json"),
                data_path=Path("data.parquet"),
                years=(2024, 2025),
                vintage_id="recent",
                publication_status="staged-candidate",
            )
        },
        evidence_path=Path("evidence.json"),
        evidence_sha256="3" * 64,
    )


def test_packaged_recent_controls_competition_ties_and_historical_parity() -> None:
    data_root = DEFAULT_DATA_ROOT.parents[1]
    if not (data_root / "bundles/annual-2025-r1/manifest.json").is_file():
        pytest.skip("packaged recent bundle candidate is not installed")
    bundle = load_bundle("annual-2025-r1", data_root=data_root)

    recent = calculate_screen(bundle=bundle)
    historical = calculate_screen()
    recent_rows = {row.airport: row for row in recent.rows}

    assert len(bundle.cohort) == 23
    assert len(recent.reference_cohort) == 22
    assert tuple(item.airport for item in recent.exclusions) == ("PVC",)
    assert (recent_rows["HVN"].screen_score, recent_rows["HVN"].rank) == (
        pytest.approx(74.761904762, abs=1e-9),
        1,
    )
    assert (recent_rows["BGR"].screen_score, recent_rows["BGR"].rank) == (
        pytest.approx(72.857142857, abs=1e-9),
        2,
    )
    assert (recent_rows["PWM"].screen_score, recent_rows["PWM"].rank) == (
        pytest.approx(72.857142857, abs=1e-9),
        2,
    )
    assert (recent_rows["BOS"].screen_score, recent_rows["BOS"].rank) == (
        pytest.approx(71.904761905, abs=1e-9),
        4,
    )
    assert [row.airport for row in recent.rows[:4]] == ["HVN", "BGR", "PWM", "BOS"]
    assert [(row.airport, row.screen_score, row.rank) for row in historical.rows[:3]] == [
        ("PVD", 87.0, 1),
        ("PWM", 83.5, 2),
        ("BOS", 80.5, 3),
    ]


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
    missing_baseline: tuple[int, ...] = (),
    years: tuple[int, int] = (2023, 2024),
    snapshot_id: str = "fixture",
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
            snapshot_id=snapshot_id, table="FMG", name="Fixture", url="https://fixture.invalid",
            period=f"{years[0]}-{years[1]}", imported_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        ),
        annual=(
            annual(years[0], passengers_2023, seats_2023, missing_baseline),
            annual(years[1], passengers_2024, seats_2024, missing_2024),
        ),
        growth=PassengerGrowth(years[0], years[1], 0.0, "ok", None),
    )
