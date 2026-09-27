from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from app.contracts import AnalysisRequest, AnalysisResult
from app.session import SessionError, SessionStore


def _result() -> AnalysisResult:
    return AnalysisResult.model_validate(
        {
            "result_id": uuid4(), "request_id": uuid4(), "status": "ok",
            "scope": {"airports": ["PVD"], "year": 2024, "metric": "passengers", "population": "test"},
            "rows": [], "summary": "test", "series": [], "sources": [], "evidence": [],
            "exclusions": [], "limitations": [],
        }
    )


def _rank_request() -> AnalysisRequest:
    return AnalysisRequest.model_validate({
        "action": "rank", "region": "new_england", "metric": "screen_score", "year": 2024,
    })


def test_tokens_are_opaque_and_sessions_are_isolated():
    store = SessionStore()
    one, two = store.create(), store.create()
    result = _result()
    store.save_success(one, result)

    assert len(one) >= 40
    assert store.latest(one).result_id == result.result_id
    assert store.latest(two) is None
    with pytest.raises(SessionError, match="current"):
        store.validate_result(two, result.result_id)


def test_latest_result_binding_rejects_stale_and_foreign_ids():
    store = SessionStore()
    token = store.create()
    original, newer = _result(), _result()
    store.save_success(token, original)
    store.save_success(token, newer)

    with pytest.raises(SessionError) as stale:
        store.validate_result(token, original.result_id)
    assert stale.value.code == "result_mismatch"
    assert store.validate_result(token, newer.result_id).result_id == newer.result_id


def test_expired_session_is_removed_and_requires_fresh_analysis():
    now = [10.0]
    store = SessionStore(idle_seconds=3600, clock=lambda: now[0])
    token = store.create()
    store.save_success(token, _result())
    now[0] += 3600

    assert not store.lookup(token)
    with pytest.raises(SessionError) as expired:
        store.latest(token)
    assert expired.value.code == "session_expired"


def test_capacity_refuses_new_session_without_evicting_active_context():
    store = SessionStore(capacity=1)
    token = store.create()
    result = _result()
    store.save_success(token, result)

    with pytest.raises(SessionError) as capped:
        store.create()
    assert capped.value.code == "busy"
    assert store.latest(token).result_id == result.result_id


def test_one_process_wide_analysis_slot_is_exclusive():
    store = SessionStore()
    assert store.try_begin_query()
    assert not store.try_begin_query()
    store.end_query()
    assert store.try_begin_query()


def test_returned_results_are_copies_and_failed_work_does_not_overwrite():
    store = SessionStore()
    token = store.create()
    result = _result()
    store.save_success(token, result)
    copy = store.latest(token)
    copy.summary = "mutated caller copy"

    # An error path does not call save_success; the last good result is unchanged.
    assert store.latest(token).summary == "test"
    assert store.latest(token).result_id == result.result_id


def test_request_context_is_bound_to_latest_result_and_preserves_rank_region():
    store = SessionStore()
    token = store.create()
    result = _result()
    request = _rank_request()
    store.save_success(token, result, request)

    returned = store.validate_request_context(token, result.result_id)
    assert returned.region == "new_england"
    assert returned.airports is None
    assert returned.metric == "screen_score"
    assert returned.year == 2024
    assert store.validate_result(token, result.result_id).result_id == result.result_id

    # A later success replaces both values, including when an old route supplied no request.
    newer = _result()
    store.save_success(token, newer)
    with pytest.raises(SessionError) as stale:
        store.validate_request_context(token, result.result_id)
    assert stale.value.code == "result_mismatch"
    assert store.validate_request_context(token, newer.result_id) is None


def test_request_context_is_copy_isolated_and_failed_work_retains_previous():
    store = SessionStore()
    token = store.create()
    result = _result()
    request = AnalysisRequest.model_validate({
        "action": "rank", "airports": ["PVD", "BOS"], "metric": "passengers", "year": 2024,
    })
    store.save_success(token, result, request)
    request.airports.append("PWM")
    returned = store.validate_request_context(token, result.result_id)
    returned.airports.append("BDL")
    assert store.validate_request_context(token, result.result_id).airports == ["PVD", "BOS"]

    invalid_request = AnalysisRequest.model_construct(
        action="rank", region="new_england", metric="screen_score", year=2022,
    )
    with pytest.raises(ValidationError):
        store.save_success(token, _result(), invalid_request)
    # A failed validation cannot replace either side of the saved pair.
    assert store.validate_request_context(token, result.result_id).metric == "passengers"
    assert store.validate_result(token, result.result_id).result_id == result.result_id


def test_request_context_rejects_foreign_and_expired_result_references():
    now = [10.0]
    store = SessionStore(idle_seconds=3600, clock=lambda: now[0])
    owner, foreign = store.create(), store.create()
    result = _result()
    store.save_success(owner, result, _rank_request())

    with pytest.raises(SessionError) as mismatch:
        store.validate_request_context(foreign, result.result_id)
    assert mismatch.value.code == "result_mismatch"
    now[0] += 3600
    with pytest.raises(SessionError) as expired:
        store.validate_request_context(owner, result.result_id)
    assert expired.value.code == "session_expired"
