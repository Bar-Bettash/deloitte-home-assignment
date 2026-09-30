"""Strict HTTP wire contracts for the local airport-analysis prototype."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

MAX_REQUEST_BYTES = 32 * 1024


def validate_request_body_size(body: bytes) -> None:
    """Raise ValueError before JSON parsing when the documented byte cap is exceeded."""
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("request body exceeds 32 KiB")


NEW_ENGLAND = frozenset(
    "BDL HVN PWM BGR PQI RKD BHB AUG BOS ACK ORH MVY HYA PVC MHT PSM LEB PVD WST BID BTV RUT".split()
)
NEW_ENGLAND_RECENT = NEW_ENGLAND | {"EWB"}
AIRPORTS = NEW_ENGLAND_RECENT | {"ANC", "LAX", "SNA", "SFO"}
OPERATIONAL_AIRPORTS = frozenset({"LAX", "SNA", "SFO"})
METRICS = frozenset(
    "passengers seats departures passenger_growth seat_occupancy long_haul_share "
    "screen_score congestion cancellation_rate diversion_rate departure_delay_minutes "
    "taxi_out_minutes sfo_enplaned_trend sfo_pressure".split()
)
RANK_METRICS = frozenset({"screen_score", "passengers", "passenger_growth", "seat_occupancy"})
T100_METRICS = frozenset(
    {"passengers", "seats", "departures", "passenger_growth", "seat_occupancy", "long_haul_share"}
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


WireNumber = StrictInt | Annotated[StrictFloat, Field(allow_inf_nan=False)]


class AnalysisRequest(StrictModel):
    action: Literal["rank", "compare", "metric", "explain"]
    airports: list[Annotated[StrictStr, Field(min_length=3, max_length=3)]] | None = Field(default=None, max_length=23)
    region: Literal["new_england"] | None = None
    metric: Literal[
        "passengers", "seats", "departures", "passenger_growth", "seat_occupancy",
        "long_haul_share", "screen_score", "congestion", "cancellation_rate",
        "diversion_rate", "departure_delay_minutes", "taxi_out_minutes",
        "sfo_enplaned_trend", "sfo_pressure",
    ] | None = None
    year: StrictInt | None = None
    bundle_id: Annotated[
        StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    ] | None = None
    threshold_miles: WireNumber | None = None

    @field_validator("airports")
    @classmethod
    def airport_codes_are_canonical(cls, value):
        if value is not None:
            normalized = [code.upper() for code in value]
            if any(code not in AIRPORTS for code in normalized):
                raise ValueError("unsupported airport")
            if len(set(normalized)) != len(normalized):
                raise ValueError("airports must be unique")
            return normalized
        return value

    @field_validator("threshold_miles")
    @classmethod
    def threshold_is_finite_and_bounded(cls, value):
        if value is not None and not (0 < value <= 12000):
            raise ValueError("threshold_miles must be in (0, 12000]")
        return value

    @model_validator(mode="after")
    def valid_scope_combination(self):
        if self.action == "explain":
            if any(
                v is not None
                for v in (self.metric, self.year, self.bundle_id, self.threshold_miles, self.region)
            ):
                raise ValueError("explain does not accept metric, year, bundle, threshold, or region")
            if self.airports is not None and len(self.airports) > 2:
                raise ValueError("explain accepts at most two airports")
            return self

        if self.metric is None:
            raise ValueError("metric is required")
        if self.year is not None and self.year not in (2023, 2024, 2025):
            raise ValueError("unsupported year")
        if self.metric in {"passenger_growth", "screen_score"} and self.year == 2023:
            raise ValueError("passenger growth and screen score require a comparison period")
        if self.threshold_miles is not None and self.metric != "long_haul_share":
            raise ValueError("threshold is only valid for long_haul_share")
        if self.metric == "long_haul_share" and self.threshold_miles is None:
            # The application applies the documented 3,000-mile default when omitted.
            pass

        if self.action == "rank":
            if self.metric not in RANK_METRICS:
                raise ValueError("unsupported ranking metric/year")
            if self.airports is not None and (
                not self.airports or not set(self.airports) <= NEW_ENGLAND_RECENT
            ):
                raise ValueError("rank airports must be a nonempty New England subset")
            if self.region not in (None, "new_england"):
                raise ValueError("unsupported ranking region")
            if self.region is not None and self.airports is not None:
                raise ValueError("provide region or airports, not both")
            if self.region is None and self.airports is None:
                raise ValueError("rank requires region or an airport subset")
        elif self.action == "compare":
            if self.region is not None or self.airports is None or len(self.airports) != 2:
                raise ValueError("compare requires exactly two airports")
            if self.metric in {"congestion", "cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes"}:
                if self.year not in (None, 2024, 2025) or not set(self.airports) <= OPERATIONAL_AIRPORTS:
                    raise ValueError("operational comparison supports LAX/SNA/SFO")
            elif self.metric not in T100_METRICS:
                raise ValueError("unsupported comparison metric")
        elif self.action == "metric":
            if self.region is not None or self.airports is None or len(self.airports) != 1:
                raise ValueError("metric requires exactly one airport")
            airport = self.airports[0]
            if self.metric in {"congestion", "cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes"}:
                if self.year not in (None, 2024, 2025) or airport not in OPERATIONAL_AIRPORTS:
                    raise ValueError("operational metrics support LAX/SNA/SFO")
            elif self.metric in {"sfo_enplaned_trend", "sfo_pressure"}:
                if self.year not in (None, 2024, 2025) or airport != "SFO":
                    raise ValueError("SFO metrics support SFO")
            elif self.metric not in T100_METRICS:
                raise ValueError("unsupported metric")
        return self


class QueryRequest(StrictModel):
    message: Annotated[StrictStr, Field(min_length=1, max_length=4000)] | None = None
    analysis: AnalysisRequest | None = None
    context_result_id: UUID | None = None

    @field_validator("context_result_id", mode="before")
    @classmethod
    def parse_result_reference(cls, value):
        if isinstance(value, str):
            return UUID(value)
        return value

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("message must not be blank")
        return value

    @model_validator(mode="after")
    def exactly_one_request_mode(self):
        if (self.message is None) == (self.analysis is None):
            raise ValueError("provide exactly one of message or analysis")
        if self.analysis is not None and self.analysis.action == "explain" and self.context_result_id is None:
            raise ValueError("explain requires context_result_id")
        return self


class MetricValue(StrictModel):
    key: Literal[
        "passengers", "seats", "departures", "passenger_growth", "seat_occupancy",
        "long_haul_share", "screen_score", "cancellation_rate", "diversion_rate",
        "departure_delay_minutes", "taxi_out_minutes", "sfo_enplaned_trend", "enplaned_growth",
        "sfo_pressure", "growth_points", "volume_points", "occupancy_points",
    ]
    value: WireNumber | None
    unit: Literal["count", "percent", "percentage_points", "minutes", "score"]
    status: Literal["ok", "unavailable"]
    numerator: WireNumber | None = None
    denominator: WireNumber | None = None
    unknown_distance_departures: Annotated[StrictInt, Field(ge=0)] | None = None
    lower_percent: Annotated[WireNumber, Field(ge=0, le=100)] | None = None
    upper_percent: Annotated[WireNumber, Field(ge=0, le=100)] | None = None
    eligible_count: Annotated[StrictInt, Field(ge=0)] | None = None
    comparison_direction: Literal["higher", "lower", "tied", "unavailable"] | None = None
    source_ids: list[Annotated[StrictStr, Field(min_length=1, max_length=80)]] = Field(max_length=20)
    reason: Annotated[StrictStr, Field(min_length=1, max_length=300)] | None = None

    @model_validator(mode="after")
    def consistent_availability_and_units(self):
        if self.status == "unavailable" and (self.value is not None or self.reason is None):
            raise ValueError("unavailable metric requires null value and reason")
        bounded_long_haul = (
            self.key == "long_haul_share"
            and self.status == "ok"
            and self.lower_percent is not None
            and self.upper_percent is not None
        )
        if self.status == "ok" and self.value is None and not bounded_long_haul:
            raise ValueError("available metric requires a value")
        if (self.numerator is None) != (self.denominator is None):
            raise ValueError("numerator and denominator must appear together")
        if self.unit == "count" and self.value is not None and not isinstance(self.value, int):
            raise ValueError("count values must be integers")
        expected_unit = {
            "passengers": "count", "seats": "count", "departures": "count",
            "passenger_growth": "percent", "seat_occupancy": "percent",
            "long_haul_share": "percent", "screen_score": "score",
            "cancellation_rate": "percent", "diversion_rate": "percent",
            "departure_delay_minutes": "minutes", "taxi_out_minutes": "minutes",
            "sfo_enplaned_trend": "count", "enplaned_growth": "percent",
            "sfo_pressure": "percentage_points",
            # Weighted screen-score components: points out of 40, 30 and 30.
            "growth_points": "score", "volume_points": "score", "occupancy_points": "score",
        }[self.key]
        if self.unit != expected_unit:
            raise ValueError(f"{self.key} must use {expected_unit}")
        if self.status == "ok" and self.key in {"seat_occupancy", "long_haul_share", "cancellation_rate", "diversion_rate"} and self.numerator is None:
            raise ValueError("ratio metric requires numerator and denominator")
        self._validate_long_haul_uncertainty()
        return self

    def _validate_long_haul_uncertainty(self) -> None:
        typed = any(
            value is not None
            for value in (
                self.unknown_distance_departures,
                self.lower_percent,
                self.upper_percent,
            )
        )
        if not typed:
            return
        if self.key != "long_haul_share":
            raise ValueError("distance uncertainty fields are only valid for long_haul_share")
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (self.numerator, self.denominator, self.unknown_distance_departures)
        ):
            raise ValueError("long-haul uncertainty requires integer departure counts")
        long_haul = self.numerator
        total = self.denominator
        unknown = self.unknown_distance_departures
        if long_haul < 0 or total < 0 or long_haul + unknown > total:
            raise ValueError("long-haul departure counts are inconsistent")
        if total == 0:
            if (
                self.status != "unavailable"
                or long_haul != 0
                or unknown != 0
                or self.lower_percent is not None
                or self.upper_percent is not None
            ):
                raise ValueError("zero departures require unavailable null bounds")
            return
        if self.status != "ok" or self.lower_percent is None or self.upper_percent is None:
            raise ValueError("positive departures require available lower and upper bounds")
        expected_lower = 100.0 * long_haul / total
        expected_upper = 100.0 * (long_haul + unknown) / total
        if not math.isclose(float(self.lower_percent), expected_lower, abs_tol=1e-9):
            raise ValueError("lower_percent does not match departure counts")
        if not math.isclose(float(self.upper_percent), expected_upper, abs_tol=1e-9):
            raise ValueError("upper_percent does not match departure counts")
        if unknown == 0:
            if self.value is None or not math.isclose(float(self.value), expected_lower, abs_tol=1e-9):
                raise ValueError("exact long-haul value does not match departure counts")
        elif self.value is not None:
            raise ValueError("bounded long-haul metric must not claim an exact value")


class AirportRow(StrictModel):
    airport: Annotated[StrictStr, Field(min_length=3, max_length=3)]
    metrics: list[MetricValue] = Field(min_length=1, max_length=20)
    rank: StrictInt | None = Field(default=None, ge=1, le=23)

    @field_validator("airport")
    @classmethod
    def valid_airport(cls, value):
        if value not in AIRPORTS:
            raise ValueError("unsupported airport")
        return value


class ResultScope(StrictModel):
    airports: list[Annotated[StrictStr, Field(min_length=3, max_length=3)]] = Field(min_length=1, max_length=23)
    year: StrictInt = Field(ge=2023, le=2025)
    bundle_id: Annotated[
        StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    ] | None = None
    baseline_year: StrictInt | None = Field(default=None, ge=2023, le=2025)
    comparison_year: StrictInt | None = Field(default=None, ge=2023, le=2025)
    metric: Literal[
        "passengers", "seats", "departures", "passenger_growth", "seat_occupancy",
        "long_haul_share", "screen_score", "congestion", "cancellation_rate",
        "diversion_rate", "departure_delay_minutes", "taxi_out_minutes",
        "sfo_enplaned_trend", "sfo_pressure",
    ]
    threshold_miles: WireNumber | None = None
    population: Annotated[StrictStr, Field(min_length=1, max_length=200)]

    @field_validator("airports")
    @classmethod
    def valid_airports(cls, value):
        if any(airport not in AIRPORTS for airport in value) or len(value) != len(set(value)):
            raise ValueError("scope airports must be supported and unique")
        return value

    @model_validator(mode="after")
    def resolved_period_is_consistent(self):
        fields = (self.bundle_id, self.baseline_year, self.comparison_year)
        if any(value is not None for value in fields) != all(value is not None for value in fields):
            raise ValueError("resolved bundle scope fields must appear together")
        if self.bundle_id is None:
            if self.year == 2025:
                raise ValueError("recent result scope requires resolved bundle identity")
            return self
        if self.baseline_year >= self.comparison_year:
            raise ValueError("resolved bundle years are invalid")
        if self.year not in (self.baseline_year, self.comparison_year):
            raise ValueError("result year is outside the resolved bundle period")
        return self


class SourceRef(StrictModel):
    id: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    name: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    url: AnyHttpUrl | None
    snapshot_id: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    period: Annotated[StrictStr, Field(min_length=1, max_length=40)]
    retrieved_at: datetime | None


class EvidenceRef(StrictModel):
    source_id: Annotated[StrictStr, Field(min_length=1, max_length=80)]
    locator: Annotated[StrictStr, Field(min_length=1, max_length=300)]
    date: Annotated[StrictStr, Field(min_length=4, max_length=40)]
    claim: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    limitation: Annotated[StrictStr, Field(min_length=1, max_length=500)]


class SeriesPoint(StrictModel):
    period: Annotated[StrictStr, Field(pattern=r"^202[345](0[1-9]|1[0-2])$", min_length=6, max_length=6)]
    value: WireNumber | None
    unit: Literal["count", "percent", "percentage_points", "minutes", "score"]
    status: Literal["ok", "unavailable"]


class AnalysisResult(StrictModel):
    result_id: UUID
    request_id: UUID
    status: Literal["ok", "partial"]
    scope: ResultScope
    rows: list[AirportRow] = Field(max_length=23)
    summary: Annotated[StrictStr, Field(max_length=2000)] | None
    series: list[SeriesPoint] = Field(max_length=24)
    sources: list[SourceRef] = Field(max_length=40)
    evidence: list[EvidenceRef] = Field(max_length=40)
    exclusions: list[Annotated[StrictStr, Field(min_length=1, max_length=300)]] = Field(max_length=23)
    limitations: list[Annotated[StrictStr, Field(min_length=1, max_length=500)]] = Field(max_length=30)

    @field_validator("result_id", "request_id", mode="before")
    @classmethod
    def parse_wire_uuids(cls, value):
        if isinstance(value, str):
            return UUID(value)
        return value

    @model_validator(mode="after")
    def references_resolve(self):
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source IDs must be unique")
        known = set(ids)
        if any(row.airport not in self.scope.airports for row in self.rows):
            raise ValueError("result row is outside declared scope")
        for row in self.rows:
            for metric in row.metrics:
                if not set(metric.source_ids) <= known:
                    raise ValueError("metric source reference is unresolved")
        if any(item.source_id not in known for item in self.evidence):
            raise ValueError("evidence source reference is unresolved")
        return self


ErrorCode = Literal[
    "invalid_json", "unsupported_media_type", "request_too_large", "invalid_request",
    "unsupported_scope", "clarification_required", "insufficient_data", "busy",
    "session_expired", "result_mismatch", "ai_unavailable",
    "data_unavailable", "query_timeout", "internal_error",
]


class ErrorDetail(StrictModel):
    code: ErrorCode
    message: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    request_id: UUID

    @field_validator("request_id", mode="before")
    @classmethod
    def parse_request_id(cls, value):
        if isinstance(value, str):
            return UUID(value)
        return value


class ErrorResponse(StrictModel):
    success: Literal[False]
    error: ErrorDetail
