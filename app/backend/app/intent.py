"""Small deterministic intent baseline; this module never calls a model."""

from __future__ import annotations

import re
from typing import Any, Literal

from app.contracts import AnalysisRequest
from pydantic import ValidationError

OutcomeKind = Literal["analysis", "clarification_required", "unsupported_scope"]
SUPPORTED_YEARS = (2023, 2024, 2025)


def parse_intent(text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Parse an explicit supported request or return a safe outcome.

    This baseline intentionally recognizes a small phrase/field vocabulary. It
    does not guess airports, use fuzzy matching, or call an external service. An
    omitted year stays omitted: the server resolves it to the newest accepted
    period, exactly as it does for a model outcome with a null year.
    """
    cleaned = " ".join(text.strip().lower().split())
    if not cleaned:
        return _safe("unsupported_scope", "Enter one supported airport analysis.")
    if any(term in cleaned for term in ("profit", "profitable", "sql", "data path", "unmet demand cause")):
        return _safe("unsupported_scope", "That business claim or arbitrary data request is outside this demo's scope.")
    years = {int(year) for year in re.findall(r"\b(20\d{2})\b", cleaned)}
    if any(year not in SUPPORTED_YEARS for year in years):
        return _safe("unsupported_scope", "Only 2023, 2024 and 2025 are supported.")
    if len(years) > 1:
        return _safe("clarification_required", "Choose one supported year.")
    if len(_matching_metrics(cleaned)) > 1:
        return _safe("unsupported_scope", "Submit one analysis at a time.")

    if context and re.fullmatch(r"(?:show|compare) only (cancellations|delays|diversions)", cleaned):
        previous = _validated_context(context)
        if previous and previous.get("action") == "compare":
            metric = {"cancellations": "cancellation_rate", "delays": "departure_delay_minutes",
                      "diversions": "diversion_rate"}[cleaned.split()[-1]]
            # Keep the previous period; the server re-resolves the accepted bundle.
            previous.pop("bundle_id", None)
            return _analysis({**previous, "metric": metric})

    if re.fullmatch(r"(?:show|compare) only (cancellations|delays|diversions)", cleaned):
        return _safe("clarification_required", "Which previous airport comparison should I update?")

    try:
        analysis = _parse_explicit(cleaned)
    except ValidationError:
        return _safe("unsupported_scope", "That combination is outside the supported airport analyses.")
    if analysis is not None:
        return _analysis(analysis)

    if any(term in cleaned for term in ("which airport", "la airport", "near la", "compare airports", "compare ")):
        return _safe("clarification_required", "Name the supported airport or two airports to compare.")
    return _safe("unsupported_scope", "This demo supports only its listed airport analyses and periods.")


def _parse_explicit(text: str) -> dict[str, Any] | None:
    # UI presets and their explicit, unambiguous text equivalents.
    if text in {"new england screening", "screen new england airports", "rank new england airports by screen score"}:
        return _request("rank", "screen_score", None, region="new_england")
    if text in {"fastest new england growth", "rank new england airports by passenger growth"}:
        return _request("rank", "passenger_growth", None, region="new_england")

    year_match = re.search(r"\b(2023|2024|2025)\b", text)
    year = int(year_match.group(1)) if year_match else None

    metric = _metric(text)
    if metric is None:
        return None
    airports = _airports(text)
    is_rank = any(word in text for word in ("rank", "fastest", "screen", "screening"))
    is_compare = any(word in text for word in ("compare", " versus ", " vs ", "against", "difference"))
    if is_rank:
        if "new england" in text:
            return _request("rank", metric, year, region="new_england")
        if airports:
            return _request("rank", metric, year, airports=airports)
        return None
    if is_compare and len(airports) == 2:
        return _request("compare", metric, year, airports=airports)
    if not is_compare and len(airports) == 1:
        return _request("metric", metric, year, airports=airports,
                        threshold_miles=3000 if metric == "long_haul_share" else None)
    return None


def _metric(text: str) -> str | None:
    matches = _matching_metrics(text)
    return matches[0] if len(matches) == 1 else None


def _matching_metrics(text: str) -> list[str]:
    terms = (
        (("congestion",), "congestion"),
        (("long-haul", "long haul"), "long_haul_share"),
        (("screen score", "screening", "expansion screen"), "screen_score"),
        (("passenger growth", "growth"), "passenger_growth"),
        (("seat occupancy", "occupancy", "load factor"), "seat_occupancy"),
        (("cancellations", "cancellation rate"), "cancellation_rate"),
        (("diversions", "diversion rate"), "diversion_rate"),
        (("departure delay", "delays"), "departure_delay_minutes"),
        (("taxi-out", "taxi out"), "taxi_out_minutes"),
        (("sfo pressure", "demand pressure", "unmet demand"), "sfo_pressure"),
        (("enplaned trend", "monthly trend", "passenger trend"), "sfo_enplaned_trend"),
        (("passengers",), "passengers"),
        (("seats",), "seats"),
        (("departures",), "departures"),
    )
    matches = [metric for phrases, metric in terms if any(phrase in text for phrase in phrases)]
    return list(dict.fromkeys(matches))


_AIRPORT_PATTERNS = (
    (r"\bsanta ana\b", "SNA"), (r"\borange county\b", "SNA"),
    (r"\blos angeles\b", "LAX"), (r"\banchorage\b", "ANC"),
    (r"\bsan francisco\b", "SFO"),
)


def _airports(text: str) -> list[str]:
    normalized = text.upper()
    for pattern, code in _AIRPORT_PATTERNS:
        normalized = re.sub(pattern, code, normalized, flags=re.IGNORECASE)
    candidates = re.findall(r"\b(BDL|HVN|PWM|BGR|PQI|RKD|BHB|AUG|BOS|ACK|ORH|MVY|HYA|PVC|MHT|PSM|LEB|PVD|WST|BID|BTV|RUT|ANC|LAX|SNA|SFO)\b", normalized)
    return list(dict.fromkeys(candidates))


def _request(action: str, metric: str, year: int | None, *, region: str | None = None,
             airports: list[str] | None = None, threshold_miles: int | None = None) -> dict[str, Any]:
    return AnalysisRequest.model_validate({
        "action": action, "metric": metric, "year": year, "region": region,
        "airports": airports, "threshold_miles": threshold_miles,
    }).model_dump(mode="json", exclude_none=True)


def _analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    try:
        validated = AnalysisRequest.model_validate(analysis)
    except ValidationError:
        return _safe("unsupported_scope", "That combination is outside the supported airport analyses.")
    return {"kind": "analysis", "analysis": validated.model_dump(mode="json", exclude_none=True)}


def _validated_context(context: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return AnalysisRequest.model_validate(context).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        return None


def _safe(kind: Literal["clarification_required", "unsupported_scope"], message: str) -> dict[str, str]:
    return {"kind": kind, "message": message}
