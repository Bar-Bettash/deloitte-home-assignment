import json
from pathlib import Path

from app.intent import parse_intent

CORPUS = Path(__file__).parent / "fixtures" / "intent_eval_2025.json"


def test_six_demo_phrasings_produce_independently_authored_expected_requests():
    cases = json.loads(CORPUS.read_text())["cases"]
    demos = [case for case in cases if case["category"] == "demonstration"]
    assert len(demos) == 6
    for case in demos:
        actual = parse_intent(case["input"]["text"], case["input"]["context"])
        assert actual == case["expected"], case["id"]


def test_explicit_supported_metric_syntax_and_contextual_followup():
    assert parse_intent("Rank New England airports by passengers in 2023")["analysis"] == {
        "action": "rank", "region": "new_england", "metric": "passengers", "year": 2023,
    }
    followup = parse_intent(
        "Show only cancellations",
        {"action": "compare", "airports": ["LAX", "SNA"], "metric": "congestion", "year": 2024},
    )
    assert followup["analysis"] == {
        "action": "compare", "airports": ["LAX", "SNA"], "metric": "cancellation_rate", "year": 2024,
    }


def test_ambiguous_unknown_and_unsupported_inputs_are_safe_outcomes():
    assert parse_intent("Compare LA airports for congestion in 2024")["kind"] == "clarification_required"
    assert parse_intent("Which airport will be most profitable?")["kind"] == "unsupported_scope"
    assert parse_intent("Show NRT long-haul share in 2024")["kind"] == "unsupported_scope"
    assert parse_intent("Compare BOS and PVD growth in 2026")["kind"] == "unsupported_scope"
    assert parse_intent("Compare BOS passengers in 2024 and 2025")["kind"] == "clarification_required"


def test_2025_and_omitted_years_are_supported_without_guessing():
    assert parse_intent("Compare BOS and PVD passenger growth in 2025")["analysis"] == {
        "action": "compare", "airports": ["BOS", "PVD"], "metric": "passenger_growth", "year": 2025,
    }
    assert parse_intent("New England screening")["analysis"] == {
        "action": "rank", "region": "new_england", "metric": "screen_score",
    }
    assert parse_intent("Compare LAX and SNA congestion")["analysis"] == {
        "action": "compare", "airports": ["LAX", "SNA"], "metric": "congestion",
    }


def test_followup_keeps_signed_period_but_not_bundle_identity():
    context = {"action": "compare", "airports": ["LAX", "SNA"], "metric": "congestion",
               "year": 2025, "bundle_id": "annual-2025-r1"}
    assert parse_intent("Show only delays", context)["analysis"] == {
        "action": "compare", "airports": ["LAX", "SNA"], "metric": "departure_delay_minutes", "year": 2025,
    }


def test_invalid_context_does_not_authorize_followup_scope():
    result = parse_intent("Show only cancellations", {"action": "explain", "year": 2024})
    assert result["kind"] == "clarification_required"
