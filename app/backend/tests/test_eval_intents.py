import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from app.contracts import AnalysisRequest
from app.model_adapter import ModelAdapterError, ModelInterpretation, ModelUsage
from app.settings import load_settings
from scripts import eval_intents
from scripts.eval_intents import (
    CorpusValidationError,
    evaluate_cases,
    load_and_validate_cases,
    main,
    write_report,
)

CORPUS = Path(__file__).parent / "fixtures" / "intent_eval_2025.json"
HISTORICAL_CORPUS = Path(__file__).parent / "fixtures" / "intent_eval.json"


def _live_settings(**overrides):
    values = {
        "GEMINI_API_KEY": "fake-key-never-output",
        "GEMINI_MODEL": "fake.model-v1",
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
    assert main(["--mode", "candidate", "--live", "--acceptance", "--output", str(output),
                 "--input-usd-per-mtok", "0.15", "--output-usd-per-mtok", "0.6"]) == 0
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
    assert report["unknown_cost_calls"] == 0
    assert 0 <= report["latency_ms_p50"] <= report["latency_ms_p95"]
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


def test_live_candidate_without_rate_card_makes_no_adapter_call(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(eval_intents, "load_settings", lambda: _live_settings())

    async def forbidden(*_args, **_kwargs):
        pytest.fail("adapter must not be called without a recorded rate card")

    monkeypatch.setattr(eval_intents.model_adapter, "interpret_message", forbidden)
    output = tmp_path / "no-rates.json"
    assert main(["--mode", "candidate", "--live", "--acceptance", "--output", str(output)]) == 1
    capsys.readouterr()
    assert json.loads(output.read_text())["candidate_status"] == "missing_model_configuration_no_provider_call_made"


@pytest.mark.parametrize("rate", ["0", "-1", "nan", "inf", "1000.01", "cheap"])
def test_live_rate_card_arguments_are_bounded(rate, capsys):
    with pytest.raises(SystemExit):
        main(["--mode", "candidate", "--live", "--input-usd-per-mtok", rate, "--output-usd-per-mtok", "1"])
    capsys.readouterr()


def test_live_candidate_timeout_is_recorded_with_unknown_cost_and_one_call_per_case(monkeypatch):
    settings = _live_settings()
    calls = []

    async def timed_out(*_args, **_kwargs):
        calls.append(1)
        raise ModelAdapterError("model_timeout")

    monkeypatch.setattr(eval_intents.model_adapter, "interpret_message", timed_out)
    report = eval_intents.evaluate_live_candidate(
        load_and_validate_cases(CORPUS), settings, "a" * 64,
        input_usd_per_million_tokens=0.15, output_usd_per_million_tokens=0.6,
    )
    assert len(calls) == 30
    assert all(item["error"] == "timeout" and item["cost_usd"] is None for item in report["results"])
    assert report["aggregate_cost_usd"] == 0
    assert report["unknown_cost_calls"] == 30
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


def test_default_corpus_is_the_2025_corpus_and_historical_corpus_still_validates():
    assert eval_intents.DEFAULT_CASES == CORPUS
    assert len(load_and_validate_cases(HISTORICAL_CORPUS)) == 30


def test_2025_corpus_covers_omitted_years_2025_and_signed_followup_context():
    cases = load_and_validate_cases(CORPUS)
    expected = [case["expected"]["analysis"] for case in cases if case["expected"]["kind"] == "analysis"]
    assert any("year" not in analysis and analysis["action"] != "explain" for analysis in expected)
    assert any(analysis.get("year") == 2025 for analysis in expected)
    contexts = [case["input"]["context"] for case in cases if case["input"]["context"]]
    assert len(contexts) >= 4
    assert any(context.get("bundle_id") == "annual-2025-r1" for context in contexts)
    assert all("bundle_id" not in analysis for analysis in expected)
    assert any(analysis == {"action": "explain"} for analysis in expected)


def test_baseline_on_2025_corpus_passes_every_safety_case(capsys):
    assert main(["--mode", "baseline"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["case_count"] == 30
    assert report["category_accuracy"]["safety_clarification"] == 1.0
    assert report["category_accuracy"]["demonstration"] == 1.0


HOLDOUT = Path(__file__).parent / "fixtures" / "intent_holdout_2025.json"


def _perfect_candidate(cases):
    def candidate(text, context):
        case = next(item for item in cases if item["input"]["text"] == text and item["input"]["context"] == context)
        return case["expected"]

    return candidate


def test_holdout_has_its_own_inventory_and_policy():
    cases = load_and_validate_cases(HOLDOUT, eval_intents.HOLDOUT_POLICY)
    assert len(cases) == 12
    assert Counter(case["category"] for case in cases) == {"safety_clarification": 5, "ordinary": 7}
    with pytest.raises(CorpusValidationError, match="holdout policy expects exactly 12"):
        load_and_validate_cases(CORPUS, eval_intents.HOLDOUT_POLICY)
    with pytest.raises(CorpusValidationError, match="corpus policy expects exactly 30"):
        load_and_validate_cases(HOLDOUT)


def test_holdout_questions_are_unseen_paraphrases():
    holdout = load_and_validate_cases(HOLDOUT, eval_intents.HOLDOUT_POLICY)
    seen = {case["input"]["text"].lower() for path in (CORPUS, HISTORICAL_CORPUS)
            for case in load_and_validate_cases(path)}
    assert not {case["input"]["text"].lower() for case in holdout} & seen
    # It covers the failure families without reusing their wording.
    kinds = Counter(case["expected"]["kind"] for case in holdout)
    assert kinds == {"analysis": 7, "unsupported_scope": 2, "clarification_required": 3}
    assert any(case["expected"].get("message") == "Submit one analysis at a time." for case in holdout)
    assert any(case["expected"].get("analysis") == {"action": "explain"} for case in holdout)


def test_holdout_policy_needs_11_of_12_every_safety_case_and_no_errors():
    cases = load_and_validate_cases(HOLDOUT, eval_intents.HOLDOUT_POLICY)
    perfect = _perfect_candidate(cases)
    report = evaluate_cases(cases, perfect, mode="candidate", policy=eval_intents.HOLDOUT_POLICY)
    assert report["candidate_acceptance"] is True
    assert report["acceptance_policy"] == {
        "name": "holdout", "case_count_required": 12,
        "category_counts_required": {"safety_clarification": 5, "ordinary": 7},
        "overall_correct_min": 11, "safety_correct_required": 5, "errors_max": 0,
    }

    def miss(target_category):
        target = next(case for case in cases if case["category"] == target_category)

        def candidate(text, context):
            if text == target["input"]["text"]:
                return {"kind": "unsupported_scope", "message": "wrong"} if target_category == "ordinary" \
                    else {"kind": "analysis", "analysis": {"action": "explain"}}
            return perfect(text, context)

        return evaluate_cases(cases, candidate, mode="candidate", policy=eval_intents.HOLDOUT_POLICY)

    assert miss("ordinary")["candidate_acceptance"] is True      # 11/12, safety intact
    assert miss("safety_clarification")["candidate_acceptance"] is False


def test_holdout_cli_uses_holdout_policy(tmp_path, capsys):
    output = tmp_path / "holdout.json"
    assert main(["--mode", "baseline", "--cases", str(HOLDOUT), "--policy", "holdout", "--output", str(output)]) == 0
    capsys.readouterr()
    report = json.loads(output.read_text())
    assert report["case_count"] == 12
    assert report["acceptance_policy"]["name"] == "holdout"


def test_omitted_long_haul_threshold_matches_the_documented_default():
    case = {"id": "x", "category": "ordinary", "input": {"text": "ANC long haul", "context": None},
            "expected": {"kind": "analysis", "analysis": {
                "action": "metric", "airports": ["ANC"], "metric": "long_haul_share", "threshold_miles": 3000}}}
    omitted = {"kind": "analysis", "analysis": {"action": "metric", "airports": ["ANC"], "metric": "long_haul_share"}}
    as_float = {"kind": "analysis", "analysis": {**omitted["analysis"], "threshold_miles": 3000.0}}
    other = {"kind": "analysis", "analysis": {**omitted["analysis"], "threshold_miles": 2500}}
    assert eval_intents._matches(case["expected"], omitted)
    assert eval_intents._matches(case["expected"], as_float)
    assert not eval_intents._matches(case["expected"], other)
    # The default does not leak onto other metrics.
    departures = {"kind": "analysis", "analysis": {"action": "metric", "airports": ["ANC"], "metric": "departures"}}
    assert eval_intents._matches(departures, departures)


def test_documented_default_is_the_threshold_the_dispatcher_applies(monkeypatch):
    from uuid import uuid4

    from app import dispatch
    from app.calculations.long_haul import calculate_long_haul_share

    used = []

    def spy(airport, year, threshold, *args, **kwargs):
        used.append(threshold)
        return calculate_long_haul_share(airport, year, threshold, *args, **kwargs)

    monkeypatch.setattr(dispatch, "calculate_long_haul_share", spy)
    request = AnalysisRequest.model_validate({"action": "metric", "airports": ["ANC"],
                                              "metric": "long_haul_share", "year": 2024})
    dispatch.dispatch_analysis(request, uuid4())
    assert used and set(used) == {float(eval_intents.DOCUMENTED_LONG_HAUL_THRESHOLD_MILES)}


def test_rejected_output_is_costed_and_only_no_response_calls_are_unknown(monkeypatch):
    settings = _live_settings()
    cases = load_and_validate_cases(CORPUS)
    calls = []

    async def mixed(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise ModelAdapterError("model_invalid_response", usage=ModelUsage(100, 20))
        if len(calls) == 2:
            raise ModelAdapterError("ai_unavailable", provider_status=500)
        raise ModelAdapterError("model_timeout")

    monkeypatch.setattr(eval_intents.model_adapter, "interpret_message", mixed)
    report = eval_intents.evaluate_live_candidate(
        cases, settings, "a" * 64, input_usd_per_million_tokens=1.0, output_usd_per_million_tokens=10.0)
    first = report["results"][0]
    assert first["error"] == "error:ModelAdapterError:model_invalid_response"
    assert first["cost_usd"] == pytest.approx(300 / 1_000_000)
    assert report["results"][1]["error"] == "error:ModelAdapterError:ai_unavailable:http_500"
    assert report["results"][1]["cost_usd"] is None
    assert report["results"][2]["error"] == "timeout"
    assert report["aggregate_cost_usd"] == pytest.approx(300 / 1_000_000)
    assert report["rejected_output_costed_calls"] == 1
    assert report["unknown_cost_calls"] == 29
