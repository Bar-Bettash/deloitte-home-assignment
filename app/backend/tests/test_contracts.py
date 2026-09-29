from uuid import UUID

import pytest
from app.contracts import (
    MAX_REQUEST_BYTES,
    AnalysisResult,
    MetricValue,
    QueryRequest,
    validate_request_body_size,
)
from pydantic import ValidationError


def test_canonical_analysis_requests_from_api_ui_map():
    free_text = QueryRequest.model_validate(
        {"message": "Compare LAX and Santa Ana congestion."}
    )
    preset = QueryRequest.model_validate(
        {"analysis": {"action": "compare", "airports": ["LAX", "SNA"], "metric": "congestion", "year": 2024}}
    )
    follow_up = QueryRequest.model_validate(
        {"message": "Show only cancellations.", "context_result_id": "11111111-1111-4111-8111-111111111111"}
    )
    explain = QueryRequest.model_validate(
        {"analysis": {"action": "explain"}, "context_result_id": "11111111-1111-4111-8111-111111111111"}
    )
    assert free_text.message.startswith("Compare")
    assert preset.analysis.airports == ["LAX", "SNA"]
    assert follow_up.context_result_id == UUID("11111111-1111-4111-8111-111111111111")
    assert explain.analysis.action == "explain"


def test_request_rejects_missing_or_both_modes_and_extra_fields():
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({})
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"message": "question", "analysis": {"action": "explain"}})
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"message": "question", "client_session_id": "x"})
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"message": "   "})
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"message": "x" * 4001})


@pytest.mark.parametrize(
    "analysis",
    [
        {"action": "compare", "airports": ["LAX"], "metric": "congestion", "year": 2024},
        {"action": "compare", "airports": ["LAX", "LAX"], "metric": "congestion", "year": 2024},
        {"action": "compare", "airports": ["LAX", "BOS"], "metric": "congestion", "year": 2024},
        {"action": "metric", "airports": ["SFO"], "metric": "sfo_pressure", "year": 2023},
        {"action": "rank", "region": "new_england", "airports": ["BOS"], "metric": "screen_score", "year": 2024},
        {"action": "rank", "metric": "screen_score", "year": 2024},
        {"action": "rank", "region": "new_england", "metric": "passenger_growth", "year": 2023},
        {"action": "compare", "airports": ["BOS", "PVD"], "metric": "passenger_growth", "year": 2023},
        {"action": "metric", "airports": ["ANC"], "metric": "passenger_growth", "year": 2023},
        {"action": "metric", "airports": ["BOS"], "metric": "screen_score", "year": 2023},
        {"action": "metric", "airports": ["BOS"], "metric": "screen_score", "year": 2024},
        {"action": "compare", "airports": ["BOS", "PVD"], "metric": "screen_score", "year": 2024},
        {"action": "rank", "airports": ["ANC"], "metric": "passengers", "year": 2024},
        {"action": "metric", "airports": ["ANC"], "metric": "long_haul_share", "year": 2024, "threshold_miles": 0},
        {"action": "metric", "airports": ["ANC"], "metric": "passengers", "year": 2024, "threshold_miles": 3000},
        {"action": "metric", "airports": ["ANC"], "metric": "passengers", "year": 2022},
        {"action": "metric", "airports": ["ANC"], "metric": "passengers", "year": 2026},
        {"action": "metric", "airports": ["ANC"], "metric": "passengers", "bundle_id": "../bad"},
        {"action": "explain", "bundle_id": "annual-2025-r1"},
        {"action": "explain", "metric": "passengers"},
    ],
)
def test_analysis_rejects_invalid_scope_combinations(analysis):
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"analysis": analysis})


def test_screen_score_remains_valid_for_rank():
    request = QueryRequest.model_validate({"analysis": {
        "action": "rank", "region": "new_england", "metric": "screen_score", "year": 2024,
    }})
    assert request.analysis.metric == "screen_score"


@pytest.mark.parametrize(
    "analysis",
    [
        {"action": "metric", "airports": ["BOS"], "metric": "passengers"},
        {"action": "metric", "airports": ["BOS"], "metric": "passengers", "year": 2025},
        {
            "action": "metric",
            "airports": ["BOS"],
            "metric": "passengers",
            "year": 2024,
            "bundle_id": "annual-2025-r1",
        },
        {"action": "rank", "region": "new_england", "metric": "screen_score"},
        {
            "action": "rank",
            "airports": ["EWB"],
            "metric": "passengers",
            "year": 2025,
        },
    ],
)
def test_request_accepts_server_resolved_default_recent_and_explicit_bundle(analysis):
    request = QueryRequest.model_validate({"analysis": analysis}).analysis
    assert request is not None
    assert request.metric == analysis["metric"]
    assert request.year == analysis.get("year")
    assert request.bundle_id == analysis.get("bundle_id")


