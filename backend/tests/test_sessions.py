from __future__ import annotations

from uuid import uuid4

import pytest
from app.contracts import AnalysisResult
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
