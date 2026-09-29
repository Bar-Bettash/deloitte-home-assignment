from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.calculations.comparison import (
    INDICATORS,
    calculate_comparison,
    compare_operations,
)
from app.calculations.operations import (
    OperationalMetric,
    OperationsResult,
    OperationsSource,
)
from app.sources.bundle import load_bundle


def result(airport, values, *, year=2024, carriers=("AA",), snapshot="ontime-test"):
    source = OperationsSource("ontime-test", "BTS", "FGJ", "https://example.test",
                              datetime(2026, 9, 27, tzinfo=timezone.utc), str(year))
    source = replace(source, snapshot_id=snapshot)
    metrics = [OperationalMetric(value, "ok" if value is not None else "unavailable",
                                 0, 10, 12, None if value is not None else "missing observations")
               for value in values]
    return OperationsResult(airport, year, source, tuple(range(1, 13)), (), carriers,
                            12, 0, *metrics)


def test_four_indicators_higher_lower_tied_and_mixed():
    a, b = result("LAX", [2, 1, 10, 20]), result("SNA", [1, 2, 10, 19])
    comparison = compare_operations(a, b)
    assert tuple(item.key for item in comparison.indicators) == INDICATORS
    assert [item.direction for item in comparison.indicators] == ["higher", "lower", "tied", "higher"]
    assert (comparison.higher_count, comparison.lower_count, comparison.tied_count) == (2, 1, 1)
    assert comparison.comparable_count == 4
    assert comparison.status == "ok"
    assert comparison.mixed_picture
    assert "higher on 2 of 4" in comparison.summary
    assert "Mixed picture" in comparison.summary
    assert comparison.a is a and comparison.b is b
    assert "not a significance test" in comparison.limitation


@pytest.mark.parametrize("av,bv,direction", [(1.004, 1.003, "higher"), (1.003, 1.004, "lower")])
def test_raw_comparison_precedes_display_rounding(av, bv, direction):
    comparison = compare_operations(result("LAX", [av] * 4), result("SNA", [bv] * 4))
    for item in comparison.indicators:
        assert item.a_value == av and item.b_value == bv
        assert item.a_display == item.b_display == "1.00"
        assert item.direction == direction


def test_unavailable_pairs_are_skipped_with_explicit_reasons():
    comparison = compare_operations(result("LAX", [None, 1, 3, 4]), result("SNA", [0, None, 2, 4]))
    assert comparison.status == "partial"
    assert comparison.comparable_count == 2
    assert comparison.higher_count == 1
    assert comparison.tied_count == 1
    assert not comparison.mixed_picture
    assert "higher on 1 of 2" in comparison.summary
    assert "Skipped 2" in comparison.summary
    assert "LAX: missing observations" == comparison.indicators[0].reason
    assert "SNA: missing observations" == comparison.indicators[1].reason
    assert comparison.indicators[0].a_display is None
    assert comparison.indicators[0].b_display == "0.00"


def test_no_comparable_pairs_returns_insufficient_data_without_winner():
    comparison = compare_operations(result("LAX", [None, 1, None, 1]), result("SNA", [1, None, 1, None]))
    assert comparison.status == "insufficient_data"
    assert comparison.comparable_count == comparison.higher_count == comparison.lower_count == 0
    assert not comparison.mixed_picture
    assert "Insufficient data" in comparison.summary
    assert "higher" not in comparison.summary
    assert all(item.direction == "unavailable" for item in comparison.indicators)


def test_exact_ties_are_comparable_and_not_mixed():
    comparison = compare_operations(result("LAX", [0, 0, 1, 2]), result("SNA", [0, 0, 1, 2]))
    assert comparison.tied_count == comparison.comparable_count == 4
    assert comparison.higher_count == comparison.lower_count == 0
    assert not comparison.mixed_picture


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_numbers_are_not_compared_or_formatted(value):
    comparison = compare_operations(result("LAX", [value] * 4), result("SNA", [1] * 4))
    assert comparison.status == "insufficient_data"
    assert all(item.a_value is None and item.a_display is None for item in comparison.indicators)


@pytest.mark.parametrize("mismatch", ["airport", "year", "snapshot", "period", "population"])
def test_incompatible_results_rejected(mismatch):
    a, b = result("LAX", [1] * 4), result("SNA", [2] * 4)
    changes = {"airport": {"airport": "LAX"}, "year": {"year": 2023},
               "snapshot": {"source": replace(b.source, snapshot_id="different")},
               "period": {"source": replace(b.source, period="2023")},
               "population": {"population": "different"}}
    with pytest.raises(ValueError):
        compare_operations(a, replace(b, **changes[mismatch]))


