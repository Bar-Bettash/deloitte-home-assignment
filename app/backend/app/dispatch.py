"""Deterministic routing from validated analysis requests to local calculations."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.calculations.long_haul import LongHaulResult, calculate_long_haul_share
from app.calculations.operations import (
    OperationsCalculationError,
    OperationsResult,
    calculate_operations,
)
from app.calculations.screen import NEW_ENGLAND_AIRPORTS, calculate_screen
from app.calculations.sfo import (
    SFOTrendError,
    calculate_sfo_enplaned_trend,
    calculate_sfo_pressure,
)
from app.calculations.traffic import (
    TrafficCalculationError,
    TrafficResult,
    calculate_traffic_batch,
)
from app.contracts import AnalysisRequest, AnalysisResult, MetricValue
from app.evidence import EvidenceIntegrityError, load_evidence
from app.sources.bundle import DEFAULT_DATA_ROOT as BUNDLE_DATA_ROOT
from app.sources.bundle import BundleContext, BundleError, load_bundle


class DispatchFailure(RuntimeError):
    """Safe client-facing outcome for a supported but unavailable analysis."""

    def __init__(self, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message


OPERATIONAL_KEYS = {
    "cancellation_rate": ("cancellation_rate", "percent"),
    "diversion_rate": ("diversion_rate", "percent"),
    "departure_delay_minutes": ("departure_delay_minutes", "minutes"),
    "taxi_out_minutes": ("taxi_out_minutes", "minutes"),
}
CONGESTION_KEYS = tuple(OPERATIONAL_KEYS)
# User-facing metric names. Summaries and explanations use these, never raw keys.
METRIC_LABELS = {
    "screen_score": "screening score",
    "passengers": "passengers",
    "seats": "seats",
    "departures": "departures",
    "passenger_growth": "passenger growth",
    "seat_occupancy": "seat occupancy",
    "long_haul_share": "long-haul share",
    "cancellation_rate": "cancellation rate",
    "diversion_rate": "diversion rate",
    "departure_delay_minutes": "average departure delay",
    "taxi_out_minutes": "average taxi-out time",
    "congestion": "operational congestion",
    "sfo_pressure": "SFO demand pressure",
    "sfo_enplaned_trend": "SFO passenger trend",
    "enplaned_growth": "enplaned passenger growth",
    "seat_growth": "airline seat supply growth",
}
T100_KEYS = {
    "passengers": "passengers",
    "seats": "seats",
    "departures": "performed_departures",
    "passenger_growth": "growth",
    "seat_occupancy": "occupancy_percent",
}
_NON_COHORT_ANALYSIS_AIRPORTS = {"ANC", "LAX", "SNA", "SFO"}
_REGISTRY_KEYS = {"schema_version", "default_bundle_id", "bundles"}
_REGISTRY_ENTRY_KEYS = {"bundle_id", "manifest_sha256"}
_BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BundleRegistryError(RuntimeError):
    """The server-owned accepted-bundle registry is malformed or inconsistent."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _read_accepted_bundle(
    bundle_id: str | None = None,
    *,
    data_root: Path = BUNDLE_DATA_ROOT,
) -> BundleContext | None:
    """Resolve only a hash-bound bundle listed by the server-owned registry."""
    registry_path = Path(data_root) / "bundles" / "accepted.json"
    try:
        raw = registry_path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BundleRegistryError("accepted bundle registry is unavailable") from exc
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise BundleRegistryError("accepted bundle registry is invalid") from exc
    if not isinstance(payload, dict) or set(payload) != _REGISTRY_KEYS:
        raise BundleRegistryError("accepted bundle registry has invalid keys")
    entries = payload["bundles"]
    default_id = payload["default_bundle_id"]
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or not isinstance(entries, list)
        or not entries
    ):
        raise BundleRegistryError("accepted bundle registry is incompatible")
    accepted: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _REGISTRY_ENTRY_KEYS:
            raise BundleRegistryError("accepted bundle entry has invalid keys")
        entry_id, checksum = entry["bundle_id"], entry["manifest_sha256"]
        if (
            not isinstance(entry_id, str)
            or not _BUNDLE_ID.fullmatch(entry_id)
            or not isinstance(checksum, str)
            or not _SHA256.fullmatch(checksum)
            or entry_id in accepted
        ):
            raise BundleRegistryError("accepted bundle entry is invalid")
        accepted[entry_id] = checksum
    if not isinstance(default_id, str) or default_id not in accepted:
        raise BundleRegistryError("accepted default bundle is invalid")
    selected_id = default_id if bundle_id is None else bundle_id
    if selected_id not in accepted:
        raise DispatchFailure("unsupported_scope", 422, "The requested bundle is not accepted.")
    try:
        context = load_bundle(selected_id, data_root=Path(data_root))
    except BundleError as exc:
        raise BundleRegistryError("accepted bundle cannot be loaded") from exc
    if context.manifest_sha256 != accepted[selected_id]:
        raise BundleRegistryError("accepted bundle manifest hash does not match the registry")
    return context


def _resolve_request(request: AnalysisRequest) -> tuple[AnalysisRequest, BundleContext | None]:
    if request.bundle_id is None and request.year in (2023, 2024):
        return request, None
    try:
        bundle = _read_accepted_bundle(request.bundle_id)
    except BundleRegistryError as exc:
        raise DispatchFailure("data_unavailable", 503, "Accepted bundle state is invalid.") from exc
    if bundle is None:
        if request.bundle_id is not None or request.year == 2025:
            raise DispatchFailure("unsupported_scope", 422, "The requested recent period is not accepted.")
        return request.model_copy(update={"year": 2024}), None
    selected_year = bundle.comparison_year if request.year is None else request.year
    if selected_year not in (bundle.baseline_year, bundle.comparison_year):
        raise DispatchFailure("unsupported_scope", 422, "The requested year is outside the bundle period.")
    comparison_only = request.metric in {
        "passenger_growth",
        "screen_score",
        "congestion",
        "cancellation_rate",
        "diversion_rate",
        "departure_delay_minutes",
        "taxi_out_minutes",
        "sfo_enplaned_trend",
        "sfo_pressure",
    }
    if comparison_only and selected_year != bundle.comparison_year:
        raise DispatchFailure("unsupported_scope", 422, "This workflow requires the bundle comparison year.")
    return request.model_copy(
        update={"year": selected_year, "bundle_id": bundle.bundle_id}
    ), bundle


def _validate_resolved_airports(
    request: AnalysisRequest, bundle: BundleContext | None
) -> None:
    if not request.airports:
        return
    cohort = set(bundle.cohort) if bundle is not None else set(NEW_ENGLAND_AIRPORTS)
    if request.action == "rank":
        allowed = cohort
    elif request.metric in OPERATIONAL_KEYS or request.metric == "congestion":
        allowed = {"LAX", "SNA", "SFO"}
    elif request.metric in {"sfo_enplaned_trend", "sfo_pressure"}:
        allowed = {"SFO"}
    else:
        allowed = cohort | _NON_COHORT_ANALYSIS_AIRPORTS
    if not set(request.airports) <= allowed:
        raise DispatchFailure(
            "unsupported_scope", 422, "One or more airports are outside the resolved data scope."
        )


