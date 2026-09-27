import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from app.contracts import AnalysisRequest
from app.model_adapter import ModelAdapterError, ModelInterpretation, ModelUsage
from app.model_budget import BudgetLedger
from app.settings import load_settings
from scripts import eval_intents
from scripts.eval_intents import (
    CorpusValidationError,
    evaluate_cases,
    load_and_validate_cases,
    main,
    write_report,
)

CORPUS = Path(__file__).parent / "fixtures" / "intent_eval.json"


def _live_settings(**overrides):
    values = {
        "OPENAI_API_KEY": "fake-key-never-output",
        "OPENAI_MODEL": "fake.model-v1",
        "MODEL_INPUT_USD_PER_MILLION_TOKENS": "0.15",
        "MODEL_OUTPUT_USD_PER_MILLION_TOKENS": "0.6",
    }
    values.update(overrides)
    return load_settings(values)


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
        corpus["cases"][0]["expected"]["analysis"]["year"] = 2026
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
    assert report["acceptance_policy"]["overall_correct_min"] == 29
    assert report["results"][0]["usage"] == {"input_tokens": 8, "output_tokens": 4}
    assert all(result["latency_ms"] >= 0 for result in report["results"])
    output = tmp_path / "candidate-output.json"
    write_report(report, output)
    assert json.loads(output.read_text()) == report


@pytest.mark.parametrize(
    ("missed_category", "miss_count", "accepted"),
    [
        ("ordinary", 3, False),  # 27/30 is below the new bar.
        ("ordinary", 1, True),   # One ordinary miss is permitted.
        ("demonstration", 1, False),
        ("safety_clarification", 1, False),
    ],
)
def test_candidate_admission_requires_29_exact_and_every_demo_and_safety_case(
    missed_category, miss_count, accepted,
):
    cases = load_and_validate_cases(CORPUS)
    missed_ids = {
        case["id"] for case in cases if case["category"] == missed_category
    }
    missed_ids = set(sorted(missed_ids)[:miss_count])
    position = {"value": 0}

    def candidate(_text, _context):
        case = cases[position["value"]]
        position["value"] += 1
        if case["id"] in missed_ids:
            return {"kind": "unsupported_scope", "message": "Cannot determine this."} if case[
                "expected"
            ]["kind"] == "analysis" else cases[0]["expected"]
        return case["expected"]

    report = evaluate_cases(cases, candidate, mode="candidate")
    assert report["correct_count"] == 30 - miss_count
    assert report["error_count"] == 0
    assert report["candidate_acceptance"] is accepted


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


def test_live_candidate_cli_uses_shared_adapter_and_records_bounded_cost_metadata(tmp_path, monkeypatch, capsys):
    cases = load_and_validate_cases(CORPUS)
    settings = _live_settings()
    monkeypatch.setattr(eval_intents, "load_settings", lambda: settings)
    calls = []

    async def fake_interpret(text, *, context, settings):
        case = cases[len(calls)]
        assert (text, context) == (case["input"]["text"], case["input"]["context"])
        calls.append(text)
        expected = case["expected"]
        analysis = AnalysisRequest.model_validate(expected["analysis"]) if expected["kind"] == "analysis" else None
        return ModelInterpretation(expected["kind"], analysis, expected.get("message"), ModelUsage(10, 5))

    monkeypatch.setattr(eval_intents.model_adapter, "interpret_message", fake_interpret)
    output = tmp_path / "live.json"
    assert main(["--mode", "candidate", "--live", "--acceptance", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    capsys.readouterr()
    assert len(calls) == 30
    assert report["candidate_acceptance"] is True
    assert report["model"] == "fake.model-v1"
    assert report["prompt_sha256"] == eval_intents.model_adapter.PROMPT_SHA256
    assert report["adapter_sha256"] == eval_intents.model_adapter.ADAPTER_SHA256
    assert report["corpus_sha256"] == hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    assert report["rate_card_usd_per_million_tokens"] == {"input": 0.15, "output": 0.6}
    assert report["aggregate_cost_usd"] == pytest.approx(30 * 4.5 / 1_000_000)
    assert all(item["usage"] == {"input_tokens": 10, "output_tokens": 5, "cost_usd": 4.5 / 1_000_000}
               for item in report["results"])
    assert all(item["cost_usd"] == 4.5 / 1_000_000 for item in report["results"])
    assert all(item["latency_ms"] >= 0 for item in report["results"])
    assert "fake-key-never-output" not in output.read_text()


def test_live_candidate_missing_config_makes_no_adapter_call(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eval_intents, "load_settings", lambda: load_settings({}))

    async def forbidden(*_args, **_kwargs):
        pytest.fail("adapter must not be called")

    monkeypatch.setattr(eval_intents.model_adapter, "interpret_message", forbidden)
    output = tmp_path / "missing.json"
    assert main(["--mode", "candidate", "--live", "--acceptance", "--output", str(output)]) == 1
    report = json.loads(output.read_text())
    capsys.readouterr()
    assert report["candidate_status"] == "missing_model_configuration_no_provider_call_made"
    assert report["aggregate_cost_usd"] == 0.0


def test_live_candidate_budget_denial_prevents_adapter_call(monkeypatch):
    settings = _live_settings(MODEL_REQUEST_BUDGET_USD="0")

    async def forbidden(*_args, **_kwargs):
        pytest.fail("budget denial must precede adapter call")

    monkeypatch.setattr(eval_intents.model_adapter, "interpret_message", forbidden)
    report = eval_intents.evaluate_live_candidate(load_and_validate_cases(CORPUS), settings, "a" * 64)
    assert report["error_count"] == 30
    assert all(item["error"] == "error:BudgetExhausted" for item in report["results"])
    assert all(item["cost_usd"] == 0.0 for item in report["results"])
    assert report["aggregate_cost_usd"] == 0.0
    assert report["candidate_acceptance"] is False


def test_live_candidate_timeout_forfeits_reservation_then_denies_further_calls(monkeypatch):
    settings = _live_settings(MODEL_PROCESS_BUDGET_USD="0.003")
    reserved = BudgetLedger().reserve(settings).reserved_usd
    calls = []

    async def timed_out(*_args, **_kwargs):
        calls.append(1)
        raise ModelAdapterError("model_timeout")

    monkeypatch.setattr(eval_intents.model_adapter, "interpret_message", timed_out)
    report = eval_intents.evaluate_live_candidate(load_and_validate_cases(CORPUS), settings, "a" * 64)
    assert len(calls) == 1
    assert report["results"][0]["error"] == "timeout"
    assert report["results"][0]["cost_usd"] == float(reserved)
    assert all(item["error"] == "error:BudgetExhausted" for item in report["results"][1:])
    assert report["aggregate_cost_usd"] == float(reserved)
    assert report["candidate_acceptance"] is False


def test_baseline_cli_records_output_and_acceptance_flag_does_not_apply_candidate_bar(tmp_path, capsys):
    output = tmp_path / "baseline.json"
    assert main(["--mode", "baseline", "--cases", str(CORPUS), "--acceptance", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    printed = json.loads(capsys.readouterr().out)
    assert report == printed
    assert report["case_count"] == 30
    assert report["candidate_acceptance"] is None
    assert "descriptive" in report["acceptance_note"]
