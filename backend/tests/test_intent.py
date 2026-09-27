import json
from pathlib import Path

from app.intent import parse_intent

CORPUS = Path(__file__).parent / "fixtures" / "intent_eval.json"


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
    assert parse_intent("Compare BOS and PVD growth in 2025")["kind"] == "unsupported_scope"


def test_invalid_context_does_not_authorize_followup_scope():
    result = parse_intent("Show only cancellations", {"action": "explain", "year": 2024})
    assert result["kind"] == "clarification_required"
