import asyncio
import json
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from app import dispatch, main
from app.calculations.long_haul import calculate_long_haul_share
from app.calculations.operations import calculate_operations
from app.calculations.screen import ScreenExclusion, ScreenResult
from app.calculations.sfo import SFOTrendError, calculate_sfo_enplaned_trend
from app.calculations.traffic import MetricResult, calculate_traffic_batch
from app.contracts import MAX_REQUEST_BYTES
from app.dispatch import DispatchFailure
from app.model_adapter import ModelAdapterError, ModelInterpretation, ModelUsage
from app.settings import Settings, load_hosting
from app.sources.bundle import DEFAULT_DATA_ROOT as BUNDLE_DATA_ROOT
from app.sources.bundle import load_bundle
from fastapi.testclient import TestClient

client = TestClient(main.app)
SFO_REQUEST = {
    "analysis": {
        "action": "metric",
        "airports": ["SFO"],
        "metric": "sfo_enplaned_trend",
        "year": 2024,
    }
}


@pytest.fixture(autouse=True)
def reset_query_slots():
    main.query_slots.reset()
    yield
    main.query_slots.reset()


def context_claims(http_client):
    """Verify the client's context cookie exactly as the server would."""
    return main._context_signer(load_hosting({})).verify(http_client.cookies.get(main.CONTEXT_COOKIE))


def admitted_settings(**overrides):
    values = {
        "model_api_key": "offline-test-only", "model_name": "fake-model",
        "model_runtime_enabled": True, "model_admitted_name": "fake-model",
        "model_admitted_adapter_sha256": main.ADAPTER_SHA256,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _write_accepted_registry(root: Path, payload: dict) -> None:
    directory = root / "bundles"
    directory.mkdir(parents=True)
    (directory / "accepted.json").write_text(json.dumps(payload), encoding="utf-8")


def _registry_payload(*, checksum: str = "a" * 64) -> dict:
    return {
        "schema_version": 1,
        "default_bundle_id": "annual-2025-r1",
        "bundles": [
            {"bundle_id": "annual-2025-r1", "manifest_sha256": checksum},
        ],
    }


def _candidate_bundle():
    return load_bundle("annual-2025-r1", data_root=BUNDLE_DATA_ROOT)


def test_accepted_bundle_reader_missing_registry_preserves_historical_only(
    tmp_path: Path, monkeypatch
) -> None:
    assert dispatch._read_accepted_bundle(data_root=tmp_path) is None
    reader = dispatch._read_accepted_bundle
    monkeypatch.setattr(
        dispatch,
        "_read_accepted_bundle",
        lambda bundle_id=None: reader(bundle_id, data_root=tmp_path),
    )
    request = main.AnalysisRequest(
        action="metric", airports=["BOS"], metric="passengers", year=2025
    )
    with pytest.raises(DispatchFailure, match="recent period is not accepted"):
        dispatch._resolve_request(request)


def test_missing_registry_defaults_omitted_period_to_historical_2024(monkeypatch) -> None:
    monkeypatch.setattr(dispatch, "_read_accepted_bundle", lambda _bundle_id=None: None)
    resolved, bundle = dispatch._resolve_request(
        main.AnalysisRequest(action="metric", airports=["BOS"], metric="passengers")
    )
    assert (resolved.year, resolved.bundle_id, bundle) == (2024, None, None)


def test_malformed_registry_fails_closed_without_historical_fallback(monkeypatch) -> None:
    def malformed(_bundle_id=None):
        raise dispatch.BundleRegistryError("bad registry")

    monkeypatch.setattr(dispatch, "_read_accepted_bundle", malformed)
    with pytest.raises(DispatchFailure, match="bundle state is invalid") as raised:
        dispatch._resolve_request(
            main.AnalysisRequest(action="metric", airports=["BOS"], metric="passengers")
        )
    assert (raised.value.code, raised.value.status_code) == ("data_unavailable", 503)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "default_bundle_id": "annual-2025-r1"},
        {**_registry_payload(), "schema_version": 2},
        {**_registry_payload(), "schema_version": True},
        {
            **_registry_payload(),
            "bundles": [
                {"bundle_id": "annual-2025-r1", "manifest_sha256": "a" * 64},
                {"bundle_id": "annual-2025-r1", "manifest_sha256": "b" * 64},
            ],
        },
        {**_registry_payload(), "default_bundle_id": "unlisted"},
    ],
)
def test_accepted_bundle_reader_rejects_malformed_duplicate_or_bad_default(
    tmp_path: Path, payload: dict
) -> None:
    _write_accepted_registry(tmp_path, payload)
    with pytest.raises(dispatch.BundleRegistryError):
        dispatch._read_accepted_bundle(data_root=tmp_path)


def test_accepted_bundle_reader_rejects_duplicate_raw_json_keys(tmp_path: Path) -> None:
    directory = tmp_path / "bundles"
    directory.mkdir()
    (directory / "accepted.json").write_text(
        '{"schema_version":1,"schema_version":1,"default_bundle_id":"x",'
        '"bundles":[{"bundle_id":"x","manifest_sha256":"' + "a" * 64 + '"}]}',
        encoding="utf-8",
    )
    with pytest.raises(dispatch.BundleRegistryError, match="invalid"):
        dispatch._read_accepted_bundle(data_root=tmp_path)


def test_accepted_bundle_reader_rejects_unlisted_and_hash_mismatch(tmp_path: Path, monkeypatch) -> None:
    _write_accepted_registry(tmp_path, _registry_payload())
    monkeypatch.setattr(
        dispatch,
        "load_bundle",
        lambda _bundle_id, *, data_root: SimpleNamespace(manifest_sha256="b" * 64),
    )
    with pytest.raises(DispatchFailure, match="not accepted"):
        dispatch._read_accepted_bundle("annual-2026-r1", data_root=tmp_path)
    with pytest.raises(dispatch.BundleRegistryError, match="manifest hash"):
        dispatch._read_accepted_bundle(data_root=tmp_path)


def test_accepted_bundle_reader_returns_hash_bound_context(tmp_path: Path, monkeypatch) -> None:
    _write_accepted_registry(tmp_path, _registry_payload())
    context = SimpleNamespace(manifest_sha256="a" * 64)
    monkeypatch.setattr(dispatch, "load_bundle", lambda _bundle_id, *, data_root: context)
    assert dispatch._read_accepted_bundle(data_root=tmp_path) is context