def dispatch_analysis(
    request: AnalysisRequest,
    request_id: UUID,
    *,
    previous: AnalysisResult | None = None,
) -> AnalysisResult:
    """Run one strict structured action. Free text is not accepted here."""
    if request.action == "explain":
        if previous is None:
            raise DispatchFailure("session_expired", 409, "Start a new analysis before asking for an explanation.")
        return _explain(request, previous, request_id)

    request, bundle = _resolve_request(request)
    _validate_resolved_airports(request, bundle)
    if request.action == "rank":
        return _rank(request, request_id, bundle=bundle)
    if request.metric == "sfo_enplaned_trend":
        return _sfo_trend(request, request_id, bundle=bundle)
    if request.metric == "sfo_pressure":
        return _sfo_pressure(request, request_id, bundle=bundle)
    if request.metric in OPERATIONAL_KEYS or request.metric == "congestion":
        return _operations(request, request_id, bundle=bundle)
    if request.metric == "long_haul_share":
        return _long_haul(request, request_id, bundle=bundle)
    if request.action == "compare":
        return _traffic_compare(request, request_id, bundle=bundle)
    return _traffic_metric(request, request_id, bundle=bundle)


def _traffic_metric(
    request: AnalysisRequest, request_id: UUID, *, bundle: BundleContext | None = None
) -> AnalysisResult:
    assert request.airports and request.metric and request.year
    try:
        traffic = (
            calculate_traffic_batch(request.airports)
            if bundle is None
            else calculate_traffic_batch(request.airports, bundle=bundle)
        )
    except TrafficCalculationError as exc:
        raise DispatchFailure("data_unavailable", 503, "Qualified T-100 traffic data is unavailable.") from exc
    source = next(iter(traffic.values())).source
    source_ref = _t100_source(source)
    rows = []
    useful = False
    for airport, result in traffic.items():
        metrics = [_traffic_metric_value(request.metric, request.year, result, source_ref["id"])]
        useful |= metrics[0].status == "ok"
        rows.append({"airport": airport, "metrics": [metric.model_dump() for metric in metrics]})
    if not useful:
        raise DispatchFailure("insufficient_data", 422, "No requested T-100 metric is available for this scope.")
    return _result(
        request, request_id, rows, [source_ref],
        _availability_status(rows),
        _traffic_summary(request, rows),
        limitations=["T-100 measures reported transported traffic and supplied seats; it does not identify unmet demand."],
        bundle=bundle,
    )


def _traffic_compare(
    request: AnalysisRequest, request_id: UUID, *, bundle: BundleContext | None = None
) -> AnalysisResult:
    assert request.airports and request.metric and request.year
    try:
        traffic = (
            calculate_traffic_batch(request.airports)
            if bundle is None
            else calculate_traffic_batch(request.airports, bundle=bundle)
        )
    except TrafficCalculationError as exc:
        raise DispatchFailure("data_unavailable", 503, "Qualified T-100 traffic data is unavailable.") from exc
    sources = [_t100_source(next(iter(traffic.values())).source)]
    rows = []
    for airport, result in traffic.items():
        value = _traffic_metric_value(request.metric, request.year, result, sources[0]["id"])
        rows.append({"airport": airport, "metrics": [value.model_dump()]})
    if not any(metric["status"] == "ok" for row in rows for metric in row["metrics"]):
        raise DispatchFailure("insufficient_data", 422, "Neither airport has the requested T-100 metric available.")
    summary = _comparison_summary(request.metric, rows)
    return _result(request, request_id, rows, sources, _availability_status(rows), summary,
                   bundle=bundle,
                   limitations=["These descriptive T-100 comparisons are not evidence of terminal capacity or profitability."])


def _traffic_metric_value(metric: str, year: int, result: TrafficResult, source_id: str) -> MetricValue:
    annual = next(item for item in result.annual if item.year == year)
    if metric == "passenger_growth":
        measure = result.growth
        if measure.status != "ok" or measure.percent is None:
            return _unavailable(metric, "percent", measure.reason or "Passenger growth is unavailable.", source_id)
        baseline = result.annual[0].passengers.value
        current = result.annual[1].passengers.value
        return MetricValue(key=metric, value=measure.percent, unit="percent", status="ok",
                           numerator=current - baseline, denominator=baseline, source_ids=[source_id])
    field = T100_KEYS[metric]
    measure = getattr(annual, field)
    if measure.status != "ok" or measure.value is None:
        unit = "count" if metric in {"passengers", "seats", "departures"} else "percent"
        return _unavailable(metric, unit, measure.reason or f"{metric} is unavailable.", source_id)
    unit = "count" if metric in {"passengers", "seats", "departures"} else "percent"
    numerator = denominator = None
    if metric == "seat_occupancy":
        numerator = annual.passengers.value
        denominator = annual.seats.value
    return MetricValue(key=metric, value=measure.value, unit=unit, status="ok",
                       numerator=numerator, denominator=denominator, source_ids=[source_id])


def _long_haul(
    request: AnalysisRequest, request_id: UUID, *, bundle: BundleContext | None = None
) -> AnalysisResult:
    assert request.airports and request.metric and request.year
    threshold = float(request.threshold_miles or 3000)
    results: list[LongHaulResult] = []
    try:
        for airport in request.airports:
            result = (
                calculate_long_haul_share(airport, request.year, threshold)
                if bundle is None
                else calculate_long_haul_share(
                    airport, request.year, threshold, bundle=bundle
                )
            )
            results.append(result)
    except TrafficCalculationError as exc:
        raise DispatchFailure("data_unavailable", 503, "Qualified T-100 distance data is unavailable.") from exc
    if len({item.source.snapshot_id for item in results}) != 1:
        raise DispatchFailure("data_unavailable", 503, "The T-100 snapshot changed during this request. Retry once.")
    source = _t100_source(results[0].source)
    rows = []
    for item in results:
        typed_counts = (
            item.long_haul_departures is not None
            and item.total_departures is not None
            and item.unknown_distance_departures is not None
        )
        if typed_counts:
            try:
                metric = MetricValue(
                    key="long_haul_share",
                    value=item.share_percent,
                    unit="percent",
                    status="ok" if item.status == "ok" else "unavailable",
                    numerator=item.long_haul_departures,
                    denominator=item.total_departures,
                    unknown_distance_departures=item.unknown_distance_departures,
                    lower_percent=item.lower_percent,
                    upper_percent=item.upper_percent,
                    reason=item.reason if item.status != "ok" else None,
                    source_ids=[source["id"]],
                )
            except ValueError as exc:
                raise DispatchFailure(
                    "data_unavailable", 503, "Long-haul calculation output is inconsistent."
                ) from exc
        else:
            metric = _unavailable(
                "long_haul_share",
                "percent",
                item.reason or "Long-haul share is unavailable.",
                source["id"],
            )
        rows.append({"airport": item.airport, "metrics": [metric.model_dump()]})
    if not any(
        item.status == "ok" or item.unknown_distance_departures is not None
        for item in results
    ):
        raise DispatchFailure("insufficient_data", 422, "Long-haul share is unavailable for this scope.")
    return _result(request, request_id, rows, [source], _availability_status(rows),
                   _long_haul_summary(rows, threshold),
                   threshold_miles=threshold,
                   bundle=bundle,
                   limitations=[_LONG_HAUL_SCOPE,
                                "The T-100 endpoint-distance share is descriptive and does not identify demand or profitability."])


