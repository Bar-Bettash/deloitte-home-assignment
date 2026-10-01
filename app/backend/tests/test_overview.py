"""The overall comparison: the key measures valid for the airports, side by side, no combined score."""

from uuid import UUID, uuid4

import pytest
from app import main
from app.contracts import AnalysisRequest, QueryRequest
from app.dispatch import CONGESTION_KEYS, OVERVIEW_TRAFFIC_KEYS, dispatch_analysis
from app.model_adapter import ModelInterpretation, ModelUsage, comparison_question
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.test_api import admitted_settings, context_claims

TRAFFIC_AND_DISTANCE = [*OVERVIEW_TRAFFIC_KEYS, "long_haul_share"]


@pytest.fixture(autouse=True)
def reset_query_slots():
    main.query_slots.reset()
    yield
    main.query_slots.reset()


def _overview(airports, **extra):
    action = "compare" if len(airports) == 2 else "metric"
    return dispatch_analysis(AnalysisRequest(action=action, airports=airports, metric="overview", **extra), uuid4())


def test_operational_pair_gets_all_eight_measures_from_two_sources():
    result = _overview(["LAX", "SFO"])
    assert result.status == "ok" and result.scope.metric == "overview" and result.scope.year == 2025
    assert [row.airport for row in result.rows] == ["LAX", "SFO"]
    for row in result.rows:
        assert [metric.key for metric in row.metrics] == [*TRAFFIC_AND_DISTANCE, *CONGESTION_KEYS]
        # Every available pair carries its direction, so the browser never compares values itself.
        assert all(metric.comparison_direction in {"higher", "lower", "tied"} for metric in row.metrics)
    assert {source.id.split("-")[0] for source in result.sources} == {"t100", "ontime"}
    assert result.scope.threshold_miles == 3000
    assert result.summary.startswith("LAX vs SFO in 2025, on 8 comparable measures: ")
    assert "LAX is higher on passengers (36,718,978 vs 26,477,602)" in result.summary
    assert "higher means more disruption" in result.summary
    assert result.limitations[0].startswith("Measures are shown side by side; no combined score is calculated")


def test_overview_values_match_the_single_measure_analyses():
    overview = _overview(["LAX", "SFO"])
    by_key = {(row.airport, metric.key): metric.value for row in overview.rows for metric in row.metrics}
    for metric in ("passengers", "passenger_growth", "seat_occupancy", "long_haul_share", "congestion"):
        single = dispatch_analysis(AnalysisRequest(action="compare", airports=["LAX", "SFO"], metric=metric), uuid4())
        for row in single.rows:
            for item in row.metrics:
                assert by_key[(row.airport, item.key)] == item.value


def test_pair_outside_operations_coverage_drops_operational_measures_and_says_why():
    result = _overview(["BOS", "SFO"])
    assert all([metric.key for metric in row.metrics] == TRAFFIC_AND_DISTANCE for row in result.rows)
    assert [source.id.split("-")[0] for source in result.sources] == ["t100"]
    assert result.limitations[0].startswith("Operational measures (cancellations, diversions")
    assert "disruption" not in result.summary


def test_one_airport_overview_lists_its_measures():
    result = _overview(["ANC"])
    assert len(result.rows) == 1 and [metric.key for metric in result.rows[0].metrics] == TRAFFIC_AND_DISTANCE
    assert all(metric.comparison_direction is None for metric in result.rows[0].metrics)
    assert result.summary.startswith("ANC in 2025: passengers 2,636,007, passenger growth -0.93% vs 2024")


def test_historical_overview_uses_the_2024_period():
    result = _overview(["LAX", "SNA"], year=2024)
    assert result.scope.year == 2024 and result.scope.bundle_id is None
    assert result.summary.startswith("LAX vs SNA in 2024")
    assert "(growth vs 2023)" in result.scope.population


@pytest.mark.parametrize("analysis", [
    {"action": "compare", "airports": ["LAX", "SFO"], "metric": "overview", "year": 2023},
    {"action": "compare", "airports": ["LAX", "SFO"], "metric": "overview", "threshold_miles": 2500},
    {"action": "rank", "region": "new_england", "metric": "overview"},
    {"action": "compare", "airports": ["LAX"], "metric": "overview"},
    {"action": "metric", "airports": ["LAX", "SFO"], "metric": "overview"},
])
def test_contract_rejects_overview_outside_its_scope(analysis):
    with pytest.raises(ValidationError):
        AnalysisRequest.model_validate(analysis)