def test_request_resolution_preserves_explicit_historical_without_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        dispatch,
        "_read_accepted_bundle",
        lambda *_args, **_kwargs: pytest.fail("historical request consulted bundle registry"),
    )
    for year in (2023, 2024):
        request = main.AnalysisRequest(
            action="metric", airports=["BOS"], metric="passengers", year=year
        )
        resolved, bundle = dispatch._resolve_request(request)
        assert (resolved.year, resolved.bundle_id, bundle) == (year, None, None)


def test_request_resolution_uses_newest_accepted_period_and_permits_level_baseline(monkeypatch) -> None:
    bundle = _candidate_bundle()
    monkeypatch.setattr(dispatch, "_read_accepted_bundle", lambda _bundle_id=None: bundle)
    recent, recent_bundle = dispatch._resolve_request(
        main.AnalysisRequest(action="metric", airports=["BOS"], metric="passengers")
    )
    baseline, baseline_bundle = dispatch._resolve_request(
        main.AnalysisRequest(
            action="metric", airports=["BOS"], metric="passengers", year=2024,
            bundle_id=bundle.bundle_id,
        )
    )
    assert (recent.year, recent.bundle_id, recent_bundle) == (2025, bundle.bundle_id, bundle)
    assert (baseline.year, baseline.bundle_id, baseline_bundle) == (2024, bundle.bundle_id, bundle)
    with pytest.raises(DispatchFailure, match="requires the bundle comparison year"):
        dispatch._resolve_request(
            main.AnalysisRequest(
                action="rank", region="new_england", metric="screen_score", year=2024,
                bundle_id=bundle.bundle_id,
            )
        )


def test_real_recent_bundle_dispatch_binds_scope_cohort_and_anc_counts(monkeypatch) -> None:
    bundle = _candidate_bundle()
    monkeypatch.setattr(dispatch, "_read_accepted_bundle", lambda _bundle_id=None: bundle)

    traffic = dispatch.dispatch_analysis(
        main.AnalysisRequest(
            action="metric", airports=["BOS"], metric="passengers", year=2025
        ),
        uuid4(),
    )
    screen = dispatch.dispatch_analysis(
        main.AnalysisRequest(
            action="rank", region="new_england", metric="screen_score", year=2025
        ),
        uuid4(),
    )
    anc = dispatch.dispatch_analysis(
        main.AnalysisRequest(
            action="metric", airports=["ANC"], metric="long_haul_share", year=2025,
            threshold_miles=3000,
        ),
        uuid4(),
    )

    assert (
        traffic.scope.year,
        traffic.scope.bundle_id,
        traffic.scope.baseline_year,
        traffic.scope.comparison_year,
    ) == (2025, bundle.bundle_id, 2024, 2025)
    assert len(screen.rows) == 22
    assert "EWB" in {row.airport for row in screen.rows}
    assert "PVC" not in {row.airport for row in screen.rows}
    metric = anc.rows[0].metrics[0]
    assert (metric.numerator, metric.denominator, metric.unknown_distance_departures) == (999, 36_040, 0)
    assert metric.value == pytest.approx(2.771920, abs=1e-6)
    assert metric.lower_percent == metric.upper_percent == metric.value


@pytest.mark.parametrize(
    "metric, airports, expected_metric_count",
    [
        ("congestion", ["LAX"], 4),
        ("sfo_enplaned_trend", ["SFO"], 2),
        ("sfo_pressure", ["SFO"], 12),
    ],
)
def test_real_recent_bundle_dispatches_operations_and_sfo_workflows(
    monkeypatch, metric, airports, expected_metric_count
) -> None:
    bundle = _candidate_bundle()
    monkeypatch.setattr(dispatch, "_read_accepted_bundle", lambda _bundle_id=None: bundle)
    result = dispatch.dispatch_analysis(
        main.AnalysisRequest(
            action="metric", airports=airports, metric=metric, year=2025
        ),
        uuid4(),
    )
    assert result.scope.bundle_id == bundle.bundle_id
    assert result.scope.year == bundle.comparison_year
    assert len(result.rows[0].metrics) == expected_metric_count
    assert result.sources


def test_recent_bundle_evidence_integrity_failure_is_not_downgraded_to_partial(monkeypatch) -> None:
    bundle = _candidate_bundle()

    def corrupt(*_args, **_kwargs):
        raise dispatch.EvidenceIntegrityError("corrupt evidence")

    monkeypatch.setattr(dispatch, "load_evidence", corrupt)
    with pytest.raises(DispatchFailure, match="evidence is unavailable") as raised:
        dispatch._rank_evidence(["BOS"], bundle=bundle)
    assert (raised.value.code, raised.value.status_code) == ("data_unavailable", 503)


@pytest.mark.parametrize(
    "updates, expected_status, expected_bounds",
    [
        (
            {"long_haul_departures": 7, "total_departures": 10,
             "unknown_distance_departures": 3, "share_percent": None,
             "lower_percent": 70.0, "upper_percent": 100.0,
             "status": "ok", "reason": None},
            "ok", (70.0, 100.0),
        ),
        (
            {"long_haul_departures": 0, "total_departures": 0,
             "unknown_distance_departures": 0, "share_percent": None,
             "lower_percent": None, "upper_percent": None,
             "status": "unavailable", "reason": "total departures are zero"},
            "unavailable", (None, None),
        ),
    ],
)
def test_long_haul_dispatch_serializes_bounds_and_zero_departures(
    monkeypatch, updates, expected_status, expected_bounds
) -> None:
    base = calculate_long_haul_share("ANC", 2024, 3000)
    monkeypatch.setattr(
        dispatch, "calculate_long_haul_share", lambda *_args, **_kwargs: replace(base, **updates)
    )
    result = dispatch.dispatch_analysis(
        main.AnalysisRequest(
            action="metric", airports=["ANC"], metric="long_haul_share", year=2024,
            threshold_miles=3000,
        ),
        uuid4(),
    )
    metric = result.rows[0].metrics[0]
    assert metric.status == expected_status
    assert (metric.lower_percent, metric.upper_percent) == expected_bounds
    assert metric.value is None


def test_long_haul_dispatch_rejects_inconsistent_calculator_counts(monkeypatch) -> None:
    base = calculate_long_haul_share("ANC", 2024, 3000)
    invalid = replace(
        base, long_haul_departures=8, total_departures=10,
        unknown_distance_departures=3, share_percent=None,
        lower_percent=80.0, upper_percent=110.0,
    )
    monkeypatch.setattr(dispatch, "calculate_long_haul_share", lambda *_args, **_kwargs: invalid)
    with pytest.raises(DispatchFailure, match="output is inconsistent") as raised:
        dispatch.dispatch_analysis(
            main.AnalysisRequest(
                action="metric", airports=["ANC"], metric="long_haul_share", year=2024,
                threshold_miles=3000,
            ),
            uuid4(),
        )
    assert (raised.value.code, raised.value.status_code) == ("data_unavailable", 503)


