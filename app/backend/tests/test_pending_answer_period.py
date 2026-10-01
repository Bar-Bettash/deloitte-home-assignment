"""A typed answer to "which measure?" resolves exactly like the measure button.

The button's period rule lives in app.js (choiceAnalysis) and is pinned to the shared
fixture by ui.test.cjs; here each typed answer must land on the same resolved analysis
as the button request that fixture names.
"""

import json
from pathlib import Path

import pytest
from app import main
from app.contracts import AnalysisRequest
from app.model_adapter import ModelAdapterError, ModelInterpretation, ModelUsage
from fastapi.testclient import TestClient

from tests.test_api import admitted_settings, context_claims

PARITY = json.loads((Path(__file__).parent / "fixtures" / "pending_answer_period.json").read_text(encoding="utf-8"))
PAIR = PARITY["pair"]


@pytest.fixture(autouse=True)
def reset_query_slots():
    main.query_slots.reset()
    yield
    main.query_slots.reset()


def _show_previous(http: TestClient, year: int) -> str:
    shown = http.post("/api/query", json={"analysis": {
        "action": "metric", "airports": ["BOS"], "metric": "passengers", "year": year}})
    assert shown.status_code == 200, shown.text
    assert shown.json()["scope"]["year"] == year
    return shown.json()["result_id"]


def _as_seen(body: dict) -> dict:
    """What the analyst sees, without the per-request result id."""
    return {key: body[key] for key in ("status", "scope", "rows", "summary", "limitations")}


def _model_answering(metric: str, seen: list, year: int | None = None):
    async def interpret(message, *, settings, context, pending_comparison=None):
        seen.append((context, pending_comparison))
        # The model names the measure; with no period in its context it names none.
        return ModelInterpretation("analysis", AnalysisRequest(
            action="compare", airports=list(pending_comparison), metric=metric, year=year), None, ModelUsage(10, 5))
    return interpret


@pytest.mark.parametrize("case", PARITY["cases"], ids=lambda case: f"{case['metric']}-after-{case['previous_year']}")
def test_typed_answer_resolves_like_the_measure_button(monkeypatch, case):
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    seen: list = []
    monkeypatch.setattr(main, "interpret_message", _model_answering(case["metric"], seen))

    typed_http = TestClient(main.app)
    result_id = _show_previous(typed_http, case["previous_year"])
    typed = typed_http.post("/api/query", json={
        "message": "that one", "context_result_id": result_id, "pending_comparison": PAIR})
    assert typed.status_code == 200, typed.text
    context, pending = seen[0]
    assert pending == PAIR and context["airports"] == ["BOS"]
    assert "year" not in context and "bundle_id" not in context, "the period is resolved by the server, not the model"

    button_http = TestClient(main.app)
    _show_previous(button_http, case["previous_year"])
    button = {"action": "compare", "airports": PAIR, "metric": case["metric"]}
    if case["button_year"] is not None:
        button["year"] = case["button_year"]
    clicked = button_http.post("/api/query", json={"analysis": button})
    assert clicked.status_code == 200, clicked.text

    assert _as_seen(typed.json()) == _as_seen(clicked.json())
    assert context_claims(typed_http).request == context_claims(button_http).request
    # The resolved period is part of the result, never implied.
    assert typed.json()["scope"]["year"] == (case["button_year"] or 2025)


def test_a_year_the_analyst_names_is_kept(monkeypatch):
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    seen: list = []
    monkeypatch.setattr(main, "interpret_message", _model_answering("passengers", seen, year=2024))
    http = TestClient(main.app)
    result_id = _show_previous(http, 2023)
    named = http.post("/api/query", json={
        "message": "passengers in 2024", "context_result_id": result_id, "pending_comparison": PAIR})
    assert named.status_code == 200, named.text
    assert named.json()["scope"]["year"] == 2024


def test_an_ordinary_follow_up_still_sees_the_period_on_screen(monkeypatch):
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    seen: list = []

    async def interpret(message, *, settings, context, pending_comparison=None):
        seen.append(context)
        return ModelInterpretation("analysis", AnalysisRequest.model_validate({**context, "metric": "seat_occupancy"}),
                                   None, ModelUsage(10, 5))

    monkeypatch.setattr(main, "interpret_message", interpret)
    http = TestClient(main.app)
    result_id = _show_previous(http, 2023)
    followed = http.post("/api/query", json={"message": "and seat occupancy?", "context_result_id": result_id})
    assert followed.status_code == 200, followed.text
    assert seen[0]["year"] == 2023 and followed.json()["scope"]["year"] == 2023


def _follow_up_model(answers: list, seen: list):
    """Each call takes the next answer: an AnalysisRequest, or a ModelAdapterError to raise."""
    async def interpret(message, *, settings, context, pending_comparison=None):
        seen.append(context)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return ModelInterpretation("analysis", answer, None, ModelUsage(10, 5))
    return interpret


@pytest.mark.parametrize(("metric", "expected_year"), [
    ("passenger_growth", 2025),  # no 2023 growth: the default period, shown with the result
    ("overview", 2025),
    ("seat_occupancy", 2023),    # the period on screen, when the measure has it
])
def test_follow_up_whose_measure_lacks_the_period_on_screen_is_asked_once_more_without_it(
        monkeypatch, metric, expected_year):
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    seen: list = []
    answers = [ModelAdapterError("model_invalid_response"),
               AnalysisRequest(action="compare", airports=["LAX", "SFO"], metric=metric)]
    monkeypatch.setattr(main, "interpret_message", _follow_up_model(answers, seen))
    http = TestClient(main.app)
    shown = http.post("/api/query", json={"analysis": {
        "action": "compare", "airports": ["LAX", "SFO"], "metric": "passengers", "year": 2023}})
    followed = http.post("/api/query", json={"message": "and the rest?", "context_result_id": shown.json()["result_id"]})
    assert followed.status_code == 200, followed.text
    assert followed.json()["scope"]["metric"] == metric and followed.json()["scope"]["year"] == expected_year
    assert len(seen) == 2 and seen[0]["year"] == 2023 and "year" not in seen[1] and "bundle_id" not in seen[1]


def test_an_invalid_answer_without_a_period_on_screen_is_not_retried(monkeypatch):
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    seen: list = []
    monkeypatch.setattr(main, "interpret_message", _follow_up_model([ModelAdapterError("model_invalid_response")], seen))
    response = TestClient(main.app).post("/api/query", json={"message": "Compare LAX and SFO"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "ai_unavailable"
    assert seen == [None]


@pytest.mark.parametrize("first", [ModelAdapterError("model_invalid_response"), ModelAdapterError("model_incomplete")])
def test_the_period_free_retry_happens_at_most_once_and_only_for_an_invalid_answer(monkeypatch, first):
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    seen: list = []
    answers = [first, ModelAdapterError("model_invalid_response")]
    monkeypatch.setattr(main, "interpret_message", _follow_up_model(answers, seen))
    http = TestClient(main.app)
    shown = http.post("/api/query", json={"analysis": {
        "action": "compare", "airports": ["LAX", "SFO"], "metric": "passengers", "year": 2023}})
    response = http.post("/api/query", json={"message": "and growth?", "context_result_id": shown.json()["result_id"]})
    assert response.status_code == 503 and response.json()["error"]["code"] == "ai_unavailable"
    assert len(seen) == (2 if first.code == "model_invalid_response" else 1)