def test_reader_convenience_uses_requested_scope(monkeypatch, tmp_path):
    calls = []

    def load(airport, root, *, year):
        calls.append((airport, root, year))
        return result(airport, [1] * 4)

    monkeypatch.setattr("app.calculations.comparison.calculate_operations", load)
    comparison = calculate_comparison("LAX", "SNA", tmp_path, year=2024)
    assert comparison.tied_count == 4
    assert calls == [("LAX", tmp_path, 2024), ("SNA", tmp_path, 2024)]


def test_different_airline_sets_keep_same_reporting_population_comparable():
    a = result("LAX", [1] * 4, carriers=("AA", "DL", "UA"))
    b = result("SNA", [2] * 4, carriers=("AA", "WN"))

    comparison = compare_operations(a, b)

    assert comparison.comparable_count == 4
    assert comparison.lower_count == 4


def test_recent_bundle_defaults_year_and_is_forwarded_to_both_reads(monkeypatch, tmp_path):
    bundle = SimpleNamespace(comparison_year=2025)
    calls = []

    def load(airport, root, *, year, bundle):
        calls.append((airport, root, year, bundle))
        return result(airport, [1] * 4, year=2025, carriers=(airport,))

    monkeypatch.setattr("app.calculations.comparison.calculate_operations", load)

    comparison = calculate_comparison("LAX", "SNA", tmp_path, bundle=bundle)

    assert comparison.a.year == comparison.b.year == 2025
    assert calls == [
        ("LAX", tmp_path, 2025, bundle),
        ("SNA", tmp_path, 2025, bundle),
    ]


@pytest.mark.parametrize("year", [2024, 2026, True])
def test_recent_bundle_rejects_mismatched_explicit_year_before_reads(monkeypatch, year):
    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid comparison year must be rejected before source reads")

    monkeypatch.setattr("app.calculations.comparison.calculate_operations", unexpected)
    with pytest.raises(ValueError, match="year"):
        calculate_comparison("LAX", "SNA", year=year, bundle=SimpleNamespace(comparison_year=2025))


def test_two_recent_results_compare_when_snapshot_period_and_population_match():
    a = result("LAX", [2, None, 10, 20], year=2025, carriers=("AA", "UA"))
    b = result("SNA", [1, 2, 10, 19], year=2025, carriers=("WN",))

    comparison = compare_operations(a, b)

    assert comparison.status == "partial"
    assert comparison.comparable_count == 3
    assert (comparison.higher_count, comparison.tied_count) == (2, 1)


def test_real_recent_lax_sna_controls_use_one_bundle_population() -> None:
    data_root = Path(__file__).resolve().parents[1] / "data"
    bundle = load_bundle("annual-2025-r1", data_root=data_root)

    comparison = calculate_comparison("LAX", "SNA", bundle=bundle)

    assert comparison.a.year == comparison.b.year == 2025
    assert comparison.a.source == comparison.b.source
    assert comparison.a.population == comparison.b.population
    assert (comparison.a.scheduled_count, comparison.b.scheduled_count) == (190_472, 44_997)
    assert [item.direction for item in comparison.indicators] == [
        "lower", "higher", "lower", "higher",
    ]
    assert comparison.indicators[0].a_value == pytest.approx(0.6903901886)
    assert comparison.indicators[0].b_value == pytest.approx(1.0467364491)
    assert comparison.indicators[2].a_value == pytest.approx(13.8000880272)
    assert comparison.indicators[2].b_value == pytest.approx(15.1720689267)
    assert (comparison.higher_count, comparison.lower_count) == (2, 2)
    assert comparison.mixed_picture


def test_real_historical_default_retains_cy2024_controls() -> None:
    comparison = calculate_comparison("LAX", "SNA")

    assert comparison.a.year == comparison.b.year == 2024
    assert (comparison.a.scheduled_count, comparison.b.scheduled_count) == (194_053, 43_519)
    assert [item.direction for item in comparison.indicators] == [
        "lower", "lower", "lower", "higher",
    ]
    assert (comparison.higher_count, comparison.lower_count) == (1, 3)
