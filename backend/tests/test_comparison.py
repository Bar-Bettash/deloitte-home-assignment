from dataclasses import replace
from datetime import datetime, timezone

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


def result(airport, values):
    source = OperationsSource("ontime-test", "BTS", "FGJ", "https://example.test",
                              datetime(2026, 9, 27, tzinfo=timezone.utc))
    metrics = [OperationalMetric(value, "ok" if value is not None else "unavailable",
                                 0, 10, 12, None if value is not None else "missing observations")
               for value in values]
    return OperationsResult(airport, 2024, source, tuple(range(1, 13)), (), ("AA",),
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


@pytest.mark.parametrize("mismatch", ["airport", "year", "snapshot", "population"])
def test_incompatible_results_rejected(mismatch):
    a, b = result("LAX", [1] * 4), result("SNA", [2] * 4)
    changes = {"airport": {"airport": "LAX"}, "year": {"year": 2023},
               "snapshot": {"source": replace(b.source, snapshot_id="different")},
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
