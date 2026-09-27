import json
from collections import Counter
from pathlib import Path

import pytest
from scripts.eval_intents import (
    CorpusValidationError,
    evaluate_cases,
    load_and_validate_cases,
    main,
    write_report,
)

CORPUS = Path(__file__).parent / "fixtures" / "intent_eval.json"


def test_frozen_corpus_has_exact_unique_inventory_and_valid_context_and_outputs():
    cases = load_and_validate_cases(CORPUS)
    assert len(cases) == 30
    assert len({case["id"] for case in cases}) == 30
    assert Counter(case["category"] for case in cases) == {
        "demonstration": 6, "safety_clarification": 8, "ordinary": 16,
    }
    assert any(case["input"]["context"] for case in cases)


@pytest.mark.parametrize("mutation", ["duplicate_id", "bad_count", "invalid_expected", "invalid_context"])
def test_corpus_validator_rejects_bad_inventory_or_invalid_wire_scope(tmp_path, mutation):
    corpus = json.loads(CORPUS.read_text())
    if mutation == "duplicate_id":
        corpus["cases"][1]["id"] = corpus["cases"][0]["id"]
    elif mutation == "bad_count":
        corpus["cases"].pop()
    elif mutation == "invalid_expected":
        corpus["cases"][0]["expected"]["analysis"]["year"] = 2025
    else:
        corpus["cases"][10]["input"]["context"] = {"action": "compare", "airports": ["BOS"],
                                                      "metric": "passenger_growth", "year": 2024}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(corpus))
    with pytest.raises(CorpusValidationError):
        load_and_validate_cases(path)


def test_injected_candidate_scores_exact_outputs_usage_latency_and_output_recording(tmp_path):
    cases = load_and_validate_cases(CORPUS)

    # Bind each expected case without introducing parser/candidate coupling.
    position = {"value": 0}

    def candidate(_text, _context):
        case = cases[position["value"]]
        position["value"] += 1
        return {"outcome": case["expected"], "usage": {"input_tokens": 8, "output_tokens": 4, "api_key": "ignore"}}

    report = evaluate_cases(cases, candidate, mode="candidate")
    assert report["case_count"] == 30
    assert report["accuracy"] == 1.0
    assert report["candidate_acceptance"] is True
    assert report["results"][0]["usage"] == {"input_tokens": 8, "output_tokens": 4}
    assert all(result["latency_ms"] >= 0 for result in report["results"])
    output = tmp_path / "candidate-output.json"
    write_report(report, output)
    assert json.loads(output.read_text()) == report


def test_candidate_failures_and_timeouts_fail_acceptance_without_losing_case_records():
    cases = load_and_validate_cases(CORPUS)
    index = {"value": 0}

    def failing_candidate(_text, _context):
        current = index["value"]
        index["value"] += 1
        if current == 0:
            raise TimeoutError("simulated")
        if current == 1:
            raise RuntimeError("simulated provider failure")
        return cases[current]["expected"]

    report = evaluate_cases(cases, failing_candidate, mode="candidate")
    assert report["error_count"] == 2
    assert report["results"][0]["error"] == "timeout"
    assert report["results"][1]["error"] == "error:RuntimeError"
    assert report["candidate_acceptance"] is False
    assert len(report["results"]) == 30


def test_candidate_outputs_are_projected_and_cli_has_no_provider_adapter(tmp_path, capsys):
    cases = load_and_validate_cases(CORPUS)

    def overbroad(_text, _context):
        expected = cases[0]["expected"]
        return {"kind": "analysis", "analysis": expected["analysis"], "secret": "drop"}

    report = evaluate_cases(cases[:1], overbroad, mode="candidate")
    assert report["results"][0]["actual"] is None
    assert report["candidate_acceptance"] is False

    output = tmp_path / "candidate.json"
    assert main(["--mode", "candidate", "--cases", str(CORPUS), "--output", str(output)]) == 0
    recorded = json.loads(output.read_text())
    capsys.readouterr()
    assert recorded["candidate_status"] == "unconfigured_no_provider_call_made"
    assert recorded["error_count"] == 30


def test_baseline_cli_records_output_and_acceptance_flag_does_not_apply_candidate_bar(tmp_path, capsys):
    output = tmp_path / "baseline.json"
    assert main(["--mode", "baseline", "--cases", str(CORPUS), "--acceptance", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    printed = json.loads(capsys.readouterr().out)
    assert report == printed
    assert report["case_count"] == 30
    assert report["candidate_acceptance"] is None
    assert "descriptive" in report["acceptance_note"]
