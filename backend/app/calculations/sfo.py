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
from app.sources.bundle import BundleContext, SnapshotRef

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "datasf"
DEFAULT_T100_DATA_ROOT = DEFAULT_T100_ROOT
DEFAULT_OPERATIONS_DATA_ROOT = DEFAULT_OPERATIONS_ROOT
_HISTORICAL_YEARS = (2023, 2024)
_GEOGRAPHIES = ("Domestic", "International")


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
    source_id: Literal["datasf", "t100", "ontime"]
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


def calculate_sfo_enplaned_trend(
    data_root: Path = DEFAULT_DATA_ROOT,
    *,
    bundle: BundleContext | None = None,
) -> SFOTrendResult:
    """Return the combined SFO enplaned trend for the selected two-year period."""
    years = _selected_years(bundle)
    periods = tuple(f"{year}{month:02d}" for year in years for month in range(1, 13))
    expected_cells = {(period, geography) for period in periods for geography in _GEOGRAPHIES}
    manifest, parquet_path = _load_snapshot(Path(data_root), bundle)
    if bundle is None:
        source = _source_from_manifest(manifest)
        cells = _query_enplaned_cells(parquet_path)
    else:
        source = _source_from_manifest(manifest, years)
        cells = _query_enplaned_cells(parquet_path, years)
    if set(cells) != expected_cells:
        raise SFOTrendError("accepted DataSF snapshot must contain all 48 Enplaned month/geography cells")

    series = tuple(
        MonthlyPassengerTotal(
            period=period,
            passengers=sum(cells[(period, geography)] for geography in _GEOGRAPHIES),
        )
        for period in periods
    )
    derived_annual_totals = {
        year: sum(point.passengers for point in series if point.period.startswith(str(year)))
        for year in years
    }
    source_annual_totals = (
        _query_annual_source_totals(parquet_path)
        if bundle is None
        else _query_annual_source_totals(parquet_path, years)
    )
    if source_annual_totals != derived_annual_totals:
        raise SFOTrendError("combined monthly series does not reconcile to annual source totals")

    annual_totals = tuple(
        AnnualPassengerTotal(year=year, passengers=source_annual_totals[year])
        for year in years
    )
    baseline_year, comparison_year = years
    baseline = source_annual_totals[baseline_year]
    comparison = source_annual_totals[comparison_year]
    if baseline == 0:
        growth = PassengerGrowth(
            baseline_year=baseline_year,
            comparison_year=comparison_year,
            percent=None,
            status="unavailable",
            reason=f"{baseline_year} enplaned passenger total is zero; growth is undefined",
        )
    else:
        growth = PassengerGrowth(
            baseline_year=baseline_year,
            comparison_year=comparison_year,
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


def _selected_years(bundle: BundleContext | None) -> tuple[int, int]:
    return _HISTORICAL_YEARS if bundle is None else (bundle.baseline_year, bundle.comparison_year)


def _load_snapshot(
    data_root: Path, bundle: BundleContext | None
) -> tuple[dict[str, object], Path]:
    return _load_accepted_snapshot(data_root) if bundle is None else _load_bundled_snapshot(bundle)


def _load_bundled_snapshot(bundle: BundleContext) -> tuple[dict[str, object], Path]:
    ref = bundle.sources.get("datasf")
    years = _selected_years(bundle)
    if ref is None or ref.years != years:
        raise SFOTrendError("bundle DataSF period does not match the selected period")
    if _sha256(ref.manifest_path, "bundle DataSF manifest") != ref.manifest_sha256:
        raise SFOTrendError("bundle DataSF manifest checksum does not match its reference")
    manifest = _read_json(ref.manifest_path, "bundle DataSF manifest")
    _validate_bundled_manifest(manifest, ref, years)
    if _sha256(ref.data_path, "bundle DataSF Parquet file") != ref.content_sha256:
        raise SFOTrendError("bundle DataSF snapshot checksum does not match its reference")
    return manifest, ref.data_path


def _validate_bundled_manifest(
    manifest: dict[str, object], ref: SnapshotRef, years: tuple[int, int]
) -> None:
    parquet_file = manifest.get("parquet_file")
    expected_path = (
        ref.manifest_path.parent / parquet_file
        if isinstance(parquet_file, str) and Path(parquet_file).name == parquet_file
        else None
    )
    scope = manifest.get("scope")
    expected_start, expected_end = f"{years[0]}01", f"{years[1]}12"
    if (
        manifest.get("snapshot_id") != ref.snapshot_id
        or manifest.get("validation_status") not in {"accepted", "staged"}
        or manifest.get("content_sha256") != ref.content_sha256
        or expected_path is None
        or expected_path.resolve() != ref.data_path.resolve()
        or not isinstance(scope, dict)
        or scope.get("activity_period_start") != expected_start
        or scope.get("activity_period_end") != expected_end
    ):
        raise SFOTrendError("bundle DataSF snapshot metadata does not match its reference")


def _sha256(path: Path, label: str) -> str:
    try:
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise SFOTrendError(f"{label} is unavailable") from exc


def calculate_sfo_pressure(
    datasf_root: Path = DEFAULT_DATA_ROOT,
    t100_root: Path = DEFAULT_T100_DATA_ROOT,
    operations_root: Path = DEFAULT_OPERATIONS_DATA_ROOT,
    *,
    bundle: BundleContext | None = None,
) -> SFOPressureBundle:
    """Combine one accepted snapshot per source while retaining independent results.

    DataSF enplanements remain a separately scoped trend. The growth gap and
    occupancy are calculated only from SFO's matching T-100 origin population.
    """
    enplaned_call = lambda: calculate_sfo_enplaned_trend(Path(datasf_root))
    traffic_call = lambda: calculate_traffic("SFO", Path(t100_root))
    operations_call = lambda: calculate_operations("SFO", Path(operations_root))
    if bundle is not None:
        enplaned_call = lambda: calculate_sfo_enplaned_trend(Path(datasf_root), bundle=bundle)
        traffic_call = lambda: calculate_traffic("SFO", Path(t100_root), bundle=bundle)
        operations_call = lambda: calculate_operations("SFO", Path(operations_root), bundle=bundle)
    enplaned = _component(
        enplaned_call,
        SFOTrendError,
        "DataSF accepted SFO snapshot is unavailable",
    )
    traffic = _component(
        traffic_call,
        TrafficCalculationError,
        "accepted T-100 SFO snapshot is unavailable",
    )
    operations_year = 2024 if bundle is None else bundle.comparison_year
    operations = _component(
        operations_call,
        OperationsCalculationError,
        f"accepted CY{operations_year} SFO on-time snapshot is unavailable",
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
        # Same "ontime" kind as the operations workflow, so one snapshot has one source ID.
        lineage.append(SFOPressureLineage("ontime", source.snapshot_id, source.name, source.url, source.period))

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
    """Compare passenger and seat growth from the same T-100 origin periods."""
    by_year = {item.year: item for item in traffic.annual}
    years = tuple(sorted(by_year))
    if len(years) != 2:
        return _unavailable_gap("T-100 growth gap requires exactly two annual periods")
    baseline_year, comparison_year = years
    baseline = by_year.get(baseline_year)
    comparison = by_year.get(comparison_year)
    if baseline is None or comparison is None:
        return _unavailable_gap("T-100 selected annual periods are not both available")
    if not baseline.coverage.complete or not comparison.coverage.complete:
        return _unavailable_gap(
            f"T-100 growth gap requires complete {baseline_year} and {comparison_year} periods"
        )
    measures = (baseline.passengers, comparison.passengers, baseline.seats, comparison.seats)
    if any(item.status != "ok" or item.value is None for item in measures):
        return _unavailable_gap("T-100 passengers and seats must be valid for both years")
    baseline_passengers, comparison_passengers = baseline.passengers.value, comparison.passengers.value
    baseline_seats, comparison_seats = baseline.seats.value, comparison.seats.value
    if baseline_passengers <= 0 or baseline_seats <= 0:
        return _unavailable_gap(
            f"T-100 {baseline_year} passenger and seat baselines must be positive"
        )
    passenger_growth = 100.0 * (comparison_passengers / baseline_passengers - 1.0)
    seat_growth = 100.0 * (comparison_seats / baseline_seats - 1.0)
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


def _source_from_manifest(
    manifest: dict[str, object], years: tuple[int, int] = _HISTORICAL_YEARS
) -> SnapshotSource:
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
        period=f"{years[0]}-{years[1]}",
        retrieved_at=retrieved_at.astimezone(timezone.utc),
    )


def _query_enplaned_cells(
    parquet_path: Path, years: tuple[int, int] = _HISTORICAL_YEARS
) -> dict[tuple[str, str], int]:
    rows = _query(
        parquet_path,
        "SELECT activity_period, geo_summary, sum(passenger_count) "
        "FROM read_parquet(?) WHERE activity_type_code = 'Enplaned' "
        "AND geo_summary IN ('Domestic', 'International') "
        "AND substr(activity_period, 1, 4) IN (?, ?) "
        "GROUP BY activity_period, geo_summary ORDER BY activity_period, geo_summary",
        (str(years[0]), str(years[1])),
    )
    cells: dict[tuple[str, str], int] = {}
    for period, geography, passengers in rows:
        if not isinstance(period, str) or not isinstance(geography, str):
            raise SFOTrendError("accepted DataSF snapshot has invalid Enplaned dimensions")
        if not isinstance(passengers, int) or passengers < 0:
            raise SFOTrendError("accepted DataSF snapshot has an invalid Enplaned passenger total")
        cells[(period, geography)] = passengers
    return cells


def _query_annual_source_totals(
    parquet_path: Path, years: tuple[int, int] = _HISTORICAL_YEARS
) -> dict[int, int]:
    rows = _query(
        parquet_path,
        "SELECT CAST(substr(activity_period, 1, 4) AS INTEGER), sum(passenger_count) "
        "FROM read_parquet(?) WHERE activity_type_code = 'Enplaned' "
        "AND geo_summary IN ('Domestic', 'International') "
        "AND substr(activity_period, 1, 4) IN (?, ?) GROUP BY 1 ORDER BY 1",
        (str(years[0]), str(years[1])),
    )
    totals = {year: passengers for year, passengers in rows}
    if set(totals) != set(years) or any(
        not isinstance(passengers, int) or passengers < 0 for passengers in totals.values()
    ):
        raise SFOTrendError("accepted DataSF snapshot has invalid annual source totals")
    return totals


def _query(
    parquet_path: Path, sql: str, parameters: tuple[object, ...] = ()
) -> list[tuple[object, ...]]:
    connection = duckdb.connect()
    try:
        return connection.execute(sql, [str(parquet_path), *parameters]).fetchall()
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
