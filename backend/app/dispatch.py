"""Deterministic routing from validated analysis requests to local calculations."""

from __future__ import annotations

import hashlib
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
from app.evidence import load_evidence


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

    if request.action == "rank":
        return _rank(request, request_id)
    if request.metric == "sfo_enplaned_trend":
        return _sfo_trend(request, request_id)
    if request.metric == "sfo_pressure":
        return _sfo_pressure(request, request_id)
    if request.metric in OPERATIONAL_KEYS or request.metric == "congestion":
        return _operations(request, request_id)
    if request.metric == "long_haul_share":
        return _long_haul(request, request_id)
    if request.action == "compare":
        return _traffic_compare(request, request_id)
    return _traffic_metric(request, request_id)


def _traffic_metric(request: AnalysisRequest, request_id: UUID) -> AnalysisResult:
    assert request.airports and request.metric and request.year
    try:
        traffic = calculate_traffic_batch(request.airports)
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
    )


def _traffic_compare(request: AnalysisRequest, request_id: UUID) -> AnalysisResult:
    assert request.airports and request.metric and request.year
    try:
        traffic = calculate_traffic_batch(request.airports)
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


def _long_haul(request: AnalysisRequest, request_id: UUID) -> AnalysisResult:
    assert request.airports and request.metric and request.year
    threshold = float(request.threshold_miles or 3000)
    results: list[LongHaulResult] = []
    try:
        for airport in request.airports:
            results.append(calculate_long_haul_share(airport, request.year, threshold))
    except TrafficCalculationError as exc:
        raise DispatchFailure("data_unavailable", 503, "Qualified T-100 distance data is unavailable.") from exc
    if len({item.source.snapshot_id for item in results}) != 1:
        raise DispatchFailure("data_unavailable", 503, "The T-100 snapshot changed during this request. Retry once.")
    source = _t100_source(results[0].source)
    rows = []
    for item in results:
        metric = (
            MetricValue(key="long_haul_share", value=item.share_percent, unit="percent", status="ok",
                        numerator=item.long_haul_departures, denominator=item.total_departures,
                        source_ids=[source["id"]])
            if item.status == "ok" else
            _unavailable("long_haul_share", "percent", item.reason or "Long-haul share is unavailable.", source["id"])
        )
        rows.append({"airport": item.airport, "metrics": [metric.model_dump()]})
    if not any(row["metrics"][0]["status"] == "ok" for row in rows):
        raise DispatchFailure("insufficient_data", 422, "Long-haul share is unavailable for this scope.")
    return _result(request, request_id, rows, [source], _availability_status(rows),
                   f"Long-haul share uses performed departures at or above {threshold:g} miles.",
                   threshold_miles=threshold,
                   limitations=["The T-100 endpoint-distance share is descriptive and does not identify demand or profitability."])