def test_bounded_long_haul_explanation_uses_saved_bounds_without_reload(monkeypatch) -> None:
    base = calculate_long_haul_share("ANC", 2024, 3000)
    bounded = replace(
        base, long_haul_departures=7, total_departures=10,
        unknown_distance_departures=3, share_percent=None,
        lower_percent=70.0, upper_percent=100.0,
    )
    monkeypatch.setattr(dispatch, "calculate_long_haul_share", lambda *_args: bounded)
    previous = dispatch.dispatch_analysis(
        main.AnalysisRequest(
            action="metric", airports=["ANC"], metric="long_haul_share", year=2024,
            threshold_miles=3000,
        ),
        uuid4(),
    )
    monkeypatch.setattr(
        dispatch,
        "_resolve_request",
        lambda *_args: pytest.fail("explain resolved sources again"),
    )
    explained = dispatch.dispatch_analysis(
        main.AnalysisRequest(action="explain"), uuid4(), previous=previous
    )
    assert "between 70 and 100 percent" in explained.summary
    assert "3 departures have unknown distance" in explained.summary
    assert "unavailable" not in explained.summary.split(".", 1)[0]


@pytest.mark.parametrize(
    "analysis",
    [
        {"action": "metric", "airports": ["EWB"], "metric": "passengers", "year": 2024},
        {"action": "rank", "airports": ["BOS", "EWB"], "metric": "passengers", "year": 2024},
    ],
)
def test_historical_ewb_scope_rejects_before_calculation(monkeypatch, analysis) -> None:
    monkeypatch.setattr(
        dispatch,
        "calculate_traffic_batch",
        lambda *_args, **_kwargs: pytest.fail("unsupported cohort reached calculator"),
    )
    response = client.post("/api/query", json={"analysis": analysis})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_scope"


def test_sfo_query_returns_real_typed_snapshot_result_and_matching_request_id():
    expected = calculate_sfo_enplaned_trend()
    response = client.post("/api/query", json=SFO_REQUEST)
    assert response.status_code == 200
    payload = response.json()
    assert UUID(response.headers["X-Request-ID"]) == UUID(payload["request_id"])
    assert payload["status"] == "ok"
    assert payload["scope"]["airports"] == ["SFO"]
    assert payload["scope"]["metric"] == "sfo_enplaned_trend"
    assert payload["series"] == [
        {"period": point.period, "value": point.passengers, "unit": "count", "status": "ok"}
        for point in expected.series
    ]
    assert len(payload["series"]) == 24
    assert payload["rows"][0]["metrics"][0]["value"] == expected.annual_totals[1].passengers
    assert payload["sources"][0]["snapshot_id"] == expected.source.snapshot_id
    assert payload["sources"][0]["url"] == expected.source.url
    assert payload["sources"][0]["retrieved_at"] is not None
    assert datetime.fromisoformat(payload["sources"][0]["retrieved_at"].replace("Z", "+00:00")) == expected.source.retrieved_at
    assert "unmet demand or its cause" in payload["limitations"][0]
    encoded = response.text.lower()
    assert "backend/data" not in encoded
    assert "parquet" not in encoded


def test_query_errors_are_non_2xx_safe_envelopes_with_server_request_ids(monkeypatch):
    def should_not_dispatch(*_args, **_kwargs):
        raise AssertionError("invalid scope must not reach the dispatcher")

    monkeypatch.setattr(main, "dispatch_analysis", should_not_dispatch)
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "metric", "airports": ["BOS"], "metric": "sfo_pressure", "year": 2024}},
    )
    assert response.status_code == 422
    payload = response.json()
    assert set(payload) == {"success", "error"}
    assert payload["success"] is False
    assert payload["error"]["code"] == "invalid_request"
    assert UUID(response.headers["X-Request-ID"]) == UUID(payload["error"]["request_id"])
    assert "AssertionError" not in response.text


@pytest.mark.parametrize("action,airports", [("metric", ["BOS"]), ("compare", ["BOS", "PVD"])])
def test_screen_score_non_rank_requests_are_rejected_before_dispatch(monkeypatch, action, airports):
    def should_not_dispatch(*_args, **_kwargs):
        raise AssertionError("invalid scope must not reach the dispatcher")

    monkeypatch.setattr(main, "dispatch_analysis", should_not_dispatch)
    response = client.post("/api/query", json={"analysis": {
        "action": action, "airports": airports, "metric": "screen_score", "year": 2024,
    }})
    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "invalid_request"
    assert UUID(response.headers["X-Request-ID"]) == UUID(payload["error"]["request_id"])


@pytest.mark.parametrize("metric", ["seat_occupancy", "long_haul_share"])
def test_unavailable_pvc_ratio_returns_insufficient_data_and_mixed_compare_is_partial(metric):
    unavailable = client.post("/api/query", json={"analysis": {
        "action": "metric", "airports": ["PVC"], "metric": metric, "year": 2024,
    }})
    assert unavailable.status_code == 422
    assert unavailable.json()["error"]["code"] == "insufficient_data"

    mixed = client.post("/api/query", json={"analysis": {
        "action": "compare", "airports": ["BOS", "PVC"], "metric": metric, "year": 2024,
    }})
    assert mixed.status_code == 200, mixed.text
    payload = mixed.json()
    assert payload["status"] == "partial"
    rows = {row["airport"]: row["metrics"][0] for row in payload["rows"]}
    assert rows["BOS"]["status"] == "ok"
    assert rows["BOS"]["numerator"] is not None
    assert rows["BOS"]["denominator"] is not None
    assert rows["PVC"]["status"] == "unavailable"
    assert rows["PVC"]["value"] is None
    assert rows["PVC"]["numerator"] is None
    assert rows["PVC"]["denominator"] is None


def test_bad_json_content_type_and_oversized_body_are_rejected_before_parsing():
    wrong_media = client.post("/api/query", content=json.dumps(SFO_REQUEST), headers={"content-type": "text/plain"})
    assert wrong_media.status_code == 415
    assert wrong_media.json()["error"]["code"] == "unsupported_media_type"

    invalid_json = client.post("/api/query", content=b"{bad", headers={"content-type": "application/json"})
    assert invalid_json.status_code == 400
    assert invalid_json.json()["error"]["code"] == "invalid_json"

    async def oversized_stream():
        async def chunks():
            yield b"{\"analysis\":"
            yield b" " * (MAX_REQUEST_BYTES + 1)
            yield b"}"

        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            return await async_client.post(
                "/api/query", content=chunks(), headers={"content-type": "application/json"},
            )

    oversized = asyncio.run(oversized_stream())
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "request_too_large"