# Matches the T-100 filter: service class F (scheduled passenger/cargo) with seats > 0.
_LONG_HAUL_SCOPE = ("Long-haul share is based on scheduled passenger-service departures with reported seats; "
                    "cargo-only and charter operations are outside this measure.")


def _operations(
    request: AnalysisRequest, request_id: UUID, *, bundle: BundleContext | None = None
) -> AnalysisResult:
    assert request.airports and request.metric
    outputs: list[OperationsResult] = []
    try:
        for airport in request.airports:
            result = (
                calculate_operations(airport)
                if bundle is None
                else calculate_operations(airport, year=request.year, bundle=bundle)
            )
            outputs.append(result)
    except OperationsCalculationError as exc:
        raise DispatchFailure(
            "data_unavailable",
            503,
            f"Qualified {request.year} on-time data is unavailable for this scope.",
        ) from exc
    if len({item.source.snapshot_id for item in outputs}) != 1:
        raise DispatchFailure("data_unavailable", 503, "The on-time snapshot changed during this request. Retry once.")
    source = _operations_source(outputs[0])
    keys = CONGESTION_KEYS if request.metric == "congestion" else (request.metric,)
    rows = []
    for output in outputs:
        metrics = [_operation_metric(output, key, source["id"]) for key in keys]
        rows.append({"airport": output.airport, "metrics": [metric.model_dump() for metric in metrics]})
    if request.action == "compare":
        _annotate_comparison_direction(rows)
    if not any(metric["status"] == "ok" for row in rows for metric in row["metrics"]):
        raise DispatchFailure(
            "insufficient_data",
            422,
            f"No requested {request.year} operational indicator is available.",
        )
    return _result(request, request_id, rows, [source], _availability_status(rows),
                   _comparison_summary(request.metric, rows) if request.action == "compare" else _operation_summary(rows, request.year, request.metric),
                   bundle=bundle,
                   limitations=[outputs[0].population,
                                "Delay and taxi means exclude cancelled/diverted flights; operational indicators do not prove terminal causation.",
                                *[_operations_coverage(item) for item in outputs]])


def _operation_metric(result: OperationsResult, key: str, source_id: str) -> MetricValue:
    field, unit = OPERATIONAL_KEYS[key]
    value = getattr(result, field)
    if value.status != "ok" or value.value is None:
        return MetricValue(key=key, value=None, unit=unit, status="unavailable",
                           numerator=value.numerator, denominator=value.denominator,
                           eligible_count=value.eligible_count,
                           reason=(value.reason or f"{key} is unavailable.")[:300], source_ids=[source_id])
    return MetricValue(key=key, value=value.value, unit=unit, status="ok",
                       numerator=value.numerator, denominator=value.denominator,
                       eligible_count=value.eligible_count, source_ids=[source_id])


def _annotate_comparison_direction(rows: list[dict[str, Any]]) -> None:
    if len(rows) != 2:
        return
    for left, right in zip(rows[0]["metrics"], rows[1]["metrics"]):
        if left["value"] is None or right["value"] is None:
            left_direction = right_direction = "unavailable"
        elif left["value"] == right["value"]:
            left_direction = right_direction = "tied"
        elif left["value"] > right["value"]:
            left_direction, right_direction = "higher", "lower"
        else:
            left_direction, right_direction = "lower", "higher"
        left["comparison_direction"] = left_direction
        right["comparison_direction"] = right_direction


def _operations_coverage(result: OperationsResult) -> str:
    observed = ",".join(f"{month:02d}" for month in result.observed_months) or "none"
    missing = ",".join(f"{month:02d}" for month in result.missing_months) or "none"
    carriers = ", ".join(result.carriers) or "none"
    if len(carriers) > 250:
        carriers = carriers[:247].rsplit(",", 1)[0] + ",..."
    text = (f"{result.airport} {result.year} on-time coverage: observed months {observed}; missing months {missing}; "
            f"reporting carriers {carriers}; valid scheduled records {result.scheduled_count}; "
            f"invalid rows {result.invalid_row_count}.")
    return text[:500]