@pytest.mark.parametrize("key", ["seat_occupancy", "long_haul_share", "cancellation_rate", "diversion_rate"])
def test_unavailable_ratio_allows_null_components_but_available_ratio_requires_them(key):
    unavailable = MetricValue.model_validate({
        "key": key, "value": None, "unit": "percent", "status": "unavailable",
        "source_ids": ["fixture-source"], "reason": "No qualifying data.",
    })
    assert unavailable.numerator is None and unavailable.denominator is None
    with pytest.raises(ValidationError):
        MetricValue.model_validate({
            "key": key, "value": 50.0, "unit": "percent", "status": "ok",
            "source_ids": ["fixture-source"],
        })


def test_request_byte_cap_is_exact_and_rejects_oversize():
    validate_request_body_size(b"x" * MAX_REQUEST_BYTES)
    with pytest.raises(ValueError):
        validate_request_body_size(b"x" * (MAX_REQUEST_BYTES + 1))


def test_canonical_synthetic_result_and_source_linkage():
    result = AnalysisResult.model_validate(
        {
            "result_id": "22222222-2222-4222-8222-222222222222",
            "request_id": "33333333-3333-4333-8333-333333333333",
            "status": "ok",
            "scope": {
                "airports": ["ANC"], "year": 2024, "metric": "long_haul_share",
                "threshold_miles": 3000, "population": "Synthetic scheduled passenger departures",
            },
            "rows": [{"airport": "ANC", "metrics": [{
                "key": "long_haul_share", "value": 40.0, "unit": "percent", "status": "ok",
                "numerator": 8, "denominator": 20, "source_ids": ["fixture-t100"],
            }]}],
            "summary": "In this synthetic fixture, 8 of 20 departures meet the 3,000-mile threshold.",
            "series": [],
            "sources": [{
                "id": "fixture-t100", "name": "Synthetic T-100-shaped fixture, not real airport evidence",
                "url": None, "snapshot_id": "synthetic-only", "period": "CY2024", "retrieved_at": None,
            }],
            "evidence": [], "exclusions": [],
            "limitations": ["Illustrative values only; not a real-data acceptance result."],
        }
    )
    assert result.rows[0].metrics[0].value == 40.0
    assert result.rows[0].metrics[0].unit == "percent"


@pytest.mark.parametrize(
    ("metric", "expected_value", "lower", "upper", "unknown"),
    [
        ({"value": 70.0, "numerator": 7, "denominator": 10}, 70.0, 70.0, 70.0, 0),
        ({"value": None, "numerator": 7, "denominator": 10}, None, 70.0, 100.0, 3),
    ],
)
def test_recent_long_haul_scope_accepts_exact_and_bounded_uncertainty(
    metric, expected_value, lower, upper, unknown
):
    payload = _canonical_result()
    payload["scope"].update(
        {
            "year": 2025,
            "bundle_id": "annual-2025-r1",
            "baseline_year": 2024,
            "comparison_year": 2025,
        }
    )
    payload["rows"][0]["metrics"][0].update(
        {
            **metric,
            "unknown_distance_departures": unknown,
            "lower_percent": lower,
            "upper_percent": upper,
        }
    )

    result = AnalysisResult.model_validate(payload)
    value = result.rows[0].metrics[0]

    assert result.scope.bundle_id == "annual-2025-r1"
    assert (result.scope.baseline_year, result.scope.comparison_year) == (2024, 2025)
    assert value.value == expected_value
    assert (value.lower_percent, value.upper_percent) == (lower, upper)
    assert value.unknown_distance_departures == unknown


def test_long_haul_zero_departures_is_typed_unavailable() -> None:
    metric = MetricValue.model_validate(
        {
            "key": "long_haul_share",
            "value": None,
            "unit": "percent",
            "status": "unavailable",
            "numerator": 0,
            "denominator": 0,
            "unknown_distance_departures": 0,
            "source_ids": ["fixture-source"],
            "reason": "Total performed departures are zero.",
        }
    )
    assert metric.denominator == 0
    assert metric.lower_percent is None and metric.upper_percent is None