def test_unavailable_snapshot_returns_safe_data_unavailable_error(monkeypatch):
    def unavailable(_root=None):
        raise SFOTrendError("/private/local/backend/data/raw/path must not reach the client")

    monkeypatch.setattr(dispatch, "calculate_sfo_enplaned_trend", unavailable)
    response = client.post("/api/query", json=SFO_REQUEST)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "data_unavailable"
    assert "/private" not in response.text
    assert "backend/data" not in response.text


def test_unexpected_failure_is_sanitized(monkeypatch):
    def crash(*_args, **_kwargs):
        raise RuntimeError("internal stack with /private/backend/data/path")

    monkeypatch.setattr(main, "dispatch_analysis", crash)
    response = client.post("/api/query", json=SFO_REQUEST)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "/private" not in response.text


def test_free_text_is_model_disabled_and_does_not_replace_last_result(monkeypatch):
    result = client.post("/api/query", json=SFO_REQUEST).json()
    token = client.cookies.get(main.CONTEXT_COOKIE)

    def no_dispatch(*_args, **_kwargs):
        raise AssertionError("free text must not call the deterministic dispatcher or a model")

    monkeypatch.setattr(main, "dispatch_analysis", no_dispatch)
    response = client.post("/api/query", json={"message": "Compare some airports."})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"
    assert client.cookies.get(main.CONTEXT_COOKIE) == token
    assert context_claims(client).result_id == UUID(result["result_id"])


def test_admitted_free_text_dispatches_validated_analysis_and_stores_request(monkeypatch):
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    calls = []

    async def interpret(message, *, settings, context):
        calls.append((message, context))
        analysis = main.AnalysisRequest(action="metric", airports=["SFO"], metric="sfo_enplaned_trend", year=2024)
        return ModelInterpretation("analysis", analysis, None, ModelUsage(100, 20))

    monkeypatch.setattr(main, "interpret_message", interpret)
    response = client.post("/api/query", json={"message": "How did SFO enplanements change?"})
    assert response.status_code == 200, response.text
    assert len(calls) == 1 and calls[0][1] is None
    claims = context_claims(client)
    assert claims.result_id == UUID(response.json()["result_id"])
    assert claims.request.metric == "sfo_enplaned_trend"


def test_each_model_call_logs_metadata_without_question_text(monkeypatch, caplog):
    monkeypatch.setattr(main, "load_settings", admitted_settings)

    async def interpret(_message, *, settings, context):
        analysis = main.AnalysisRequest(action="metric", airports=["SFO"], metric="sfo_enplaned_trend", year=2024)
        return ModelInterpretation("analysis", analysis, None, ModelUsage(123, 45))

    async def failing(*_args, **_kwargs):
        raise ModelAdapterError("model_refusal")

    caplog.set_level("INFO", logger="app.main")
    monkeypatch.setattr(main, "interpret_message", interpret)
    assert client.post("/api/query", json={"message": "secret-question-text SFO trend"}).status_code == 200
    monkeypatch.setattr(main, "interpret_message", failing)
    assert client.post("/api/query", json={"message": "secret-question-text again"}).status_code == 503
    lines = [record.getMessage() for record in caplog.records if record.getMessage().startswith("model call")]
    assert len(lines) == 2
    assert "model=fake-model outcome=analysis follow_up=False" in lines[0]
    assert "input_tokens=123 output_tokens=45" in lines[0]
    assert "outcome=model_refusal" in lines[1]
    assert all("secret-question-text" not in record.getMessage() for record in caplog.records)


def test_independent_question_after_prior_result_has_no_context(monkeypatch):
    first = client.post("/api/query", json=SFO_REQUEST)
    assert first.status_code == 200
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    contexts = []

    async def interpret(_message, *, settings, context):
        contexts.append(context)
        analysis = main.AnalysisRequest(action="metric", airports=["PVD"], metric="passengers", year=2024)
        return ModelInterpretation("analysis", analysis, None, ModelUsage(100, 20))

    monkeypatch.setattr(main, "interpret_message", interpret)
    second = client.post("/api/query", json={"message": "How many PVD passengers in 2024?"})
    assert second.status_code == 200, second.text
    assert contexts == [None]
    assert second.json()["scope"]["airports"] == ["PVD"]