def _rank(
    request: AnalysisRequest, request_id: UUID, *, bundle: BundleContext | None = None
) -> AnalysisResult:
    assert request.metric and request.year
    reference_cohort = set(bundle.cohort) if bundle is not None else set(NEW_ENGLAND_AIRPORTS)
    selected = sorted(reference_cohort) if request.region == "new_england" else request.airports
    assert selected
    try:
        traffic = (
            calculate_traffic_batch(sorted(reference_cohort))
            if bundle is None
            else calculate_traffic_batch(sorted(reference_cohort), bundle=bundle)
        )
    except TrafficCalculationError as exc:
        raise DispatchFailure("data_unavailable", 503, "Qualified New England T-100 data is unavailable.") from exc
    screen = calculate_screen(
        traffic.values(),
        sort_by="passenger_growth" if request.metric == "passenger_growth" else "screen_score",
        bundle=bundle,
    )
    if request.metric == "screen_score" and (screen.status != "ok" or not screen.rows):
        raise DispatchFailure("insufficient_data", 422, screen.reason or "Fewer than two airports are assessable.")
    selected_set = set(selected)
    screen_by_airport = {row.airport: row for row in screen.rows}
    annual_by_airport = {
        airport: {annual.year: annual for annual in result.annual}[request.year]
        for airport, result in traffic.items()
    }
    rank_values = {
        airport: (
            int(annual.passengers.value)
            if request.metric == "passengers" and annual.passengers.status == "ok"
            else annual.occupancy_percent.value
            if request.metric == "seat_occupancy" and annual.occupancy_percent.status == "ok"
            else None
        )
        for airport, annual in annual_by_airport.items()
    }
    growth_values = {
        airport: result.growth.percent if result.growth.status == "ok" else None
        for airport, result in traffic.items()
    }
    if request.metric == "screen_score":
        ordered = [row.airport for row in screen.rows if row.airport in selected_set]
        ranks = {row.airport: row.rank for row in screen.rows}
    elif request.metric in {"passengers", "seat_occupancy"}:
        rankable = [airport for airport, value in rank_values.items() if value is not None]
        ordered = sorted((airport for airport in rankable if airport in selected_set),
                         key=lambda airport: (-rank_values[airport], airport))
        ranks = {
            airport: 1 + sum(rank_values[other] > rank_values[airport]
                             for other in rankable if other != airport)
            for airport in rankable
        }
    elif request.metric == "passenger_growth":
        rankable = [airport for airport, value in growth_values.items() if value is not None]
        ordered = sorted((airport for airport in rankable if airport in selected_set),
                         key=lambda airport: (-growth_values[airport], airport))
        ranks = {
            airport: 1 + sum(growth_values[other] > growth_values[airport]
                             for other in rankable if other != airport)
            for airport in rankable
        }
    else:
        raise DispatchFailure("unsupported_scope", 422, "Unsupported ranking metric.")
    if not ordered:
        raise DispatchFailure("insufficient_data", 422, "No airports in the requested ranking scope have assessable metric data.")
    rows = []
    for airport in ordered:
        item = screen_by_airport.get(airport)
        traffic_result = traffic[airport]
        source_id = _traffic_id(traffic_result.source.snapshot_id)
        annual = annual_by_airport[airport]
        passengers_value = int(annual.passengers.value) if annual.passengers.status == "ok" else None
        occupancy_value = annual.occupancy_percent.value if annual.occupancy_percent.status == "ok" else None
        seats_value = int(annual.seats.value) if annual.seats.status == "ok" else None
        growth_value = traffic_result.growth.percent
        level_metrics = {
            "passengers": MetricValue(key="passengers", value=passengers_value, unit="count",
                                      status="ok" if passengers_value is not None else "unavailable",
                                      reason=None if passengers_value is not None else annual.passengers.reason,
                                      source_ids=[source_id]),
            "seat_occupancy": MetricValue(key="seat_occupancy", value=occupancy_value, unit="percent",
                                          status="ok" if occupancy_value is not None else "unavailable",
                                          numerator=passengers_value, denominator=seats_value,
                                          reason=None if occupancy_value is not None else annual.occupancy_percent.reason,
                                          source_ids=[source_id]),
        }
        baseline_year = bundle.baseline_year if bundle is not None else 2023
        comparison_year = bundle.comparison_year if bundle is not None else 2024
        if request.year == baseline_year:
            metrics = [level_metrics[request.metric]]
        else:
            metrics = [
                MetricValue(key="screen_score", value=item.screen_score if item else None, unit="score",
                            status="ok" if item else "unavailable",
                            reason=None if item else f"Incomplete {comparison_year} screen inputs; no frozen-cohort score is available.",
                            source_ids=[source_id]),
                level_metrics["passengers"],
                MetricValue(key="passenger_growth", value=growth_value, unit="percent",
                            status="ok" if growth_value is not None else "unavailable",
                            reason=None if growth_value is not None else traffic_result.growth.reason,
                            source_ids=[source_id]),
                level_metrics["seat_occupancy"],
            ]
            if request.metric == "screen_score" and item is not None:
                # The weighted parts of the existing score, so a reader can see why it ranks.
                metrics += [
                    MetricValue(key=key, value=value, unit="score", status="ok", source_ids=[source_id])
                    for key, value in (("growth_points", item.growth_points),
                                       ("volume_points", item.volume_points),
                                       ("occupancy_points", item.occupancy_points))
                ]
        rows.append({"airport": airport, "rank": ranks[airport], "metrics": [metric.model_dump() for metric in metrics]})
    source = _t100_source(next(iter(traffic.values())).source)
    evidence, evidence_sources, evidence_limitations = _rank_evidence(
        [row["airport"] for row in rows[:3]], bundle=bundle
    )
    if request.metric == "screen_score":
        exclusions = [f"{item.airport}: {item.reason}" for item in screen.exclusions
                      if item.airport in selected_set]
    elif request.metric == "passenger_growth":
        exclusions = [
            f"{airport}: {traffic[airport].growth.reason or 'passenger growth is unavailable'}"
            for airport in sorted(selected_set) if growth_values[airport] is None
        ]
    else:
        exclusions = []
        for airport in sorted(selected_set):
            annual = annual_by_airport[airport]
            result = annual.passengers if request.metric == "passengers" else annual.occupancy_percent
            if result.status != "ok":
                exclusions.append(f"{airport}: {result.reason or f'{request.metric} is unavailable'}")
    summary = _rank_summary(request.metric, request.year, rows)
    has_unavailable_values = any(metric["status"] != "ok" for row in rows for metric in row["metrics"])
    unreviewed = any(item.endswith(_NOT_REVIEWED_SUFFIX) for item in evidence_limitations)
    return _result(request, request_id, rows, [source, *evidence_sources],
                   "partial" if exclusions or has_unavailable_values or unreviewed else "ok", summary,
                   bundle=bundle,
                   evidence=evidence, exclusions=exclusions,
                   limitations=[_SCREEN_LIMITATION if request.metric == "screen_score" else
                                "Rankings describe reported traffic, not terminal capacity or investment success.",
                                *evidence_limitations])


