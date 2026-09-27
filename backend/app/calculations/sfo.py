"""SFO passenger-trend calculation over an accepted local DataSF snapshot."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generic, Literal, TypeVar

import duckdb
from app.calculations.operations import (
    DEFAULT_DATA_ROOT as DEFAULT_OPERATIONS_ROOT,
)
from app.calculations.operations import (
    OperationsCalculationError,
    OperationsResult,
    calculate_operations,
)
from app.calculations.traffic import (
    DEFAULT_DATA_ROOT as DEFAULT_T100_ROOT,
)
from app.calculations.traffic import (
    TrafficCalculationError,
    TrafficResult,
    calculate_traffic,
)

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "datasf"
DEFAULT_T100_DATA_ROOT = DEFAULT_T100_ROOT
DEFAULT_OPERATIONS_DATA_ROOT = DEFAULT_OPERATIONS_ROOT
_PERIODS = tuple(f"{year}{month:02d}" for year in (2023, 2024) for month in range(1, 13))
_GEOGRAPHIES = ("Domestic", "International")
_EXPECTED_CELLS = {(period, geography) for period in _PERIODS for geography in _GEOGRAPHIES}


class SFOTrendError(RuntimeError):
    """The accepted snapshot cannot produce a trustworthy SFO trend."""


@dataclass(frozen=True, slots=True)
class MonthlyPassengerTotal:
    period: str
    passengers: int


@dataclass(frozen=True, slots=True)
class AnnualPassengerTotal:
    year: int
    passengers: int


@dataclass(frozen=True, slots=True)
class PassengerGrowth:
    baseline_year: int
    comparison_year: int
    percent: float | None
    status: Literal["ok", "unavailable"]
    reason: str | None


@dataclass(frozen=True, slots=True)
class SnapshotSource:
    snapshot_id: str
    dataset_id: str
    name: str
    url: str
    period: str
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class SFOTrendResult:
    source: SnapshotSource
    series: tuple[MonthlyPassengerTotal, ...]
    annual_totals: tuple[AnnualPassengerTotal, ...]
    growth: PassengerGrowth


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ComponentResult(Generic[T]):
    status: Literal["ok", "unavailable"]
    result: T | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class PressureMetric:
    value: float | None
    unit: Literal["percentage_points"]
    status: Literal["ok", "unavailable"]
    reason: str | None


@dataclass(frozen=True, slots=True)
class SFOPressureLineage:
    source_id: Literal["datasf", "t100", "bts_ontime"]
    snapshot_id: str
    name: str
    url: str
    period: str


@dataclass(frozen=True, slots=True)
class SFOPressureBundle:
    status: Literal["ok", "partial", "unavailable"]
    enplaned_trend: ComponentResult[SFOTrendResult]
    t100_traffic: ComponentResult[TrafficResult]
    operations: ComponentResult[OperationsResult]
    growth_gap_pp: PressureMetric
    lineage: tuple[SFOPressureLineage, ...]
    limitation: str = (
        "These transported-passenger, supplied-seat and operational indicators do not identify "
        "unmet demand, terminal saturation, or the cause of any observed pressure."
    )


def calculate_sfo_enplaned_trend(data_root: Path = DEFAULT_DATA_ROOT) -> SFOTrendResult:
    """Return the combined 2023/24 SFO enplaned trend from the accepted snapshot."""
    manifest, parquet_path = _load_accepted_snapshot(Path(data_root))
    source = _source_from_manifest(manifest)
    cells = _query_enplaned_cells(parquet_path)
    if set(cells) != _EXPECTED_CELLS:
        raise SFOTrendError("accepted DataSF snapshot must contain all 48 Enplaned month/geography cells")

    series = tuple(
        MonthlyPassengerTotal(
            period=period,
            passengers=sum(cells[(period, geography)] for geography in _GEOGRAPHIES),
        )
        for period in _PERIODS
    )
    derived_annual_totals = {
        year: sum(point.passengers for point in series if point.period.startswith(str(year)))
        for year in (2023, 2024)
    }
    source_annual_totals = _query_annual_source_totals(parquet_path)
    if source_annual_totals != derived_annual_totals:
        raise SFOTrendError("combined monthly series does not reconcile to annual source totals")

    annual_totals = tuple(
        AnnualPassengerTotal(year=year, passengers=source_annual_totals[year])
        for year in (2023, 2024)
    )
    baseline = source_annual_totals[2023]
    comparison = source_annual_totals[2024]
    if baseline == 0:
        growth = PassengerGrowth(
            baseline_year=2023,
            comparison_year=2024,
            percent=None,
            status="unavailable",
            reason="2023 enplaned passenger total is zero; growth is undefined",
        )
    else:
        growth = PassengerGrowth(
            baseline_year=2023,
            comparison_year=2024,
            percent=(comparison - baseline) / baseline * 100,
            status="ok",
            reason=None,
        )

    return SFOTrendResult(
        source=source,
        series=series,
        annual_totals=annual_totals,
        growth=growth,
    )


def calculate_sfo_pressure(
    datasf_root: Path = DEFAULT_DATA_ROOT,
    t100_root: Path = DEFAULT_T100_DATA_ROOT,
    operations_root: Path = DEFAULT_OPERATIONS_DATA_ROOT,
) -> SFOPressureBundle:
    """Combine one accepted snapshot per source while retaining independent results.

    DataSF enplanements remain a separately scoped trend. The growth gap and
    occupancy are calculated only from SFO's matching T-100 origin population.
    """
    enplaned = _component(
        lambda: calculate_sfo_enplaned_trend(Path(datasf_root)),
        SFOTrendError,
        "DataSF accepted SFO snapshot is unavailable",
    )
    traffic = _component(
        lambda: calculate_traffic("SFO", Path(t100_root)),
        TrafficCalculationError,
        "accepted T-100 SFO snapshot is unavailable",
    )
    operations = _component(
        lambda: calculate_operations("SFO", Path(operations_root)),
        OperationsCalculationError,
        "accepted CY2024 SFO on-time snapshot is unavailable",
    )

    gap = _growth_gap(traffic.result) if traffic.result is not None else _unavailable_gap(
        traffic.reason or "accepted T-100 SFO snapshot is unavailable"
    )
    lineage: list[SFOPressureLineage] = []
    if enplaned.result is not None:
        source = enplaned.result.source
        lineage.append(SFOPressureLineage("datasf", source.snapshot_id, source.name, source.url, source.period))
    if traffic.result is not None:
        source = traffic.result.source
        lineage.append(SFOPressureLineage("t100", source.snapshot_id, source.name, source.url, source.period))
    if operations.result is not None:
        source = operations.result.source
        lineage.append(SFOPressureLineage("bts_ontime", source.snapshot_id, source.name, source.url, source.period))

    available_components = sum(
        component.result is not None for component in (enplaned, traffic, operations)
    )
    if available_components == 0:
        status: Literal["ok", "partial", "unavailable"] = "unavailable"
    elif available_components == 3 and gap.status == "ok":
        status = "ok"
    else:
        status = "partial"
    return SFOPressureBundle(status, enplaned, traffic, operations, gap, tuple(lineage))


def _component(call, error_type, unavailable_reason):
    try:
        result = call()
    except error_type:
        return ComponentResult("unavailable", None, unavailable_reason)
    return ComponentResult("ok", result, None)


def _growth_gap(traffic: TrafficResult) -> PressureMetric:
    """Compute 100*((P24/P23-1)-(S24/S23-1)) from matched T-100 origin rows."""
    by_year = {item.year: item for item in traffic.annual}
    baseline = by_year.get(2023)
    comparison = by_year.get(2024)
    if baseline is None or comparison is None:
        return _unavailable_gap("T-100 2023/2024 annual periods are not both available")
    if not baseline.coverage.complete or not comparison.coverage.complete:
        return _unavailable_gap("T-100 growth gap requires complete 2023 and 2024 periods")
    measures = (baseline.passengers, comparison.passengers, baseline.seats, comparison.seats)
    if any(item.status != "ok" or item.value is None for item in measures):
        return _unavailable_gap("T-100 passengers and seats must be valid for both years")
    p23, p24 = baseline.passengers.value, comparison.passengers.value
    s23, s24 = baseline.seats.value, comparison.seats.value
    if p23 <= 0 or s23 <= 0:
        return _unavailable_gap("T-100 2023 passenger and seat baselines must be positive")
    passenger_growth = 100.0 * (p24 / p23 - 1.0)
    seat_growth = 100.0 * (s24 / s23 - 1.0)
    return PressureMetric(passenger_growth - seat_growth, "percentage_points", "ok", None)


def _unavailable_gap(reason: str) -> PressureMetric:
    return PressureMetric(None, "percentage_points", "unavailable", reason)


def _load_accepted_snapshot(data_root: Path) -> tuple[dict[str, object], Path]:
    pointer = _read_json(data_root / "current.json", "DataSF current snapshot pointer")
    snapshot_id = pointer.get("snapshot_id")
    manifest_relative = pointer.get("manifest")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise SFOTrendError("DataSF current snapshot pointer has no snapshot ID")
    expected_manifest = f"snapshots/{snapshot_id}/manifest.json"
    if manifest_relative != expected_manifest:
        raise SFOTrendError("DataSF current snapshot pointer has an invalid manifest path")

    manifest_path = data_root / expected_manifest
    manifest = _read_json(manifest_path, "DataSF snapshot manifest")
    if manifest.get("snapshot_id") != snapshot_id:
        raise SFOTrendError("DataSF snapshot manifest does not match the current pointer")
    if manifest.get("validation_status") != "accepted":
        raise SFOTrendError("DataSF snapshot is not marked accepted")

    parquet_file = manifest.get("parquet_file")
    if not isinstance(parquet_file, str) or not parquet_file or Path(parquet_file).name != parquet_file:
        raise SFOTrendError("DataSF snapshot manifest has an invalid Parquet filename")
    parquet_path = manifest_path.parent / parquet_file
    if not parquet_path.is_file():
        raise SFOTrendError("accepted DataSF snapshot Parquet file is unavailable")

    expected_checksum = manifest.get("content_sha256")
    if not isinstance(expected_checksum, str) or len(expected_checksum) != 64:
        raise SFOTrendError("DataSF snapshot manifest has an invalid content checksum")
    try:
        with parquet_path.open("rb") as handle:
            actual_checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise SFOTrendError("accepted DataSF snapshot Parquet file is unavailable") from exc
    if actual_checksum != expected_checksum:
        raise SFOTrendError("accepted DataSF snapshot checksum does not match its manifest")
    return manifest, parquet_path


def _source_from_manifest(manifest: dict[str, object]) -> SnapshotSource:
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise SFOTrendError("DataSF snapshot manifest has invalid source metadata")
    snapshot_id = manifest.get("snapshot_id")
    retrieved_at_raw = manifest.get("retrieved_at_utc")
    dataset_id = source.get("dataset_id")
    name = source.get("name")
    url = source.get("url")
    if not all(isinstance(value, str) and value for value in (snapshot_id, dataset_id, name, url, retrieved_at_raw)):
        raise SFOTrendError("DataSF snapshot manifest has incomplete source metadata")
    try:
        retrieved_at = datetime.fromisoformat(retrieved_at_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SFOTrendError("DataSF snapshot manifest has an invalid retrieval time") from exc
    if retrieved_at.tzinfo is None:
        raise SFOTrendError("DataSF snapshot retrieval time must include a timezone")
    return SnapshotSource(
        snapshot_id=snapshot_id,
        dataset_id=dataset_id,
        name=name,
        url=url,
        period="2023-2024",
        retrieved_at=retrieved_at.astimezone(timezone.utc),
    )


def _query_enplaned_cells(parquet_path: Path) -> dict[tuple[str, str], int]:
    rows = _query(
        parquet_path,
        "SELECT activity_period, geo_summary, sum(passenger_count) "
        "FROM read_parquet(?) WHERE activity_type_code = 'Enplaned' "
        "GROUP BY activity_period, geo_summary ORDER BY activity_period, geo_summary",
    )
    cells: dict[tuple[str, str], int] = {}
    for period, geography, passengers in rows:
        if not isinstance(period, str) or not isinstance(geography, str):
            raise SFOTrendError("accepted DataSF snapshot has invalid Enplaned dimensions")
        if not isinstance(passengers, int) or passengers < 0:
            raise SFOTrendError("accepted DataSF snapshot has an invalid Enplaned passenger total")
        cells[(period, geography)] = passengers
    return cells


def _query_annual_source_totals(parquet_path: Path) -> dict[int, int]:
    rows = _query(
        parquet_path,
        "SELECT CAST(substr(activity_period, 1, 4) AS INTEGER), sum(passenger_count) "
        "FROM read_parquet(?) WHERE activity_type_code = 'Enplaned' "
        "AND geo_summary IN ('Domestic', 'International') GROUP BY 1 ORDER BY 1",
    )
    totals = {year: passengers for year, passengers in rows}
    if set(totals) != {2023, 2024} or any(
        not isinstance(passengers, int) or passengers < 0 for passengers in totals.values()
    ):
        raise SFOTrendError("accepted DataSF snapshot has invalid annual source totals")
    return totals


def _query(parquet_path: Path, sql: str) -> list[tuple[object, ...]]:
    connection = duckdb.connect()
    try:
        return connection.execute(sql, [str(parquet_path)]).fetchall()
    except duckdb.Error as exc:
        raise SFOTrendError("accepted DataSF snapshot could not be read") from exc
    finally:
        connection.close()


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SFOTrendError(f"{label} is unavailable or invalid") from exc
    if not isinstance(payload, dict):
        raise SFOTrendError(f"{label} must be a JSON object")
    return payload
