from uuid import UUID

import pytest
from app.contracts import (
    MAX_REQUEST_BYTES,
    AnalysisResult,
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
        {"action": "rank", "airports": ["ANC"], "metric": "passengers", "year": 2024},
        {"action": "metric", "airports": ["ANC"], "metric": "long_haul_share", "year": 2024, "threshold_miles": 0},
        {"action": "metric", "airports": ["ANC"], "metric": "passengers", "year": 2024, "threshold_miles": 3000},
        {"action": "metric", "airports": ["ANC"], "metric": "passengers", "year": 2025},
        {"action": "explain", "metric": "passengers"},
    ],
)
def test_analysis_rejects_invalid_scope_combinations(analysis):
    with pytest.raises(ValidationError):
        QueryRequest.model_validate({"analysis": analysis})


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


def test_result_rejects_bad_units_unresolved_refs_extra_fields_and_bad_ids():
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate({"success": True})
    with pytest.raises(ValidationError):
        payload = _canonical_result()
        payload["rows"][0]["metrics"][0]["unit"] = "count"
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