def _rank_evidence(
    top_airports: list[str], *, bundle: BundleContext | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    try:
        evidence_bundle = load_evidence() if bundle is None else load_evidence(bundle=bundle)
    except (EvidenceIntegrityError, OSError, ValueError) as exc:
        if bundle is not None:
            raise DispatchFailure(
                "data_unavailable", 503, "Accepted bundle evidence is unavailable or inconsistent."
            ) from exc
        return [], [], ["Curated terminal evidence is unavailable; no terminal disposition is inferred."]
    source_by_id = {item.source_id: item for item in evidence_bundle.sources}
    reviewed = {item.airport: item for item in evidence_bundle.airports}
    evidence, sources, limitations = [], [], []
    used: set[str] = set()
    for airport in top_airports:
        note = reviewed.get(airport)
        if note is None:
            limitations.append(f"Terminal project evidence for {airport}{_NOT_REVIEWED_SUFFIX}")
            continue
        for observation in note.observations:
            source = source_by_id[observation.source_id]
            if source.source_id not in used:
                sources.append({"id": source.source_id, "name": f"{source.publisher}: {source.title}",
                                "url": str(source.url), "snapshot_id": f"curated-{source.source_id}",
                                "period": source.source_date.isoformat() if source.source_date else "undated",
                                "retrieved_at": None})
                used.add(source.source_id)
            evidence.append({"source_id": source.source_id, "locator": observation.locator,
                             "date": (source.source_date or source.checked_at).isoformat(),
                             "claim": _evidence_claim(observation.fact, note.status)[:500],
                             "limitation": note.limitations[0]})
        limitations.extend(note.limitations)
    return evidence, sources, list(dict.fromkeys(limitations))


def _sfo_trend(
    request: AnalysisRequest, request_id: UUID, *, bundle: BundleContext | None = None
) -> AnalysisResult:
    try:
        trend = (
            calculate_sfo_enplaned_trend()
            if bundle is None
            else calculate_sfo_enplaned_trend(bundle=bundle)
        )
    except SFOTrendError as exc:
        raise DispatchFailure("data_unavailable", 503, "SFO passenger data is unavailable or incomplete. Refresh the accepted snapshot and try again.") from exc
    source_id = f"datasf-{trend.source.dataset_id}"
    annual = {item.year: item.passengers for item in trend.annual_totals}
    baseline_year = bundle.baseline_year if bundle is not None else 2023
    comparison_year = bundle.comparison_year if bundle is not None else 2024
    metrics = [
        MetricValue(key="passengers", value=annual[comparison_year], unit="count", status="ok", source_ids=[source_id]),
        MetricValue(key="passenger_growth", value=trend.growth.percent, unit="percent", status=trend.growth.status,
                    numerator=annual[comparison_year] - annual[baseline_year], denominator=annual[baseline_year], source_ids=[source_id],
                    reason=trend.growth.reason),
    ]
    return _result(request, request_id, [{"airport": "SFO", "metrics": [item.model_dump() for item in metrics]}],
                   [_datasf_source(trend.source, source_id)],
                   "partial" if trend.growth.status == "unavailable" else "ok",
                   f"SFO recorded {annual[baseline_year]:,} enplaned passengers in {baseline_year} and {annual[comparison_year]:,} in {comparison_year}. " +
                   (f"That is {trend.growth.percent:.2f}% growth." if trend.growth.percent is not None else trend.growth.reason or "Growth is unavailable."),
                   bundle=bundle,
                   series=[{"period": item.period, "value": item.passengers, "unit": "count", "status": "ok"} for item in trend.series],
                   limitations=["Passenger trends do not identify unmet demand or its cause."])


def _sfo_pressure(
    request: AnalysisRequest, request_id: UUID, *, bundle: BundleContext | None = None
) -> AnalysisResult:
    pressure = (
        calculate_sfo_pressure()
        if bundle is None
        else calculate_sfo_pressure(bundle=bundle)
    )
    if not pressure.lineage:
        raise DispatchFailure("data_unavailable", 503, "No accepted SFO source snapshot is available.")
    source_by_key = {item.source_id: _lineage_source(item) for item in pressure.lineage}
    sources = list(source_by_key.values())
    metrics: list[MetricValue] = []
    series = []
    comparison_year = bundle.comparison_year if bundle is not None else 2024
    if pressure.enplaned_trend.result is not None:
        trend = pressure.enplaned_trend.result
        enplaned_id = source_by_key["datasf"]["id"]
        annual = {item.year: item.passengers for item in trend.annual_totals}
        metrics.append(MetricValue(key="sfo_enplaned_trend", value=annual[comparison_year], unit="count", status="ok", source_ids=[enplaned_id]))
        baseline = annual[trend.growth.baseline_year]
        metrics.append(MetricValue(key="enplaned_growth", value=trend.growth.percent, unit="percent",
                                   status=trend.growth.status, numerator=annual[comparison_year] - baseline,
                                   denominator=baseline, source_ids=[enplaned_id], reason=trend.growth.reason))
        series = [{"period": item.period, "value": item.passengers, "unit": "count", "status": "ok"} for item in trend.series]
    if pressure.t100_traffic.result is not None:
        traffic = pressure.t100_traffic.result
        source_id = source_by_key["t100"]["id"]
        for key in ("passengers", "seats", "departures"):
            metrics.append(_traffic_metric_value(key, comparison_year, traffic, source_id))
        metrics.append(_traffic_metric_value("passenger_growth", comparison_year, traffic, source_id))
        metrics.append(_traffic_metric_value("seat_occupancy", comparison_year, traffic, source_id))
    if pressure.operations.result is not None:
        operation = pressure.operations.result
        source_id = source_by_key["ontime"]["id"]
        metrics.extend(_operation_metric(operation, key, source_id) for key in CONGESTION_KEYS)
    gap_source = source_by_key.get("t100", {}).get("id")
    metrics.append(MetricValue(key="sfo_pressure", value=pressure.growth_gap_pp.value, unit="percentage_points",
                              status=pressure.growth_gap_pp.status, source_ids=[gap_source] if gap_source else [],
                              reason=pressure.growth_gap_pp.reason or (None if gap_source else "Matched T-100 data is unavailable.")))
    # Seat growth comes from the same T-100 periods as the gap (calculations/sfo.py), so
    # passenger growth minus seat growth equals the gap; the browser never derives it.
    seats = pressure.seat_growth
    metrics.append(MetricValue(key="seat_growth", value=seats.value, unit="percent", status=seats.status,
                               numerator=seats.numerator, denominator=seats.denominator,
                               source_ids=[gap_source] if gap_source else [],
                               reason=seats.reason or (None if gap_source else "Matched T-100 data is unavailable.")))
    available = sum(item.status == "ok" for item in metrics)
    if available == 0:
        raise DispatchFailure("insufficient_data", 422, "No requested SFO pressure indicator is available.")
    evidence, evidence_sources, evidence_limits = _rank_evidence(["SFO"], bundle=bundle)
    for source in evidence_sources:
        if source["id"] not in {item["id"] for item in sources}:
            sources.append(source)
    by_key = {item.key: item for item in metrics}
    answer = _sfo_answer(by_key, comparison_year)
    causes = pressure.operations.result.delay_causes if pressure.operations.result is not None else None
    cause_sentence = _delay_cause_sentence("SFO", causes)
    summary = (" ".join(item for item in (answer, cause_sentence) if item) if answer else
               "SFO transported-traffic growth, occupancy and operational indicators are descriptive only; "
               "precise unmet demand is not identifiable.")
    return _result(request, request_id, [{"airport": "SFO", "metrics": [item.model_dump() for item in metrics]}],
                   sources, "partial" if pressure.status != "ok" or any(item.status != "ok" for item in metrics) else "ok",
                   summary,
                   bundle=bundle,
                   series=series, evidence=evidence,
                   limitations=[pressure.limitation, "Profitability and quantitative unmet demand cannot be identified from these data.",
                                *([_DELAY_CAUSE_SCOPE] if cause_sentence else []), *evidence_limits])


_DELAY_CAUSE_SCOPE = ("Delay-cause shares use the minutes reporting carriers attribute to five BTS causes on flights "
                      "that arrived 15 or more minutes late; they explain reported delays, not latent demand or terminal capacity.")
_DELAY_CAUSE_LABELS = {
    "carrier_delay": "carrier issues",
    "weather_delay": "extreme weather",
    "nas_delay": "national airspace system factors such as air traffic control and traffic volume",
    "security_delay": "security",
    "late_aircraft_delay": "late-arriving aircraft",
}


def _sfo_answer(by_key: dict[str, MetricValue], year: int) -> str | None:
    """A direct answer from the growth gap: seats outpacing passengers is not a seat shortage."""
    gap, growth, occupancy = by_key.get("sfo_pressure"), by_key.get("passenger_growth"), by_key.get("seat_occupancy")
    if gap is None or gap.value is None or growth is None or growth.value is None:
        return None
    occupied = (f", and seat occupancy was {_format_value(occupancy.value, occupancy.unit)}"
                if occupancy is not None and occupancy.value is not None else "")
    points = f"{abs(gap.value):.2f}"
    passengers = f"passenger growth {_format_value(growth.value, growth.unit)}"
    if round(gap.value, 2) < 0:
        return (f"No sign in {year} that airline seat supply at SFO fell behind passenger traffic: supplied seats grew "
                f"{points} percentage points faster than transported passengers ({passengers}){occupied}. "
                "That does not rule out latent demand, because traffic data counts only passengers who flew.")
    if round(gap.value, 2) > 0:
        return (f"Passenger traffic at SFO outpaced airline seat supply in {year}: passengers grew {points} percentage "
                f"points faster than supplied seats ({passengers}){occupied}. That is a demand-pressure signal, "
                "not a measured unmet demand; traffic data counts only passengers who flew.")
    return (f"Passenger traffic and airline seat supply at SFO grew at the same pace in {year} ({passengers}){occupied}. "
            "Traffic data counts only passengers who flew, so latent demand cannot be measured.")


def _delay_cause_sentence(airport: str, mix) -> str | None:
    """Name the causes behind most attributed delay minutes, with their exact shares."""
    if mix is None or mix.status != "ok" or not mix.causes:
        return None
    ordered = sorted(mix.causes, key=lambda item: (-item[2], item[0]))
    named = [f"{_DELAY_CAUSE_LABELS[field]} ({share:.1f}%)" for field, _minutes, share in ordered if share >= 5]
    minor = [f"{_DELAY_CAUSE_LABELS[field]} {share:.1f}%" for field, _minutes, share in ordered if share < 5]
    if not named:
        return None
    text = f"Reported delays on {airport} departures were mostly attributed to {_join_and(named)}"
    if minor:
        text += f"; {_join_and(minor)}"
    return text + ". These shares explain operational delays, not latent demand or terminal capacity."


_SUMMARY_LIMIT = 2000
_NOT_REVIEWED_SUFFIX = " has not been reviewed."


def _explain(request: AnalysisRequest, previous: AnalysisResult, request_id: UUID) -> AnalysisResult:
    """A short deterministic narrative: what was measured, how, the takeaway, and what it cannot prove."""
    selected = request.airports or previous.scope.airports
    if not set(selected) <= set(previous.scope.airports):
        raise DispatchFailure("unsupported_scope", 422, "Choose only airports in the referenced result.")
    rows = [row for row in previous.rows if row.airport in selected]
    metric = previous.scope.metric
    if metric in ("screen_score", "passenger_growth") and any(row.rank is not None for row in rows):
        sentences = _explain_ranking(metric, rows, previous)
    elif metric == "congestion":
        sentences = _explain_congestion(rows)
    elif metric == "long_haul_share":
        sentences = _explain_long_haul(rows, previous.scope.threshold_miles)
    elif metric == "sfo_pressure":
        sentences = _explain_sfo_pressure(rows, previous.scope)
    else:
        sentences = _explain_values(metric, rows)
    summary = " ".join(_sentence(item) for item in sentences if item)
    return previous.model_copy(
        update={"request_id": request_id, "summary": summary[:_SUMMARY_LIMIT]}, deep=True
    )


def _metric_label(key: str) -> str:
    return METRIC_LABELS.get(key, key.replace("_", " "))


def _find_metric(row, key: str) -> MetricValue | None:
    return next((item for item in row.metrics if item.key == key), None)


def _shown(metric: MetricValue | None) -> str | None:
    return None if metric is None or metric.value is None else _format_value(metric.value, metric.unit)


def _explain_ranking(metric: str, rows, previous: AnalysisResult) -> list[str]:
    ranked = sorted((row for row in rows if row.rank is not None), key=lambda row: (row.rank, row.airport))
    leaders = [(row.airport, _shown(_find_metric(row, metric))) for row in ranked[:3]]
    named = [f"{airport} ({value})" if value else airport for airport, value in leaders]
    if metric == "screen_score":
        sentences = ["This screen ranks New England airports on three traffic-pressure signals: passenger growth "
                     "(40%), passenger volume (30%) and seat occupancy (30%), each scored against the other eligible airports"]
    else:
        sentences = ["This ranking orders New England airports by raw passenger growth between the two comparison years"]
    if named:
        lead = f"{named[0]} ranked highest"
        rest = named[1:]
        if rest:
            lead += f", followed by {' and '.join(rest)}"
        sentences.append(f"{lead}, out of {len(ranked)} assessed airports")
    if metric == "screen_score" and len(ranked) > 1 and ranked[0].rank == 1:
        # Component points for every airport are shown in the ranking itself.
        sentences.append(_screen_leader_reason(ranked[0], ranked[1]))
    if previous.exclusions:
        count = len(previous.exclusions)
        sentences.append(f"{count} airport{'s were' if count > 1 else ' was'} excluded because required data was incomplete")
    if metric == "screen_score":
        sentences.append(_SCREEN_LIMITATION)
    else:
        sentences.append("Growth describes transported traffic; it does not show terminal capacity, unmet demand or profitability")
    return sentences


def _explain_congestion(rows) -> list[str]:
    sentences = ["The comparison uses four operational indicators from scheduled domestic departures: cancellation rate, "
                 "diversion rate, average departure delay and average taxi-out time"]
    if len(rows) == 2:
        sentences.append(_comparison_summary("congestion", [row.model_dump() for row in rows]))
    sentences.append("These measures describe day-to-day operations, not terminal capacity or investment return")
    return sentences


def _explain_long_haul(rows, threshold_miles) -> list[str]:
    threshold = f"{int(threshold_miles or 3000):,}"
    sentences = [f"Long-haul share is the percentage of eligible performed departures whose route distance is at least {threshold} miles"]
    for row in rows:
        share = _find_metric(row, "long_haul_share")
        if share is None:
            continue
        if share.value is not None and share.numerator is not None and share.denominator is not None:
            sentences.append(f"{row.airport} had {int(share.numerator):,} such departures out of {int(share.denominator):,} "
                             f"eligible departures, or {_format_value(share.value, share.unit)}")
        else:
            sentences.append(f"{row.airport}: {_explain_metric(share)}")
    sentences.append("This describes route mix; it does not measure profitability or latent demand")
    return sentences


def _explain_sfo_pressure(rows, scope) -> list[str]:
    row = rows[0] if rows else None
    by_key = {item.key: item for item in row.metrics} if row else {}
    gap, growth, occupancy = by_key.get("sfo_pressure"), by_key.get("passenger_growth"), by_key.get("seat_occupancy")
    sentences = []
    if gap is not None and gap.value is not None and growth is not None and growth.value is not None:
        # The gap is defined as passenger growth minus seat growth (calculations/sfo.py);
        # the returned seat-growth metric is used when present.
        returned = by_key.get("seat_growth")
        seat_growth = returned.value if returned is not None and returned.value is not None else growth.value - gap.value
        sentences.append(f"The answer compares two {scope.year} growth rates from BTS T-100: transported passengers grew "
                         f"{_format_value(growth.value, 'percent')} and supplied seats grew {_format_value(seat_growth, 'percent')}, "
                         f"a gap of {_format_value(gap.value, 'percentage_points')}"
                         + (f"; seat occupancy was {_format_value(occupancy.value, occupancy.unit)}"
                            if occupancy is not None and occupancy.value is not None else ""))
        if round(gap.value, 2) < 0:
            sentences.append("Airlines added seats faster than passengers arrived, so these data show no seat shortage")
        elif round(gap.value, 2) > 0:
            sentences.append("Passengers arrived faster than airlines added seats, which is a demand-pressure signal")
    sentences.append("Traffic data count only passengers who flew, so latent demand is neither measured nor ruled out")
    sentences.append(_delay_cause_sentence("SFO", _sfo_delay_causes(scope)))
    return sentences


def _sfo_delay_causes(scope):
    """Recompute the SFO delay-cause mix for the explained result's own period."""
    try:
        bundle = _read_accepted_bundle(scope.bundle_id) if scope.bundle_id is not None else None
        result = (calculate_operations("SFO") if bundle is None
                  else calculate_operations("SFO", year=scope.year, bundle=bundle))
    except (BundleRegistryError, DispatchFailure, OperationsCalculationError):
        return None
    return result.delay_causes


def _explain_values(metric: str, rows) -> list[str]:
    label = _metric_label(metric)
    values = [(row.airport, _find_metric(row, metric)) for row in rows]
    shown = [f"{airport} {_explain_metric(value, labelled=False)}" for airport, value in values if value is not None]
    sentences = []
    if metric == "sfo_enplaned_trend":
        sentences.append("This is the monthly count of passengers boarding flights at SFO, from DataSF")
        total = _shown(_find_metric(rows[0], "passengers")) if rows else None
        growth = _shown(_find_metric(rows[0], "passenger_growth")) if rows else None
        if total and growth:
            sentences.append(f"The latest full year totals {total} enplaned passengers, a change of {growth} on the year before")
        return [*sentences, "Passenger counts describe transported traffic; they do not show unmet demand or its cause"]
    elif metric in OPERATIONAL_KEYS:
        sentences.append(f"{label[0].upper()}{label[1:]} is measured over scheduled domestic departures by reporting carriers")
    else:
        sentences.append(f"{label[0].upper()}{label[1:]} comes from reported T-100 traffic")
    counted = [(airport, value) for airport, value in values if value is not None and value.unit == "percent"
               and value.value is not None and value.numerator is not None and value.denominator is not None]
    if metric in OPERATIONAL_KEYS and counted:
        outcome = {"cancellation_rate": " were cancelled", "diversion_rate": " were diverted"}.get(metric, "")
        sentences += [f"{airport}: {int(value.numerator):,} of {int(value.denominator):,} scheduled departures{outcome} "
                      f"({_format_value(value.value, value.unit)})" for airport, value in counted]
    elif shown:
        sentences.append(f"{label[0].upper()}{label[1:]}: {', '.join(shown)}")
    sentences.append("These figures describe reported traffic and operations; they do not identify unmet demand, "
                     "terminal capacity or profitability")
    return sentences


def _format_value(value: float, unit: str) -> str:
    if unit == "count":
        return f"{int(value):,}"
    if unit == "minutes":
        return f"{value:,.1f} min"
    rounded = round(value, 2)
    text = f"{abs(rounded):,.2f}".rstrip("0").rstrip(".")
    sign = "-" if rounded < 0 else ("+" if unit == "percentage_points" and rounded > 0 else "")
    suffix = {"percent": "%", "percentage_points": " pp"}.get(unit, "")
    return f"{sign}{text}{suffix}"


def _sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _explain_metric(metric: MetricValue, *, labelled: bool = True) -> str:
    label = metric.key.replace("_", " ") if labelled else ""
    if metric.value is not None:
        return f"{label} {_format_value(metric.value, metric.unit)}".strip()
    if (
        metric.key == "long_haul_share"
        and metric.status == "ok"
        and metric.lower_percent is not None
        and metric.upper_percent is not None
    ):
        unknown = metric.unknown_distance_departures or 0
        return (
            f"{label} between {_format_value(metric.lower_percent, 'percent')} and "
            f"{_format_value(metric.upper_percent, 'percent')} ({unknown:,} departures have unknown distance)"
        ).strip()
    return f"{label} unavailable".strip()


def _result(request, request_id, rows, sources, status, summary, *, series=None, evidence=None,
            exclusions=None, limitations=None, threshold_miles=None,
            bundle: BundleContext | None = None) -> AnalysisResult:
    airports = request.airports or sorted(bundle.cohort if bundle is not None else NEW_ENGLAND_AIRPORTS)
    scope = {
        "airports": airports,
        "year": request.year,
        "metric": request.metric,
        "threshold_miles": threshold_miles if threshold_miles is not None else request.threshold_miles,
        "population": _population(request, bundle=bundle),
    }
    if bundle is not None:
        scope.update({
            "bundle_id": bundle.bundle_id,
            "baseline_year": bundle.baseline_year,
            "comparison_year": bundle.comparison_year,
        })
    return AnalysisResult.model_validate({
        "result_id": uuid4(), "request_id": request_id, "status": status,
        "scope": scope,
        "rows": rows, "summary": summary, "series": series or [], "sources": sources,
        "evidence": evidence or [], "exclusions": exclusions or [], "limitations": limitations or [],
    })


def _population(
    request: AnalysisRequest, *, bundle: BundleContext | None = None
) -> str:
    baseline_year = bundle.baseline_year if bundle is not None else 2023
    comparison_year = bundle.comparison_year if bundle is not None else 2024
    if request.metric in OPERATIONAL_KEYS or request.metric == "congestion":
        return f"Domestic reporting-carrier scheduled departures at origin; {comparison_year} only"
    if request.metric in {"sfo_enplaned_trend", "sfo_pressure"}:
        return "SFO Enplaned passengers combined Domestic and International; T-100 and on-time populations separately identified"
    if request.action == "rank":
        if request.metric == "screen_score":
            return f"New England airports normalized against the frozen eligible {comparison_year} cohort"
        if request.metric == "passenger_growth":
            return (f"Raw {comparison_year} versus {baseline_year} passenger growth; rank positions "
                    "against the full growth-eligible New England cohort")
        label = "passengers" if request.metric == "passengers" else "seat occupancy"
        return (f"Raw BTS T-100 {label} for CY{request.year}; rank positions against the full eligible "
                "New England cohort with valid requested-year values")
    return (f"BTS T-100 scheduled passenger origin traffic; {baseline_year} and "
            f"{comparison_year} where applicable")


def _availability_status(rows) -> str:
    return "partial" if any(metric["status"] != "ok" for row in rows for metric in row["metrics"]) else "ok"


def _traffic_summary(request, rows) -> str:
    return _comparison_summary(request.metric, rows) if request.action == "compare" else _values_summary(request.metric, rows, request.year)


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _join_and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _row_value(row, key: str) -> str | None:
    metric = next((item for item in row["metrics"] if item["key"] == key), None)
    return None if metric is None or metric["value"] is None else _format_value(metric["value"], metric["unit"])


def _values_summary(metric, rows, year) -> str:
    label = _metric_label(metric)
    parts = [f"{row['airport']} {value}" for row in rows if (value := _row_value(row, metric)) is not None]
    if not parts:
        return f"{_capitalized(label)} is unavailable for {', '.join(row['airport'] for row in rows)} in {year}."
    return f"{_capitalized(label)} in {year}: {', '.join(parts)}."


def _rank_summary(metric, year, rows) -> str:
    ranked = sorted((row for row in rows if row.get("rank") is not None), key=lambda row: (row["rank"], row["airport"]))
    if not ranked:
        return f"No airport could be ranked on {_metric_label(metric)} in {year}."
    subset = ranked[0]["rank"] != 1
    named = [f"{row['airport']} ({'#' + str(row['rank']) + ', ' if subset else ''}{_row_value(row, metric) or 'unavailable'})"
             for row in ranked[:3]]
    period = "" if metric in ("screen_score", "passenger_growth") else f" in {year}"
    basis = {"screen_score": "scores are normalized against the full eligible cohort",
             "passenger_growth": "ranks use raw growth across the full eligible cohort"}.get(
        metric, "rank positions are against the full eligible cohort")
    summary = f"Top of {len(ranked)} ranked airports on {_metric_label(metric)}{period}: {', '.join(named)}; {basis}."
    if metric == "screen_score" and not subset and len(ranked) > 1:
        reason = _screen_leader_reason(ranked[0], ranked[1])
        if reason:
            summary += f" {reason}"
    return summary


_SCREEN_WEIGHTS = (("growth_points", "passenger growth", 40), ("volume_points", "passenger volume", 30),
                   ("occupancy_points", "seat occupancy", 30))
_SCREEN_LIMITATION = ("This is a relative traffic-pressure screen: it does not directly include terminal "
                      "square footage or flight-frequency pressure, and it is not a profitability model.")


def _screen_points(row) -> dict[str, float] | None:
    """The weighted components of a returned screen row, from either wire or model rows."""
    metrics = row["metrics"] if isinstance(row, dict) else [item.model_dump() for item in row.metrics]
    points = {item["key"]: item["value"] for item in metrics
              if item["key"] in {key for key, _label, _weight in _SCREEN_WEIGHTS} and item["value"] is not None}
    return points if len(points) == len(_SCREEN_WEIGHTS) else None


def _screen_leader_reason(first, second) -> str | None:
    """Why the top airport leads: its component points against the next airport's."""
    lead, runner = _screen_points(first), _screen_points(second)
    if lead is None or runner is None:
        return None
    name = (lambda row: row["airport"] if isinstance(row, dict) else row.airport)
    a, b = name(first), name(second)
    ahead = [f"{label} ({lead[key]:.1f} vs {runner[key]:.1f})" for key, label, _w in _SCREEN_WEIGHTS if lead[key] > runner[key]]
    behind = [f"{label} ({runner[key]:.1f} vs {lead[key]:.1f})" for key, label, _w in _SCREEN_WEIGHTS if lead[key] < runner[key]]
    if not ahead:
        return None
    gap = sum(lead.values()) - sum(runner.values())
    text = f"{a} ranks first, {gap:.2f} points ahead of {b}, on higher {_join_and(ahead)} points"
    if behind:
        text += f", which outweighs {b}'s higher {_join_and(behind)}"
    return text + "."


def _comparison_summary(metric, rows) -> str:
    if len(rows) != 2:
        return f"Comparison of {_metric_label(metric)} across the requested scope."
    if metric == "congestion":
        a, b = rows
        comparable = 0
        first_higher: list[str] = []
        second_higher: list[str] = []
        for left, right in zip(a["metrics"], b["metrics"]):
            if left["value"] is None or right["value"] is None:
                continue
            comparable += 1
            if left["value"] > right["value"]:
                first_higher.append(_metric_label(left["key"]))
            elif right["value"] > left["value"]:
                second_higher.append(_metric_label(right["key"]))
        if comparable == 0:
            return "Insufficient comparable operational indicators for a congestion comparison."
        if first_higher and second_higher:
            return (f"No single airport is uniformly more congested. {a['airport']} has the higher "
                    f"{_join_and(first_higher)}, while {b['airport']} has the higher {_join_and(second_higher)}.")
        if first_higher or second_higher:
            higher, lower, labels = (a, b, first_higher) if first_higher else (b, a, second_higher)
            tied = comparable - len(labels)
            return (f"{higher['airport']} is higher than {lower['airport']} on {len(labels)} of {comparable} comparable "
                    f"operational indicators: {_join_and(labels)}" + (f"; {tied} {'is' if tied == 1 else 'are'} tied." if tied else "."))
        return f"{a['airport']} and {b['airport']} are tied on {comparable} comparable operational indicators."
    a, b = rows
    first, second = a["metrics"][0]["value"], b["metrics"][0]["value"]
    label = _capitalized(_metric_label(a["metrics"][0]["key"]))
    if first is None or second is None:
        return f"{label} comparison is partial because at least one airport is unavailable."
    unit = a["metrics"][0]["unit"]
    if first == second:
        return f"{label}: {a['airport']} and {b['airport']} are tied at {_format_value(first, unit)}."
    higher, lower = (a, b) if first > second else (b, a)
    return (f"{label}: {higher['airport']} {_format_value(higher['metrics'][0]['value'], unit)} "
            f"versus {lower['airport']} {_format_value(lower['metrics'][0]['value'], unit)}.")


def _operation_summary(rows, year: int, metric: str | None = None) -> str:
    if metric in OPERATIONAL_KEYS:
        return _values_summary(metric, rows, year)
    return f"{year} operational indicators for {', '.join(row['airport'] for row in rows)}; delay and taxi means use eligible completed departures."


def _long_haul_summary(rows, threshold) -> str:
    parts = []
    for row in rows:
        share = row["metrics"][0]
        if share["value"] is not None and share["numerator"] is not None and share["denominator"] is not None:
            parts.append(f"{row['airport']} {_format_value(share['value'], share['unit'])} "
                         f"({int(share['numerator']):,} of {int(share['denominator']):,} eligible departures)")
    if not parts:
        return f"Long-haul share uses performed departures at or above {threshold:,g} miles."
    return f"Long-haul share (routes of {threshold:,g}+ miles): {'; '.join(parts)}."


def _evidence_claim(fact: str, status: str) -> str:
    """The reviewed fact leads; an unverified project status is stated after it, never implied."""
    claim = _sentence(fact.strip())
    if status == "unknown":
        claim += " Current project status was not verified in the accepted evidence."
    return claim


def _t100_source(source) -> dict[str, Any]:
    return {"id": _traffic_id(source.snapshot_id), "name": source.name, "url": source.url,
            "snapshot_id": source.snapshot_id, "period": source.period, "retrieved_at": None}


def _traffic_id(snapshot_id: str) -> str:
    return _compact_source_id("t100", snapshot_id)


def _operations_source(result: OperationsResult) -> dict[str, Any]:
    source = result.source
    return {"id": _compact_source_id("ontime", source.snapshot_id), "name": source.name, "url": source.url,
            "snapshot_id": source.snapshot_id, "period": source.period, "retrieved_at": None}


def _datasf_source(source, source_id: str) -> dict[str, Any]:
    return {"id": source_id, "name": source.name, "url": source.url, "snapshot_id": source.snapshot_id,
            "period": source.period, "retrieved_at": source.retrieved_at}


def _lineage_source(source) -> dict[str, Any]:
    return {"id": _compact_source_id(source.source_id, source.snapshot_id), "name": source.name, "url": source.url,
            "snapshot_id": source.snapshot_id, "period": source.period, "retrieved_at": None}


def _unavailable(key: str, unit: str, reason: str, source_id: str) -> MetricValue:
    return MetricValue(key=key, value=None, unit=unit, status="unavailable", reason=reason[:300], source_ids=[source_id])


def _compact_source_id(kind: str, snapshot_id: str) -> str:
    digest = hashlib.sha256(snapshot_id.encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{digest}"