def test_old_prompt_hash_cannot_admit_changed_adapter(monkeypatch):
    from app.model_adapter import PROMPT_SHA256

    assert PROMPT_SHA256 != main.ADAPTER_SHA256
    monkeypatch.setattr(main, "load_settings", lambda: admitted_settings(model_admitted_adapter_sha256=PROMPT_SHA256))

    async def fail(*_args, **_kwargs):
        raise AssertionError("unadmitted prompt reached model")

    monkeypatch.setattr(main, "interpret_message", fail)
    response = client.post("/api/query", json={"message": "How many PVD passengers in 2024?"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"


def test_followup_passes_only_signed_request_and_stale_id_keeps_cookie(monkeypatch):
    first = client.post("/api/query", json={"analysis": {
        "action": "rank", "region": "new_england", "metric": "passengers", "year": 2024,
    }})
    assert first.status_code == 200
    first_id = first.json()["result_id"]
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    contexts = []

    async def interpret(_message, *, settings, context):
        contexts.append(context)
        analysis = main.AnalysisRequest.model_validate({**context, "metric": "passenger_growth"})
        return ModelInterpretation("analysis", analysis, None, ModelUsage(100, 20))

    monkeypatch.setattr(main, "interpret_message", interpret)
    second = client.post("/api/query", json={"message": "Show growth instead", "context_result_id": first_id})
    assert second.status_code == 200, second.text
    assert second.json()["scope"]["metric"] == "passenger_growth"
    assert contexts == [{"action": "rank", "region": "new_england", "metric": "passengers", "year": 2024}]
    token = client.cookies.get(main.CONTEXT_COOKIE)
    stale = client.post("/api/query", json={"message": "And again", "context_result_id": first_id})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "result_mismatch"
    assert client.cookies.get(main.CONTEXT_COOKIE) == token
    assert len(contexts) == 1
    assert context_claims(client).result_id == UUID(second.json()["result_id"])


def test_foreign_context_never_calls_model(monkeypatch):
    first = client.post("/api/query", json=SFO_REQUEST)
    monkeypatch.setattr(main, "load_settings", admitted_settings)

    async def fail(*_args, **_kwargs):
        raise AssertionError("foreign context reached model")

    monkeypatch.setattr(main, "interpret_message", fail)
    foreign = TestClient(main.app)
    response = foreign.post("/api/query", json={"message": "Show more", "context_result_id": first.json()["result_id"]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_expired"


@pytest.mark.parametrize("cookie", ["", "garbage", "v1.e30.AAAA"])
def test_invalid_context_cookie_fails_before_model_and_is_cleared(monkeypatch, cookie):
    first = client.post("/api/query", json=SFO_REQUEST)
    result_id = first.json()["result_id"]
    monkeypatch.setattr(main, "load_settings", admitted_settings)

    async def fail(*_args, **_kwargs):
        raise AssertionError("invalid context reached model")

    monkeypatch.setattr(main, "interpret_message", fail)
    monkeypatch.setattr(main, "dispatch_analysis", fail)
    other = TestClient(main.app)
    other.cookies.set(main.CONTEXT_COOKIE, cookie, domain="testserver.local")
    for body in ({"message": "Show more", "context_result_id": result_id},
                 {"analysis": {"action": "explain"}, "context_result_id": result_id}):
        response = other.post("/api/query", json=body)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "session_expired"
        assert main.CONTEXT_COOKIE in response.headers["set-cookie"]
        assert "max-age=0" in response.headers["set-cookie"].lower()


def test_no_server_side_session_state_remains():
    import importlib.util

    assert importlib.util.find_spec("app.session") is None
    assert importlib.util.find_spec("app.model_budget") is None
    assert not hasattr(main, "session_store")
    assert not hasattr(main, "model_budget")
    mutable = {
        name for name, value in vars(main).items()
        if not name.startswith("__") and isinstance(value, (dict, list, set))
    }
    assert mutable == set()


def test_message_without_context_ignores_bad_cookie(monkeypatch):
    monkeypatch.setattr(main, "load_settings", admitted_settings)

    async def interpret(_message, *, settings, context):
        assert context is None
        analysis = main.AnalysisRequest(action="metric", airports=["PVD"], metric="passengers", year=2024)
        return ModelInterpretation("analysis", analysis, None, ModelUsage(10, 5))

    monkeypatch.setattr(main, "interpret_message", interpret)
    other = TestClient(main.app)
    other.cookies.set(main.CONTEXT_COOKIE, "garbage", domain="testserver.local")
    response = other.post("/api/query", json={"message": "PVD passengers in 2024"})
    assert response.status_code == 200, response.text
    assert context_claims(other).result_id == UUID(response.json()["result_id"])


def test_tampered_context_cookie_is_rejected():
    first = client.post("/api/query", json=SFO_REQUEST)
    token = client.cookies.get(main.CONTEXT_COOKIE)
    version, payload, mac = token.split(".")
    forged = TestClient(main.app)
    forged.cookies.set(main.CONTEXT_COOKIE, f"{version}.{payload}.{'B' if mac[0] == 'A' else 'A'}{mac[1:]}", domain="testserver.local")
    response = forged.post("/api/query", json={
        "analysis": {"action": "explain"}, "context_result_id": first.json()["result_id"],
    })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_expired"


def test_independent_request_ignores_bad_cookie_and_issues_fresh_token():
    other = TestClient(main.app)
    other.cookies.set(main.CONTEXT_COOKIE, "garbage", domain="testserver.local")
    response = other.post("/api/query", json=SFO_REQUEST)
    assert response.status_code == 200, response.text
    assert context_claims(other).result_id == UUID(response.json()["result_id"])


def test_explain_detects_digest_drift_as_result_mismatch(monkeypatch):
    first = client.post("/api/query", json=SFO_REQUEST)
    assert first.status_code == 200
    real_dispatch = main.dispatch_analysis

    def drifted(analysis, request_id, *, previous=None):
        result = real_dispatch(analysis, request_id, previous=previous)
        if analysis.action == "explain":
            return result
        return result.model_copy(update={"summary": "source data changed"})

    monkeypatch.setattr(main, "dispatch_analysis", drifted)
    response = client.post("/api/query", json={
        "analysis": {"action": "explain"}, "context_result_id": first.json()["result_id"],
    })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "result_mismatch"
    assert "reproduced" in response.json()["error"]["message"]


def test_explain_via_cookie_on_fresh_verifier_with_same_key(monkeypatch):
    """A second instance holding only the signing key can serve the follow-up."""
    monkeypatch.setattr(main, "_LOCAL_SIGNING_KEY", b"s" * 32)
    first = client.post("/api/query", json=SFO_REQUEST)
    token = client.cookies.get(main.CONTEXT_COOKIE)
    assert main.ContextSigner(b"s" * 32).verify(token).result_id == UUID(first.json()["result_id"])
    monkeypatch.setattr(main, "_LOCAL_SIGNING_KEY", b"t" * 32)
    rejected = client.post("/api/query", json={
        "analysis": {"action": "explain"}, "context_result_id": first.json()["result_id"],
    })
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "session_expired"


def test_free_text_explain_keeps_latest_result(monkeypatch):
    first = client.post("/api/query", json=SFO_REQUEST)
    result_id = UUID(first.json()["result_id"])
    token = client.cookies.get(main.CONTEXT_COOKIE)
    monkeypatch.setattr(main, "load_settings", admitted_settings)

    async def interpret(_message, *, settings, context):
        assert context["metric"] == "sfo_enplaned_trend"
        return ModelInterpretation("analysis", main.AnalysisRequest(action="explain"), None, ModelUsage(100, 20))

    monkeypatch.setattr(main, "interpret_message", interpret)
    response = client.post("/api/query", json={"message": "Explain that", "context_result_id": str(result_id)})
    assert response.status_code == 200
    assert response.json()["result_id"] == str(result_id)
    assert response.json()["rows"] == first.json()["rows"]
    assert client.cookies.get(main.CONTEXT_COOKIE) == token


@pytest.mark.parametrize("kind,expected", [
    ("clarification_required", "clarification_required"),
    ("unsupported_scope", "unsupported_scope"),
])
def test_model_safe_outcome_is_422_and_preserves_prior_result(monkeypatch, kind, expected):
    first = client.post("/api/query", json=SFO_REQUEST)
    monkeypatch.setattr(main, "load_settings", admitted_settings)

    async def interpret(_message, *, settings, context):
        return ModelInterpretation(kind, None, "provider-controlled prose", ModelUsage(100, 20))

    monkeypatch.setattr(main, "interpret_message", interpret)
    response = client.post("/api/query", json={"message": "Something ambiguous"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == expected
    assert "provider-controlled" not in response.text
    assert context_claims(client).result_id == UUID(first.json()["result_id"])


def test_model_timeout_preserves_prior_result(monkeypatch):
    first = client.post("/api/query", json=SFO_REQUEST)
    called = []

    async def interpret(*_args, **_kwargs):
        called.append(True)
        raise ModelAdapterError("model_timeout")

    monkeypatch.setattr(main, "interpret_message", interpret)
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    timed_out = client.post("/api/query", json={"message": "SFO trend?"})
    assert timed_out.status_code == 504
    assert timed_out.json()["error"]["code"] == "query_timeout"
    assert len(called) == 1
    assert context_claims(client).result_id == UUID(first.json()["result_id"])


def test_structured_preset_never_calls_model_even_when_admitted(monkeypatch):
    monkeypatch.setattr(main, "load_settings", admitted_settings)

    async def fail(*_args, **_kwargs):
        raise AssertionError("preset called model")

    monkeypatch.setattr(main, "interpret_message", fail)
    response = client.post("/api/query", json=SFO_REQUEST)
    assert response.status_code == 200


def test_cookie_is_signed_http_only_and_explain_does_not_replace_it():
    first = client.post("/api/query", json=SFO_REQUEST)
    assert first.status_code == 200
    original = first.json()
    token = client.cookies.get(main.CONTEXT_COOKIE)
    assert token and token.startswith("v1.")
    set_cookie = first.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "max-age=3600" in set_cookie
    assert "secure" not in set_cookie

    explain = client.post(
        "/api/query",
        json={"analysis": {"action": "explain"}, "context_result_id": original["result_id"]},
    )
    assert explain.status_code == 200
    assert "set-cookie" not in explain.headers
    explained = explain.json()
    assert explained["result_id"] == original["result_id"]
    assert explained["request_id"] != original["request_id"]
    assert explained["scope"] == original["scope"]
    assert explained["rows"] == original["rows"]
    assert explained["summary"] != original["summary"]
    assert client.cookies.get(main.CONTEXT_COOKIE) == token


def test_saved_historical_explain_recomputes_pinned_period_without_model(monkeypatch):
    first = client.post(
        "/api/query",
        json={"analysis": {
            "action": "metric", "airports": ["PVD"], "metric": "passengers", "year": 2024,
        }},
    )
    assert first.status_code == 200, first.text
    original = first.json()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("explanation must not call a model or reopen the bundle registry")

    monkeypatch.setattr(dispatch, "_read_accepted_bundle", forbidden)
    monkeypatch.setattr(main, "interpret_message", forbidden)
    explained = client.post(
        "/api/query",
        json={
            "analysis": {"action": "explain"},
            "context_result_id": original["result_id"],
        },
    )
    assert explained.status_code == 200, explained.text
    payload = explained.json()
    assert payload["scope"] == original["scope"]
    assert payload["rows"] == original["rows"]
    assert payload["sources"] == original["sources"]
    assert payload["evidence"] == original["evidence"]


def test_context_reference_is_cookie_bound_and_stale_ids_conflict():
    first = client.post("/api/query", json=SFO_REQUEST)
    result_id = first.json()["result_id"]
    foreign = TestClient(main.app)
    mismatch = foreign.post(
        "/api/query", json={"analysis": {"action": "explain"}, "context_result_id": result_id},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "session_expired"

    second = client.post("/api/query", json=SFO_REQUEST).json()
    stale = client.post(
        "/api/query", json={"analysis": {"action": "explain"}, "context_result_id": result_id},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "result_mismatch"
    assert context_claims(client).result_id == UUID(second["result_id"])


def test_real_t100_metric_route_returns_typed_source_lineage():
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "metric", "airports": ["PVD"], "metric": "passengers", "year": 2024}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scope"]["metric"] == "passengers"
    assert payload["rows"][0]["metrics"][0]["status"] == "ok"
    assert payload["sources"][0]["snapshot_id"].startswith("t100-")
    assert payload["sources"][0]["retrieved_at"] is None


@pytest.mark.parametrize("metric,metric_key", [("passengers", "passengers"), ("seat_occupancy", "seat_occupancy")])
def test_2023_rank_levels_use_requested_year_values_and_scope(metric, metric_key):
    cohort = sorted(dispatch.NEW_ENGLAND_AIRPORTS)
    traffic = calculate_traffic_batch(cohort)
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "rank", "region": "new_england", "metric": metric, "year": 2023}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["scope"]["year"] == 2023
    assert payload["scope"]["metric"] == metric
    assert "Raw BTS T-100" in payload["scope"]["population"]
    assert "CY2023" in payload["scope"]["population"]
    assert "rank positions against the full eligible" in payload["scope"]["population"]
    assert "normalized" not in payload["scope"]["population"]
    assert "in 2023" in payload["summary"]
    assert "full eligible cohort" in payload["summary"]
    assert "screen scores" not in payload["summary"]

    index = 0  # TrafficResult.annual is ordered 2023, 2024.
    expected = {
        airport: (traffic[airport].annual[index].passengers.value if metric == "passengers"
                  else traffic[airport].annual[index].occupancy_percent.value)
        for airport in cohort
        if (traffic[airport].annual[index].passengers.status == "ok" if metric == "passengers"
            else traffic[airport].annual[index].occupancy_percent.status == "ok")
    }
    rows = payload["rows"]
    observed = {row["airport"]: next(m for m in row["metrics"] if m["key"] == metric_key) for row in rows}
    assert set(observed) == set(expected)
    if "PVC" in expected:
        assert "PVC" in observed
        assert all(not item.startswith("PVC:") for item in payload["exclusions"])
    assert all([metric["key"] for metric in row["metrics"]] == [metric_key] for row in rows)
    assert [row["airport"] for row in rows] == sorted(expected, key=lambda airport: (-expected[airport], airport))
    for airport, expected_value in expected.items():
        assert observed[airport]["value"] == pytest.approx(expected_value)
        assert observed[airport]["status"] == "ok"
    if metric == "seat_occupancy":
        for airport, metric_value in observed.items():
            annual = traffic[airport].annual[index]
            assert metric_value["numerator"] == annual.passengers.value
            assert metric_value["denominator"] == annual.seats.value


@pytest.mark.parametrize("metric", ["screen_score", "passengers", "seat_occupancy"])
def test_rank_single_airport_with_no_2024_metric_returns_insufficient_data(metric):
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "rank", "airports": ["PVC"], "metric": metric, "year": 2024}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_data"
    assert response.json()["success"] is False


def test_growth_ranking_positions_use_raw_growth_not_composite_screen_rank():
    cohort = sorted(dispatch.NEW_ENGLAND_AIRPORTS)
    traffic = calculate_traffic_batch(cohort)
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "rank", "region": "new_england", "metric": "passenger_growth", "year": 2024}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "raw growth" in payload["summary"]
    assert "Raw 2024 versus 2023 passenger growth" in payload["scope"]["population"]
    expected_growth = {
        airport: traffic[airport].growth.percent
        for airport in cohort if traffic[airport].growth.status == "ok"
    }
    rows = payload["rows"]
    assert [row["airport"] for row in rows] == sorted(expected_growth, key=lambda airport: (-expected_growth[airport], airport))
    for row in rows:
        metric = next(item for item in row["metrics"] if item["key"] == "passenger_growth")
        assert metric["value"] == pytest.approx(expected_growth[row["airport"]])
        assert row["rank"] == 1 + sum(value > expected_growth[row["airport"]]
                                      for value in expected_growth.values())
    growth_by_airport = {row["airport"]: row for row in rows}
    bos_growth = next(item["value"] for item in growth_by_airport["BOS"]["metrics"] if item["key"] == "passenger_growth")
    pvd_growth = next(item["value"] for item in growth_by_airport["PVD"]["metrics"] if item["key"] == "passenger_growth")
    assert bos_growth == pytest.approx(6.58, abs=0.01)
    assert pvd_growth == pytest.approx(14.55, abs=0.01)
    assert growth_by_airport["PVD"]["rank"] < growth_by_airport["BOS"]["rank"]


def test_screen_score_rank_still_reports_normalized_population():
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "rank", "region": "new_england", "metric": "screen_score", "year": 2024}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "normalized against the frozen eligible 2024 cohort" in payload["scope"]["population"]
    assert "scores are normalized" in payload["summary"]


@pytest.mark.parametrize("metric", ["passengers", "seat_occupancy", "passenger_growth"])
def test_metric_rank_eligibility_and_exclusions_ignore_composite_screen(monkeypatch, metric):
    cohort = sorted(dispatch.NEW_ENGLAND_AIRPORTS)
    traffic = calculate_traffic_batch(cohort)

    def reject_composite_scope(_traffic, **_kwargs):
        return ScreenResult(
            status="insufficient_data", reference_cohort=(), rows=(),
            exclusions=(ScreenExclusion("BOS", "composite-only input unavailable"),),
            reason="synthetic composite screen exclusion", sort_by="screen_score",
        )

    monkeypatch.setattr(dispatch, "calculate_screen", reject_composite_scope)
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "rank", "region": "new_england", "metric": metric, "year": 2024}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "BOS" in {row["airport"] for row in payload["rows"]}
    assert all(not item.startswith("BOS:") for item in payload["exclusions"])
    if metric == "passenger_growth":
        expected_exclusions = [f"{airport}: {traffic[airport].growth.reason}"
                               for airport in sorted(cohort) if traffic[airport].growth.status != "ok"]
    else:
        expected_exclusions = []
        for airport in sorted(cohort):
            annual = traffic[airport].annual[1]
            metric_result = annual.passengers if metric == "passengers" else annual.occupancy_percent
            if metric_result.status != "ok":
                expected_exclusions.append(f"{airport}: {metric_result.reason}")
    assert payload["exclusions"] == expected_exclusions


@pytest.mark.parametrize("metric", ["passengers", "seat_occupancy"])
def test_2023_raw_rank_exclusions_follow_requested_metric_not_screen(monkeypatch, metric):
    cohort = sorted(dispatch.NEW_ENGLAND_AIRPORTS)
    traffic = calculate_traffic_batch(cohort)
    original = traffic["BGR"]
    annual_2023 = original.annual[0]
    reason = f"fixture missing requested-year {metric}"
    unavailable = MetricResult(None, "unavailable", reason)
    if metric == "passengers":
        annual_2023 = replace(annual_2023, passengers=unavailable)
    else:
        annual_2023 = replace(annual_2023, occupancy_percent=unavailable)
    traffic["BGR"] = replace(original, annual=(annual_2023, original.annual[1]))

    def composite_excludes_pvc(_traffic, **_kwargs):
        return ScreenResult(
            status="insufficient_data", reference_cohort=(), rows=(),
            exclusions=(ScreenExclusion("PVC", "2024 December absent from composite scope"),),
            reason="screen-only exclusion", sort_by="screen_score",
        )

    monkeypatch.setattr(dispatch, "calculate_traffic_batch", lambda _airports: traffic)
    monkeypatch.setattr(dispatch, "calculate_screen", composite_excludes_pvc)
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "rank", "region": "new_england", "metric": metric, "year": 2023}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["exclusions"] == [f"BGR: {reason}"]
    ranked = {row["airport"] for row in payload["rows"]}
    assert "BGR" not in ranked
    assert "PVC" in ranked


def test_real_operations_congestion_compare_uses_accepted_snapshot():
    expected = [calculate_operations(airport) for airport in ("LAX", "SNA")]
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "compare", "airports": ["LAX", "SNA"], "metric": "congestion", "year": 2024}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["rows"]) == 2
    assert len(payload["rows"][0]["metrics"]) == 4
    assert payload["sources"][0]["snapshot_id"] == expected[0].source.snapshot_id
    assert payload["sources"][0]["snapshot_id"] == expected[1].source.snapshot_id
    assert payload["sources"][0]["retrieved_at"] is None
    assert "comparable operational" in payload["summary"]
    directions = set()
    for left, right in zip(payload["rows"][0]["metrics"], payload["rows"][1]["metrics"]):
        directions.add(left["comparison_direction"])
        assert left["eligible_count"] is not None
        assert right["eligible_count"] is not None
        assert left["denominator"] is not None
        assert right["denominator"] is not None
        reverse = {"higher": "lower", "lower": "higher", "tied": "tied", "unavailable": "unavailable"}
        assert right["comparison_direction"] == reverse[left["comparison_direction"]]
    assert directions & {"higher", "lower", "tied"}
    assert any("reporting carriers" in item and "observed months" in item for item in payload["limitations"])