def _operations(request: AnalysisRequest, request_id: UUID) -> AnalysisResult:
    assert request.airports and request.metric
    outputs: list[OperationsResult] = []
    try:
        for airport in request.airports:
            outputs.append(calculate_operations(airport))
    except OperationsCalculationError as exc:
        raise DispatchFailure("data_unavailable", 503, "Qualified 2024 on-time data is unavailable for this scope.") from exc
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
        raise DispatchFailure("insufficient_data", 422, "No requested 2024 operational indicator is available.")
    return _result(request, request_id, rows, [source], _availability_status(rows),
                   _comparison_summary(request.metric, rows) if request.action == "compare" else _operation_summary(rows),
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
    text = (f"{result.airport} 2024 on-time coverage: observed months {observed}; missing months {missing}; "
            f"reporting carriers {carriers}; valid scheduled records {result.scheduled_count}; "
            f"invalid rows {result.invalid_row_count}.")
    return text[:500]


def _rank(request: AnalysisRequest, request_id: UUID) -> AnalysisResult:
    assert request.metric and request.year
    selected = sorted(NEW_ENGLAND_AIRPORTS) if request.region == "new_england" else request.airports
    assert selected
    try:
        traffic = calculate_traffic_batch(sorted(NEW_ENGLAND_AIRPORTS))
    except TrafficCalculationError as exc:
        raise DispatchFailure("data_unavailable", 503, "Qualified New England T-100 data is unavailable.") from exc
    screen = calculate_screen(
        traffic.values(),
        sort_by="passenger_growth" if request.metric == "passenger_growth" else "screen_score",
    )
    if request.metric == "screen_score" and (screen.status != "ok" or not screen.rows):
        raise DispatchFailure("insufficient_data", 422, screen.reason or "Fewer than two airports are assessable.")
    selected_set = set(selected)
    screen_by_airport = {row.airport: row for row in screen.rows}
    year_index = request.year - 2023
    annual_by_airport = {airport: result.annual[year_index] for airport, result in traffic.items()}
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
        if request.year == 2023:
            metrics = [level_metrics[request.metric]]
        else:
            metrics = [
                MetricValue(key="screen_score", value=item.screen_score if item else None, unit="score",
                            status="ok" if item else "unavailable",
                            reason=None if item else "Incomplete 2024 screen inputs; no frozen-cohort score is available.",
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
    evidence, evidence_sources, evidence_limitations = _rank_evidence([row["airport"] for row in rows[:3]])
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
                   evidence=evidence, exclusions=exclusions,
                   limitations=["Screen scores are heuristic traffic pressure, not terminal capacity or investment success.", *evidence_limitations])


def _rank_evidence(top_airports: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    try:
        bundle = load_evidence()
    except (OSError, ValueError):
        return [], [], ["Curated terminal evidence is unavailable; no terminal disposition is inferred."]
    source_by_id = {item.source_id: item for item in bundle.sources}
    reviewed = {item.airport: item for item in bundle.airports}
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


def _sfo_trend(request: AnalysisRequest, request_id: UUID) -> AnalysisResult:
    try:
        trend = calculate_sfo_enplaned_trend()
    except SFOTrendError as exc:
        raise DispatchFailure("data_unavailable", 503, "SFO passenger data is unavailable or incomplete. Refresh the accepted snapshot and try again.") from exc
    source_id = f"datasf-{trend.source.dataset_id}"
    annual = {item.year: item.passengers for item in trend.annual_totals}
    metrics = [
        MetricValue(key="passengers", value=annual[2024], unit="count", status="ok", source_ids=[source_id]),
        MetricValue(key="passenger_growth", value=trend.growth.percent, unit="percent", status=trend.growth.status,
                    numerator=annual[2024] - annual[2023], denominator=annual[2023], source_ids=[source_id],
                    reason=trend.growth.reason),
    ]
    return _result(request, request_id, [{"airport": "SFO", "metrics": [item.model_dump() for item in metrics]}],
                   [_datasf_source(trend.source, source_id)],
                   "partial" if trend.growth.status == "unavailable" else "ok",
                   f"SFO recorded {annual[2023]:,} enplaned passengers in 2023 and {annual[2024]:,} in 2024. " +
                   (f"That is {trend.growth.percent:.2f}% growth." if trend.growth.percent is not None else trend.growth.reason or "Growth is unavailable."),
                   series=[{"period": item.period, "value": item.passengers, "unit": "count", "status": "ok"} for item in trend.series],
                   limitations=["Passenger trends do not identify unmet demand or its cause."])


def _sfo_pressure(request: AnalysisRequest, request_id: UUID) -> AnalysisResult:
    bundle = calculate_sfo_pressure()
    if not bundle.lineage:
        raise DispatchFailure("data_unavailable", 503, "No accepted SFO source snapshot is available.")
    source_by_key = {item.source_id: _lineage_source(item) for item in bundle.lineage}
    sources = list(source_by_key.values())
    metrics: list[MetricValue] = []
    series = []
    if bundle.enplaned_trend.result is not None:
        trend = bundle.enplaned_trend.result
        enplaned_id = source_by_key["datasf"]["id"]
        annual = {item.year: item.passengers for item in trend.annual_totals}
        metrics.append(MetricValue(key="sfo_enplaned_trend", value=annual[2024], unit="count", status="ok", source_ids=[enplaned_id]))
        series = [{"period": item.period, "value": item.passengers, "unit": "count", "status": "ok"} for item in trend.series]
    if bundle.t100_traffic.result is not None:
        traffic = bundle.t100_traffic.result
        source_id = source_by_key["t100"]["id"]
        for key in ("passengers", "seats", "departures"):
            metrics.append(_traffic_metric_value(key, 2024, traffic, source_id))
        metrics.append(_traffic_metric_value("passenger_growth", 2024, traffic, source_id))
        metrics.append(_traffic_metric_value("seat_occupancy", 2024, traffic, source_id))
    if bundle.operations.result is not None:
        operation = bundle.operations.result
        source_id = source_by_key["bts_ontime"]["id"]
        metrics.extend(_operation_metric(operation, key, source_id) for key in CONGESTION_KEYS)
    gap_source = source_by_key.get("t100", {}).get("id")
    metrics.append(MetricValue(key="sfo_pressure", value=bundle.growth_gap_pp.value, unit="percentage_points",
                              status=bundle.growth_gap_pp.status, source_ids=[gap_source] if gap_source else [],
                              reason=bundle.growth_gap_pp.reason or (None if gap_source else "Matched T-100 data is unavailable.")))
    available = sum(item.status == "ok" for item in metrics)
    if available == 0:
        raise DispatchFailure("insufficient_data", 422, "No requested SFO pressure indicator is available.")
    evidence, evidence_sources, evidence_limits = _rank_evidence(["SFO"])
    for source in evidence_sources:
        if source["id"] not in {item["id"] for item in sources}:
            sources.append(source)
    return _result(request, request_id, [{"airport": "SFO", "metrics": [item.model_dump() for item in metrics]}],
                   sources, "partial" if bundle.status != "ok" or any(item.status != "ok" for item in metrics) else "ok",
                   "SFO transported-traffic growth, occupancy and operational indicators are descriptive only; precise unmet demand is not identifiable.",
                   series=series, evidence=evidence,
                   limitations=[bundle.limitation, "Profitability and quantitative unmet demand are not_identifiable.", *evidence_limits])


def _explain(request: AnalysisRequest, previous: AnalysisResult, request_id: UUID) -> AnalysisResult:
    selected = request.airports or previous.scope.airports
    if not set(selected) <= set(previous.scope.airports):
        raise DispatchFailure("unsupported_scope", 422, "Choose only airports in the referenced result.")
    lines = []
    for row in previous.rows:
        if row.airport not in selected:
            continue
        rendered = ", ".join(
            f"{metric.key} {metric.value:g} {metric.unit}" if metric.value is not None
            else f"{metric.key} unavailable" for metric in row.metrics
        )
        lines.append(f"{row.airport}: {rendered}.")
    evidence_notes = " ".join(item.claim for item in previous.evidence)
    limitations = " ".join(previous.limitations)
    summary = " ".join(lines)
    if evidence_notes:
        summary += f" Reviewed evidence: {evidence_notes}"
    if limitations:
        summary += f" Limitations: {limitations}"
    return previous.model_copy(update={"request_id": request_id, "summary": summary[:2000]}, deep=True)


def _result(request, request_id, rows, sources, status, summary, *, series=None, evidence=None,
            exclusions=None, limitations=None, threshold_miles=None) -> AnalysisResult:
    airports = request.airports or sorted(NEW_ENGLAND_AIRPORTS)
    return AnalysisResult.model_validate({
        "result_id": uuid4(), "request_id": request_id, "status": status,
        "scope": {"airports": airports, "year": request.year, "metric": request.metric,
                  "threshold_miles": threshold_miles if threshold_miles is not None else request.threshold_miles,
                  "population": _population(request)},
        "rows": rows, "summary": summary, "series": series or [], "sources": sources,
        "evidence": evidence or [], "exclusions": exclusions or [], "limitations": limitations or [],
    })


def _population(request: AnalysisRequest) -> str:
    if request.metric in OPERATIONAL_KEYS or request.metric == "congestion":
        return "Domestic reporting-carrier scheduled departures at origin; 2024 only"
    if request.metric in {"sfo_enplaned_trend", "sfo_pressure"}:
        return "SFO Enplaned passengers combined Domestic and International; T-100 and on-time populations separately identified"
    if request.action == "rank":
        if request.metric == "screen_score":
            return "New England airports normalized against the frozen eligible 2024 cohort"
        if request.metric == "passenger_growth":
            return "Raw 2024 versus 2023 passenger growth; rank positions against the full growth-eligible New England cohort"
        label = "passengers" if request.metric == "passengers" else "seat occupancy"
        return (f"Raw BTS T-100 {label} for CY{request.year}; rank positions against the full eligible "
                "New England cohort with valid requested-year values")
    return "BTS T-100 scheduled passenger origin traffic; 2023 and 2024 where applicable"


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
            return f"Mixed picture: indicators favor {a['airport']} and {b['airport']} across {comparable} comparable operational measures."
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


def _operation_summary(rows) -> str:
    return f"2024 operational indicators for {', '.join(row['airport'] for row in rows)}; delay and taxi means use eligible completed departures."


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