def test_explaining_an_overview_names_the_widest_gap_and_states_no_score():
    previous = _overview(["BOS", "SFO"])
    explained = dispatch_analysis(AnalysisRequest(action="explain"), uuid4(), previous=previous)
    assert explained.result_id == previous.result_id and explained.rows == previous.rows
    assert "SFO is higher on 4 of 4" in explained.summary
    assert "The widest relative gap is in long-haul share: SFO 13.31% versus BOS 7.3%, about 1.8 times as high." in explained.summary
    assert explained.summary.endswith("not terminal capacity, unmet demand or profitability.")


def test_explaining_an_overview_with_nothing_comparable_never_calls_it_a_tie():
    previous = _overview(["BOS", "PVC"])
    assert all(metric.value is None for metric in previous.rows[1].metrics)
    explained = dispatch_analysis(AnalysisRequest(action="explain"), uuid4(), previous=previous)
    assert "No measure is comparable for both BOS and PVC." in explained.summary
    assert "tied" not in explained.summary


def test_clarification_offers_the_overall_comparison_first():
    question = comparison_question(["LAX", "SFO"], None)
    assert question.startswith("Which measure should I compare for LAX and SFO: an overall comparison of the key measures, ")
    assert "“Compare everything”" in question


def test_screenshot_conversation_runs_end_to_end(monkeypatch):
    """"analysis of LAX versus SFO" -> which measure? -> "compare each thing" -> overview -> "what stands out?"."""
    monkeypatch.setattr(main, "load_settings", admitted_settings)
    http = TestClient(main.app)
    calls = []

    async def interpret(message, *, settings, context, pending_comparison=None):
        calls.append((message, context, pending_comparison))
        if message.startswith("give me"):
            return ModelInterpretation("clarification_required", None, "provider prose", ModelUsage(10, 5), ("LAX", "SFO"))
        if message.startswith("do an overall"):
            analysis = AnalysisRequest(action="compare", airports=list(pending_comparison), metric="overview")
        elif message == "What stands out?":
            analysis = AnalysisRequest(action="explain")
        else:
            analysis = AnalysisRequest(action="compare", airports=context["airports"], metric="congestion")
        return ModelInterpretation("analysis", analysis, None, ModelUsage(10, 5))

    monkeypatch.setattr(main, "interpret_message", interpret)
    asked = http.post("/api/query", json={"message": "give me an analysis of LAX versus SFO"})
    assert asked.status_code == 422
    error = asked.json()["error"]
    assert error["code"] == "clarification_required" and error["pending_comparison"] == ["LAX", "SFO"]
    assert "an overall comparison of the key measures" in error["message"]

    overall = http.post("/api/query", json={"message": "do an overall test and compare each thing",
                                            "pending_comparison": error["pending_comparison"]})
    assert overall.status_code == 200, overall.text
    body = overall.json()
    assert body["scope"]["metric"] == "overview" and body["scope"]["airports"] == ["LAX", "SFO"]
    assert context_claims(http).request.metric == "overview"

    # The signed overview context is recomputed and verified for the follow-ups.
    explained = http.post("/api/query", json={"message": "What stands out?", "context_result_id": body["result_id"]})
    assert explained.status_code == 200, explained.text
    assert explained.json()["result_id"] == body["result_id"]
    assert "The widest relative gap is in passengers" in explained.json()["summary"]

    narrowed = http.post("/api/query", json={"message": "Now just congestion", "context_result_id": body["result_id"]})
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json()["scope"]["metric"] == "congestion" and narrowed.json()["scope"]["airports"] == ["LAX", "SFO"]
    assert calls[1][2] == ["LAX", "SFO"] and calls[2][1]["metric"] == "overview"
    assert context_claims(http).result_id == UUID(narrowed.json()["result_id"])


def test_unsupported_reply_says_what_is_possible(monkeypatch):
    monkeypatch.setattr(main, "load_settings", admitted_settings)

    async def interpret(*_args, **_kwargs):
        return ModelInterpretation("unsupported_scope", None, "provider prose", ModelUsage(10, 5))

    monkeypatch.setattr(main, "interpret_message", interpret)
    response = TestClient(main.app).post("/api/query", json={"message": "What is LAX worth?"})
    assert response.status_code == 422
    assert response.json()["error"]["message"] == (
        "I can't answer that from this airport data. I can rank New England airports, compare two airports on one "
        "measure or overall, or show one airport's figures for 2023 to 2025.")


def test_structured_overview_request_is_accepted_on_the_wire():
    request = QueryRequest.model_validate({"analysis": {"action": "compare", "airports": ["LAX", "SFO"], "metric": "overview"}})
    assert request.analysis.metric == "overview"