def test_mixed_congestion_summary_states_each_airports_count():
    rows = [
        {"airport": "LAX", "metrics": [{"value": 2}, {"value": 1}, {"value": None}, {"value": 5}]},
        {"airport": "SNA", "metrics": [{"value": 1}, {"value": 3}, {"value": 4}, {"value": 5}]},
    ]
    summary = dispatch._comparison_summary("congestion", rows)
    assert summary == (
        "Mixed picture: LAX is higher on 1 and SNA on 1 of 3 comparable operational-strain indicators."
    )
    assert "favor" not in summary


@pytest.mark.parametrize("year", [2024, 2025])
def test_same_on_time_snapshot_has_one_source_id_across_workflows(year):
    operations = client.post("/api/query", json={"analysis": {
        "action": "compare", "airports": ["LAX", "SNA"], "metric": "congestion", "year": year,
    }})
    pressure = client.post("/api/query", json={"analysis": {
        "action": "metric", "airports": ["SFO"], "metric": "sfo_pressure", "year": year,
    }})
    assert operations.status_code == pressure.status_code == 200
    by_snapshot = {}
    for payload in (operations.json(), pressure.json()):
        for source in payload["sources"]:
            by_snapshot.setdefault(source["snapshot_id"], set()).add(source["id"])
    ontime = {snapshot: ids for snapshot, ids in by_snapshot.items() if snapshot.startswith("ontime-")}
    assert ontime
    assert all(len(ids) == 1 and next(iter(ids)).startswith("ontime-") for ids in ontime.values())