def test_exact_long_haul_requires_value_equal_to_collapsed_bounds() -> None:
    exact = {
        "key": "long_haul_share",
        "value": None,
        "unit": "percent",
        "status": "ok",
        "numerator": 7,
        "denominator": 10,
        "unknown_distance_departures": 0,
        "lower_percent": 70.0,
        "upper_percent": 70.0,
        "source_ids": ["fixture-source"],
    }
    with pytest.raises(ValidationError):
        MetricValue.model_validate(exact)
    exact["value"] = 71.0
    with pytest.raises(ValidationError):
        MetricValue.model_validate(exact)


def test_result_rejects_bad_units_unresolved_refs_extra_fields_and_bad_ids():
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate({"success": True})
    with pytest.raises(ValidationError):
        payload = _canonical_result()
        payload["rows"][0]["metrics"][0]["unit"] = "count"
        AnalysisResult.model_validate(payload)


@pytest.mark.parametrize(
    "changes",
    [
        {"lower_percent": 69.0},
        {"upper_percent": 99.0},
        {"unknown_distance_departures": 4},
        {"value": 70.0},
        {"upper_percent": None},
        {"numerator": 7.0},
    ],
)
def test_bounded_long_haul_rejects_inconsistent_counts_bounds_and_exact_value(changes):
    value = {
        "key": "long_haul_share",
        "value": None,
        "unit": "percent",
        "status": "ok",
        "numerator": 7,
        "denominator": 10,
        "unknown_distance_departures": 3,
        "lower_percent": 70.0,
        "upper_percent": 100.0,
        "source_ids": ["fixture-source"],
    }
    value.update(changes)
    with pytest.raises(ValidationError):
        MetricValue.model_validate(value)


def test_uncertainty_fields_reject_non_long_haul_and_partial_resolved_scope():
    with pytest.raises(ValidationError):
        MetricValue.model_validate(
            {
                "key": "seat_occupancy",
                "value": 50.0,
                "unit": "percent",
                "status": "ok",
                "numerator": 5,
                "denominator": 10,
                "unknown_distance_departures": 0,
                "lower_percent": 50.0,
                "upper_percent": 50.0,
                "source_ids": ["fixture-source"],
            }
        )
    payload = _canonical_result()
    payload["scope"].update({"year": 2025, "bundle_id": "annual-2025-r1"})
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


def test_recent_result_rejects_unresolved_or_out_of_period_scope():
    payload = _canonical_result()
    payload["scope"]["year"] = 2025
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)

    payload["scope"].update(
        {
            "bundle_id": "annual-2025-r1",
            "baseline_year": 2023,
            "comparison_year": 2024,
        }
    )
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)
    with pytest.raises(ValidationError):
        payload = _canonical_result()
        payload["rows"][0]["metrics"][0]["source_ids"] = ["missing"]
        AnalysisResult.model_validate(payload)
    with pytest.raises(ValidationError):
        payload = _canonical_result()
        payload["unexpected"] = "extra"
        AnalysisResult.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("rows", 0, "metrics", 0, "value"), float("nan")),
        (("rows", 0, "metrics", 0, "numerator"), float("inf")),
        (("rows", 0, "metrics", 0, "denominator"), float("-inf")),
        (("series", 0, "value"), float("nan")),
        (("scope", "threshold_miles"), float("inf")),
    ],
)
def test_result_rejects_non_finite_numbers(path, value):
    payload = _canonical_result()
    payload["series"] = [{"period": "202401", "value": 1, "unit": "count", "status": "ok"}]
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


def _canonical_result():
    return {
        "result_id": "22222222-2222-4222-8222-222222222222",
        "request_id": "33333333-3333-4333-8333-333333333333", "status": "ok",
        "scope": {"airports": ["ANC"], "year": 2024, "metric": "long_haul_share", "threshold_miles": 3000,
                  "population": "Synthetic scheduled passenger departures"},
        "rows": [{"airport": "ANC", "metrics": [{"key": "long_haul_share", "value": 40.0, "unit": "percent",
                   "status": "ok", "numerator": 8, "denominator": 20, "source_ids": ["fixture-t100"]}]}],
        "summary": "Synthetic", "series": [],
        "sources": [{"id": "fixture-t100", "name": "Fixture", "url": None, "snapshot_id": "fixture",
                     "period": "CY2024", "retrieved_at": None}],
        "evidence": [], "exclusions": [], "limitations": [],
    }
