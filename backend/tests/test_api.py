import asyncio
import json
import threading
import time
from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

import httpx
import pytest
from app import dispatch, main
from app.calculations.operations import calculate_operations
from app.calculations.screen import ScreenExclusion, ScreenResult
from app.calculations.sfo import SFOTrendError, calculate_sfo_enplaned_trend
from app.calculations.traffic import MetricResult, calculate_traffic_batch
from app.contracts import MAX_REQUEST_BYTES
from app.dispatch import DispatchFailure
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
def reset_session_store():
    main.session_store.clear()
    yield
    main.session_store.clear()


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
    token = client.cookies.get(main.SESSION_COOKIE)

    def no_dispatch(*_args, **_kwargs):
        raise AssertionError("free text must not call the deterministic dispatcher or a model")

    monkeypatch.setattr(main, "dispatch_analysis", no_dispatch)
    response = client.post("/api/query", json={"message": "Compare some airports."})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_unavailable"
    assert main.session_store.latest(token).result_id == UUID(result["result_id"])


def test_cookie_is_opaque_and_explain_is_read_only():
    first = client.post("/api/query", json=SFO_REQUEST)
    assert first.status_code == 200
    original = first.json()
    token = client.cookies.get(main.SESSION_COOKIE)
    assert token and len(token) >= 40
    assert "httponly" in first.headers["set-cookie"].lower()
    assert "samesite=strict" in first.headers["set-cookie"].lower()

    explain = client.post(
        "/api/query",
        json={"analysis": {"action": "explain"}, "context_result_id": original["result_id"]},
    )
    assert explain.status_code == 200
    explained = explain.json()
    assert explained["result_id"] == original["result_id"]
    assert explained["request_id"] != original["request_id"]
    assert explained["scope"] == original["scope"]
    assert explained["rows"] == original["rows"]
    assert main.session_store.latest(token).summary == original["summary"]


def test_context_reference_is_session_bound_and_stale_ids_conflict():
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
    assert main.session_store.latest(client.cookies.get(main.SESSION_COOKIE)).result_id == UUID(second["result_id"])


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
            token = async_client.cookies.get(main.SESSION_COOKIE)
            monkeypatch.setattr(main, "QUERY_DEADLINE_SECONDS", 0.01)
            monkeypatch.setattr(main, "dispatch_analysis", slow)
            timed_out = await async_client.post(
                "/api/query", json={"analysis": {"action": "metric", "airports": ["PVD"], "metric": "passengers", "year": 2024}},
            )
            assert timed_out.status_code == 504
            assert timed_out.json()["error"]["code"] == "query_timeout"
            assert main.session_store.latest(token).result_id == UUID(original["result_id"])
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
    while main.session_store.query_busy and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert not main.session_store.query_busy


def test_cancelled_request_keeps_busy_slot_until_worker_finishes_without_saving_late_result(monkeypatch):
    worker_started = threading.Event()
    work_gate = threading.Event()

    async def run_cancelled_request():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            original_response = await async_client.post("/api/query", json=SFO_REQUEST)
            assert original_response.status_code == 200
            original = main.AnalysisResult.model_validate_json(original_response.text)
            token = async_client.cookies.get(main.SESSION_COOKIE)

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

            assert main.session_store.query_busy
            assert main.session_store.latest(token).result_id == original.result_id
            busy = await async_client.post(
                "/api/query",
                json={"analysis": {"action": "metric", "airports": ["PVD"], "metric": "passengers", "year": 2024}},
            )
            assert busy.status_code == 409
            assert busy.json()["error"]["code"] == "busy"

            work_gate.set()
            deadline = time.monotonic() + 2
            while main.session_store.query_busy and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert not main.session_store.query_busy
            assert main.session_store.latest(token).result_id == original.result_id

    try:
        asyncio.run(run_cancelled_request())
    finally:
        work_gate.set()