def test_real_sfo_pressure_route_keeps_populations_and_not_identifiable_boundary():
    expected_operations = calculate_operations("SFO")
    response = client.post(
        "/api/query",
        json={"analysis": {"action": "metric", "airports": ["SFO"], "metric": "sfo_pressure", "year": 2024}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] in {"ok", "partial"}
    assert len(payload["series"]) == 24
    assert {source["snapshot_id"] for source in payload["sources"]} >= {
        "datasf-28fd4041701d874e6ad2cb134615e2e2ced826ddce40bece5b938c0a0b7250cf",
        "t100-09666a46b4108e6393c72af9423ac17913ca99206e75c0bc336d717355c318ac",
        expected_operations.source.snapshot_id,
    }
    assert any("not_identifiable" in item for item in payload["limitations"])


def test_timeout_keeps_previous_result_and_holds_busy_slot_until_worker_finishes(monkeypatch):
    work_gate = threading.Event()

    def slow(_analysis, request_id, *, previous=None):
        work_gate.wait(timeout=5)
        raise DispatchFailure("data_unavailable", 503, "late result")

    async def run_same_loop_requests():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            original_response = await async_client.post("/api/query", json=SFO_REQUEST)
            original = original_response.json()
            monkeypatch.setattr(main, "QUERY_DEADLINE_SECONDS", 0.01)
            monkeypatch.setattr(main, "dispatch_analysis", slow)
            timed_out = await async_client.post(
                "/api/query", json={"analysis": {"action": "metric", "airports": ["PVD"], "metric": "passengers", "year": 2024}},
            )
            assert timed_out.status_code == 504
            assert timed_out.json()["error"]["code"] == "query_timeout"
            assert context_claims(async_client).result_id == UUID(original["result_id"])
            busy = await async_client.post(
                "/api/query", json={"analysis": {"action": "metric", "airports": ["PVD"], "metric": "passengers", "year": 2024}},
            )
            assert busy.status_code == 409
            assert busy.json()["error"]["code"] == "busy"

    try:
        asyncio.run(run_same_loop_requests())
    finally:
        work_gate.set()
    deadline = time.monotonic() + 2
    while main.query_slots.active and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert main.query_slots.active == 0


def test_cancelled_request_keeps_busy_slot_until_worker_finishes_without_saving_late_result(monkeypatch):
    worker_started = threading.Event()
    work_gate = threading.Event()

    async def run_cancelled_request():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            original_response = await async_client.post("/api/query", json=SFO_REQUEST)
            assert original_response.status_code == 200
            original = main.AnalysisResult.model_validate_json(original_response.text)

            def slow(_analysis, request_id, *, previous=None):
                worker_started.set()
                work_gate.wait(timeout=5)
                return original.model_copy(update={
                    "result_id": uuid4(), "request_id": request_id, "summary": "late background result",
                })

            monkeypatch.setattr(main, "dispatch_analysis", slow)
            pending = asyncio.create_task(async_client.post(
                "/api/query",
                json={"analysis": {"action": "metric", "airports": ["PVD"], "metric": "passengers", "year": 2024}},
            ))
            assert await asyncio.to_thread(worker_started.wait, 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending

            assert main.query_slots.active == 1
            assert context_claims(async_client).result_id == original.result_id
            busy = await async_client.post(
                "/api/query",
                json={"analysis": {"action": "metric", "airports": ["PVD"], "metric": "passengers", "year": 2024}},
            )
            assert busy.status_code == 409
            assert busy.json()["error"]["code"] == "busy"

            work_gate.set()
            deadline = time.monotonic() + 2
            while main.query_slots.active and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert main.query_slots.active == 0
            assert context_claims(async_client).result_id == original.result_id

    try:
        asyncio.run(run_cancelled_request())
    finally:
        work_gate.set()


@pytest.mark.parametrize("year", [2024, 2025])
def test_sfo_pressure_returns_datasf_enplaned_growth(year):
    payload = client.post("/api/query", json={"analysis": {
        "action": "metric", "airports": ["SFO"], "metric": "sfo_pressure", "year": year,
    }}).json()
    metrics = {metric["key"]: metric for metric in payload["rows"][0]["metrics"]}
    growth = metrics["enplaned_growth"]
    datasf_ids = {source["id"] for source in payload["sources"] if source["snapshot_id"].startswith("datasf-")}
    series = {point["period"]: point["value"] for point in payload["series"]}
    baseline = sum(value for period, value in series.items() if period.startswith(str(year - 1)))
    comparison = sum(value for period, value in series.items() if period.startswith(str(year)))

    assert growth["unit"] == "percent" and growth["status"] == "ok"
    assert growth["source_ids"] == metrics["sfo_enplaned_trend"]["source_ids"]
    assert set(growth["source_ids"]) <= datasf_ids
    assert (growth["numerator"], growth["denominator"]) == (comparison - baseline, baseline)
    assert growth["value"] == pytest.approx((comparison - baseline) / baseline * 100)
    assert metrics["sfo_enplaned_trend"]["value"] == comparison
