"""A broad follow-up such as "everything" runs the overview of the airports in play without a model call.

Only exact phrases are recognised; anything longer, or naming a year or new airports, and any
ranking context, still goes to the model.
"""

import pytest
from app import main
from app.contracts import AnalysisRequest
from app.model_adapter import ModelInterpretation, ModelUsage
from fastapi.testclient import TestClient

from tests.test_api import admitted_settings, context_claims


@pytest.fixture(autouse=True)
def reset_query_slots():
    main.query_slots.reset()
    yield
    main.query_slots.reset()


@pytest.fixture
def model_calls(monkeypatch):
    """Every message the model is asked to interpret; it answers with an overview of LAX and SFO."""
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    calls: list[str] = []

    async def interpret(message, *, settings, context, pending_comparison=None):
        calls.append(message)
        return ModelInterpretation("analysis", AnalysisRequest(
            action="compare", airports=["LAX", "SFO"], metric="overview", year=2024), None, ModelUsage(10, 5))

    monkeypatch.setattr(main, "interpret_message", interpret)
    return calls


def _shown(http: TestClient, analysis: dict) -> str:
    response = http.post("/api/query", json={"analysis": analysis})
    assert response.status_code == 200, response.text
    return response.json()["result_id"]


def _ask(http: TestClient, message: str, result_id: str | None = None, pending: list[str] | None = None):
    body: dict = {"message": message}
    if result_id is not None:
        body["context_result_id"] = result_id
    if pending is not None:
        body["pending_comparison"] = pending
    return http.post("/api/query", json=body)


def test_everything_after_a_2023_comparison_is_the_overview_in_the_default_period(model_calls):
    http = TestClient(main.app)
    shown = _shown(http, {"action": "compare", "airports": ["LAX", "SFO"], "metric": "passengers", "year": 2023})
    response = _ask(http, "everything", shown)
    assert response.status_code == 200, response.text
    scope = response.json()["scope"]
    # Overview has no 2023 figures: the default period, shown with the result.
    assert (scope["airports"], scope["metric"], scope["year"]) == (["LAX", "SFO"], "overview", 2025)
    assert (scope["baseline_year"], scope["comparison_year"]) == (2024, 2025)
    assert model_calls == []
    assert (context_claims(http).request.action, context_claims(http).request.metric) == ("compare", "overview")


def test_full_picture_after_a_2023_airport_is_that_airports_overview(model_calls):
    http = TestClient(main.app)
    shown = _shown(http, {"action": "metric", "airports": ["BOS"], "metric": "passengers", "year": 2023})
    response = _ask(http, "Full picture.", shown)
    assert response.status_code == 200, response.text
    scope = response.json()["scope"]
    assert (scope["airports"], scope["metric"], scope["year"]) == (["BOS"], "overview", 2025)
    assert context_claims(http).request.action == "metric"
    assert model_calls == []


@pytest.mark.parametrize("year", [2024, 2025])
def test_a_period_the_overview_supports_is_kept(model_calls, year):
    http = TestClient(main.app)
    shown = _shown(http, {"action": "compare", "airports": ["SFO", "LAX"], "metric": "seat_occupancy", "year": year})
    response = _ask(http, "Overall?", shown)
    assert response.status_code == 200, response.text
    scope = response.json()["scope"]
    assert (scope["airports"], scope["metric"], scope["year"]) == (["SFO", "LAX"], "overview", year)
    assert model_calls == []


def test_all_of_them_answers_which_measure_with_the_pending_pairs_overview(model_calls):
    http = TestClient(main.app)
    shown = _shown(http, {"action": "metric", "airports": ["BOS"], "metric": "passengers", "year": 2023})
    response = _ask(http, "All of them, please.", shown, pending=["SFO", "LAX"])
    assert response.status_code == 200, response.text
    scope = response.json()["scope"]
    assert (scope["airports"], scope["metric"], scope["year"]) == (["SFO", "LAX"], "overview", 2025)
    assert context_claims(http).request.action == "compare"
    assert model_calls == []


@pytest.mark.parametrize("message", [
    "everything in 2024",                       # names a year
    "compare everything at LAX and SFO",        # names airports
    "what changed overall since last year?",    # a longer question containing the word
    "and everything about the delays",
])
def test_anything_beyond_the_exact_phrase_still_goes_to_the_model(model_calls, message):
    http = TestClient(main.app)
    shown = _shown(http, {"action": "compare", "airports": ["LAX", "SFO"], "metric": "passengers", "year": 2025})
    assert _ask(http, message, shown).status_code == 200
    assert model_calls == [message]


def test_a_ranking_on_screen_is_not_intercepted(model_calls):
    http = TestClient(main.app)
    shown = _shown(http, {"action": "rank", "region": "new_england", "metric": "screen_score"})
    _ask(http, "everything", shown)
    assert model_calls == ["everything"]


def test_with_nothing_in_play_the_phrase_goes_to_the_model(model_calls):
    _ask(TestClient(main.app), "everything")
    assert model_calls == ["everything"]
