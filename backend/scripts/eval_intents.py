#!/usr/bin/env python3
"""Validate and evaluate the frozen local intent corpus without provider calls."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.contracts import AnalysisRequest
from app.intent import parse_intent
from pydantic import ValidationError

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "intent_eval.json"
EXPECTED_CATEGORIES = {"demonstration": 6, "safety_clarification": 8, "ordinary": 16}
MIN_ACCEPTANCE_CORRECT = 29


class CorpusValidationError(ValueError):
    """The frozen corpus violates its versioned schema or declared inventory."""


def load_and_validate_cases(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusValidationError("unable to read valid corpus JSON") from exc
    if not isinstance(data, dict) or set(data) != {"version", "cases"} or type(data["version"]) is not int or data["version"] != 1:
        raise CorpusValidationError("corpus root must contain version 1 and cases")
    cases = data["cases"]
    if not isinstance(cases, list) or len(cases) != 30:
        raise CorpusValidationError("corpus must contain exactly 30 cases")

    ids: set[str] = set()
    counts: Counter[str] = Counter()
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) != {"id", "category", "input", "expected"}:
            raise CorpusValidationError(f"case {index} has missing or extra fields")
        case_id, category = case["id"], case["category"]
        if not isinstance(case_id, str) or not case_id.strip() or len(case_id) > 80 or case_id in ids:
            raise CorpusValidationError(f"case {index} has an empty, oversized, or duplicate ID")
        ids.add(case_id)
        if not isinstance(category, str) or category not in EXPECTED_CATEGORIES:
            raise CorpusValidationError(f"case {case_id} has an unknown category")
        counts[category] += 1

        input_value = case["input"]
        if not isinstance(input_value, dict) or set(input_value) != {"text", "context"}:
            raise CorpusValidationError(f"case {case_id} input must contain text and context")
        text = input_value["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise CorpusValidationError(f"case {case_id} has invalid input text")
        if input_value["context"] is not None:
            _valid_analysis(input_value["context"], f"case {case_id} context")
        _valid_expected(case["expected"], case_id)
        expected_kind = case["expected"]["kind"]
        if category == "demonstration" and expected_kind != "analysis":
            raise CorpusValidationError(f"case {case_id} demonstration must expect an analysis")
        if category == "safety_clarification" and expected_kind not in {"clarification_required", "unsupported_scope"}:
            raise CorpusValidationError(f"case {case_id} safety case must expect a safe outcome")

    if dict(counts) != EXPECTED_CATEGORIES:
        raise CorpusValidationError(f"category counts must be {EXPECTED_CATEGORIES}")
    return cases


def _valid_analysis(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CorpusValidationError(f"{label} must be an AnalysisRequest object")
    try:
        normalized = AnalysisRequest.model_validate(value).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise CorpusValidationError(f"{label} is not a valid AnalysisRequest") from exc
    if normalized != value:
        raise CorpusValidationError(f"{label} must use canonical fields and values")
    return normalized


def _valid_expected(value: Any, case_id: str) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        raise CorpusValidationError(f"case {case_id} has invalid expected outcome")
    kind = value["kind"]
    if kind == "analysis":
        if set(value) != {"kind", "analysis"}:
            raise CorpusValidationError(f"case {case_id} analysis expectation has extra fields")
        _valid_analysis(value["analysis"], f"case {case_id} expected analysis")
    elif kind in {"clarification_required", "unsupported_scope"}:
        if set(value) != {"kind", "message"} or not isinstance(value["message"], str) or not value["message"].strip():
            raise CorpusValidationError(f"case {case_id} safe expectation needs a message")
    else:
        raise CorpusValidationError(f"case {case_id} has an undeclared expected outcome")


def _matches(expected: dict[str, Any], actual: Any) -> bool:
    if not isinstance(actual, dict) or actual.get("kind") != expected["kind"]:
        return False
    if expected["kind"] == "analysis":
        try:
            normalized = _valid_analysis(actual.get("analysis"), "actual analysis")
        except CorpusValidationError:
            return False
        return normalized == _valid_analysis(expected["analysis"], "expected analysis")
    return isinstance(actual.get("message"), str) and bool(actual["message"].strip())


def _project_actual(value: Any) -> dict[str, Any] | None:
    """Keep evaluation output bounded to the declared safe outcome schema."""
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if kind == "analysis" and set(value) == {"kind", "analysis"}:
        try:
            analysis = AnalysisRequest.model_validate(value["analysis"]).model_dump(mode="json", exclude_none=True)
        except ValidationError:
            return None
        return {"kind": "analysis", "analysis": analysis}
    if (kind in {"clarification_required", "unsupported_scope"}
            and set(value) == {"kind", "message"} and isinstance(value["message"], str)
            and 0 < len(value["message"].strip()) <= 500):
        return {"kind": kind, "message": value["message"].strip()}
    return None


def _safe_usage(value: Any) -> dict[str, int | float] | None:
    if not isinstance(value, dict):
        return None
    allowed = {"input_tokens", "output_tokens", "cost_usd"}
    usage: dict[str, int | float] = {}
    for key, item in value.items():
        if key in allowed and type(item) in (int, float) and math.isfinite(item) and item >= 0:
            usage[key] = item
    return usage or None


def evaluate_cases(
    cases: list[dict[str, Any]],
    parser: Callable[[str, dict[str, Any] | None], Any],
    *, mode: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    category_totals: Counter[str] = Counter()
    category_correct: Counter[str] = Counter()
    errors = 0
    for case in cases:
        started = time.perf_counter()
        actual = None
        usage = None
        error = None
        try:
            returned = parser(case["input"]["text"], case["input"]["context"])
            if isinstance(returned, dict) and "outcome" in returned and set(returned) <= {"outcome", "usage"}:
                actual = returned["outcome"]
                usage = _safe_usage(returned.get("usage"))
            else:
                actual = returned
        except TimeoutError:
            error = "timeout"
        except Exception as exc:  # noqa: BLE001 - evaluator records candidate failures instead of aborting
            error = f"error:{type(exc).__name__}"
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        actual = _project_actual(actual)
        correct = error is None and _matches(case["expected"], actual)
        category_totals[case["category"]] += 1
        category_correct[case["category"]] += int(correct)
        errors += int(error is not None)
        results.append({
            "id": case["id"], "category": case["category"], "expected": case["expected"],
            "actual": actual, "correct": correct, "latency_ms": latency_ms,
            "usage": usage, "error": error,
        })

    total = len(results)
    correct_total = sum(item["correct"] for item in results)
    accuracy = correct_total / total if total else 0.0
    candidate_acceptance = (
        mode == "candidate"
        and dict(category_totals) == EXPECTED_CATEGORIES
        and correct_total >= MIN_ACCEPTANCE_CORRECT
        and category_correct["demonstration"] == EXPECTED_CATEGORIES["demonstration"]
        and category_correct["safety_clarification"] == EXPECTED_CATEGORIES["safety_clarification"]
        and errors == 0
    )
    return {
        "mode": mode,
        "case_count": total,
        "correct_count": correct_total,
        "accuracy": accuracy,
        "category_accuracy": {
            category: category_correct[category] / count for category, count in category_totals.items()
        },
        "error_count": errors,
        "candidate_acceptance": candidate_acceptance if mode == "candidate" else None,
        "acceptance_policy": {
            "case_count_required": sum(EXPECTED_CATEGORIES.values()),
            "category_counts_required": EXPECTED_CATEGORIES,
            "overall_correct_min": MIN_ACCEPTANCE_CORRECT,
            "demonstration_correct_required": EXPECTED_CATEGORIES["demonstration"],
            "safety_correct_required": EXPECTED_CATEGORIES["safety_clarification"],
            "errors_max": 0,
        },
        "results": results,
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--acceptance", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        cases = load_and_validate_cases(args.cases)
    except CorpusValidationError as exc:
        parser.error(str(exc))

    if args.mode == "baseline":
        report = evaluate_cases(cases, parse_intent, mode="baseline")
    else:
        # Candidate adapter is intentionally absent in this offline slice.
        def unconfigured(_text, _context):
            raise RuntimeError("candidate adapter is not configured")

        report = evaluate_cases(cases, unconfigured, mode="candidate")
        report["candidate_status"] = "unconfigured_no_provider_call_made"
    if args.acceptance and args.mode == "baseline":
        report["acceptance_note"] = "Baseline measurement is descriptive; candidate bar is not applied."
    if args.output:
        write_report(report, args.output)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if args.acceptance and args.mode == "candidate" and not report["candidate_acceptance"] else 0


if __name__ == "__main__":
    sys.exit(main())
