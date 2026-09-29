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
                   f"Long-haul share uses performed departures at or above {threshold:g} miles.",
                   threshold_miles=threshold,
                   bundle=bundle,
                   limitations=["The T-100 endpoint-distance share is descriptive and does not identify demand or profitability."])


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
                   _comparison_summary(request.metric, rows) if request.action == "compare" else _operation_summary(rows, request.year),
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
    if request.metric == "screen_score":
        summary = f"Ranked {len(rows)} assessable airports by screen_score; scores are normalized against the frozen full eligible cohort."
    elif request.metric == "passenger_growth":
        summary = f"Ranked {len(rows)} assessable airports by passenger_growth; ranks use raw growth across the full eligible cohort."
    else:
        summary = (f"Ranked {len(rows)} assessable airports by {request.metric} in {request.year}; "
                   "rank positions are against the full eligible cohort.")
    has_unavailable_values = any(metric["status"] != "ok" for row in rows for metric in row["metrics"])
    return _result(request, request_id, rows, [source, *evidence_sources],
                   "partial" if exclusions or has_unavailable_values or any("not_reviewed" in item for item in evidence_limitations) else "ok", summary,
                   bundle=bundle,
                   evidence=evidence, exclusions=exclusions,
                   limitations=["Screen scores are heuristic traffic pressure, not terminal capacity or investment success.", *evidence_limitations])


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
            limitations.append(f"Terminal evidence for {airport} is not_reviewed.")
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
                             "claim": f"Status unknown; {observation.fact}"[:500],
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
    available = sum(item.status == "ok" for item in metrics)
    if available == 0:
        raise DispatchFailure("insufficient_data", 422, "No requested SFO pressure indicator is available.")
    evidence, evidence_sources, evidence_limits = _rank_evidence(["SFO"], bundle=bundle)
    for source in evidence_sources:
        if source["id"] not in {item["id"] for item in sources}:
            sources.append(source)
    return _result(request, request_id, [{"airport": "SFO", "metrics": [item.model_dump() for item in metrics]}],
                   sources, "partial" if pressure.status != "ok" or any(item.status != "ok" for item in metrics) else "ok",
                   "SFO transported-traffic growth, occupancy and operational indicators are descriptive only; precise unmet demand is not identifiable.",
                   bundle=bundle,
                   series=series, evidence=evidence,
                   limitations=[pressure.limitation, "Profitability and quantitative unmet demand are not_identifiable.", *evidence_limits])


def _explain(request: AnalysisRequest, previous: AnalysisResult, request_id: UUID) -> AnalysisResult:
    selected = request.airports or previous.scope.airports
    if not set(selected) <= set(previous.scope.airports):
        raise DispatchFailure("unsupported_scope", 422, "Choose only airports in the referenced result.")
    lines = []
    for row in previous.rows:
        if row.airport not in selected:
            continue
        rendered = ", ".join(_explain_metric(metric) for metric in row.metrics)
        lines.append(f"{row.airport}: {rendered}.")
    evidence_notes = " ".join(item.claim for item in previous.evidence)
    limitations = " ".join(previous.limitations)
    summary = " ".join(lines)
    if evidence_notes:
        summary += f" Reviewed evidence: {evidence_notes}"
    if limitations:
        summary += f" Limitations: {limitations}"
    return previous.model_copy(update={"request_id": request_id, "summary": summary[:2000]}, deep=True)


def _explain_metric(metric: MetricValue) -> str:
    if metric.value is not None:
        return f"{metric.key} {metric.value:g} {metric.unit}"
    if (
        metric.key == "long_haul_share"
        and metric.status == "ok"
        and metric.lower_percent is not None
        and metric.upper_percent is not None
    ):
        unknown = metric.unknown_distance_departures or 0
        return (
            f"long_haul_share between {metric.lower_percent:g} and "
            f"{metric.upper_percent:g} percent ({unknown} departures have unknown distance)"
        )
    return f"{metric.key} unavailable"


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
    return _comparison_summary(request.metric, rows) if request.action == "compare" else f"{request.metric} for {', '.join(request.airports)} in {request.year}."


def _comparison_summary(metric, rows) -> str:
    if len(rows) != 2:
        return f"Comparison of {metric} across the requested scope."
    if metric == "congestion":
        a, b = rows
        comparable = 0
        first_higher = 0
        second_higher = 0
        for left, right in zip(a["metrics"], b["metrics"]):
            if left["value"] is None or right["value"] is None:
                continue
            comparable += 1
            first_higher += left["value"] > right["value"]
            second_higher += right["value"] > left["value"]
        if comparable == 0:
            return "Insufficient comparable operational indicators for a congestion comparison."
        if first_higher and second_higher:
            return (f"Mixed picture: {a['airport']} is higher on {first_higher} and {b['airport']} on "
                    f"{second_higher} of {comparable} comparable operational-strain indicators.")
        if first_higher:
            return f"{a['airport']} is higher on {first_higher} of {comparable} comparable operational-strain indicators."
        if second_higher:
            return f"{b['airport']} is higher on {second_higher} of {comparable} comparable operational-strain indicators."
        return f"{a['airport']} and {b['airport']} are tied on {comparable} comparable operational indicators."
    a, b = rows
    first, second = a["metrics"][0]["value"], b["metrics"][0]["value"]
    if first is None or second is None:
        return f"{metric} comparison is partial because at least one airport is unavailable."
    if first == second:
        return f"{a['airport']} and {b['airport']} are tied on {metric}."
    higher, lower = (a, b) if first > second else (b, a)
    return f"{higher['airport']} is higher than {lower['airport']} on {metric}."


def _operation_summary(rows, year: int) -> str:
    return f"{year} operational indicators for {', '.join(row['airport'] for row in rows)}; delay and taxi means use eligible completed departures."


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
