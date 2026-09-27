"""Annual traffic metrics over the accepted local T-100 snapshot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, cast

import duckdb

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "t100"
_YEARS = (2023, 2024)
_EXPECTED_MONTHS = frozenset(range(1, 13))


class TrafficCalculationError(RuntimeError):
    """The accepted T-100 snapshot cannot be opened or identified safely."""


@dataclass(frozen=True, slots=True)
class T100SnapshotSource:
    snapshot_id: str
    table: str
    name: str
    url: str
    period: str
    imported_at: datetime


@dataclass(frozen=True, slots=True)
class AnnualCoverage:
    year: int
    observed_months: tuple[int, ...]
    missing_months: tuple[int, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class MetricResult:
    value: int | float | None
    status: Literal["ok", "unavailable"]
    reason: str | None


@dataclass(frozen=True, slots=True)
class AnnualTraffic:
    year: int
    coverage: AnnualCoverage
    passengers: MetricResult
    seats: MetricResult
    performed_departures: MetricResult
    occupancy_percent: MetricResult


@dataclass(frozen=True, slots=True)
class PassengerGrowth:
    baseline_year: int
    comparison_year: int
    percent: float | None
    status: Literal["ok", "unavailable"]
    reason: str | None


@dataclass(frozen=True, slots=True)
class TrafficResult:
    airport: str
    source: T100SnapshotSource
    annual: tuple[AnnualTraffic, ...]
    growth: PassengerGrowth


def calculate_traffic(airport: str, data_root: Path = DEFAULT_DATA_ROOT) -> TrafficResult:
    """Calculate 2023/24 origin traffic while keeping invalid metrics unavailable."""
    manifest, parquet_path, source = load_t100_snapshot(Path(data_root))
    airport_id = _validated_airport(airport, manifest)
    rows = _query_rows(parquet_path, airport_id)
    return _result_from_rows(airport_id, source, rows)


def calculate_traffic_batch(
    airports: Iterable[str], data_root: Path = DEFAULT_DATA_ROOT,
) -> dict[str, TrafficResult]:
    """Read one verified snapshot once for canonical airport keys and both years.

    Duplicate aliases collapse to one result in first-request order. Missing rows
    retain unavailable annual metrics; an unsupported airport rejects the batch.
    """
    requested = list(airports)
    if not requested:
        return {}
    manifest, parquet_path, source = load_t100_snapshot(Path(data_root))
    airport_ids = list(dict.fromkeys(_validated_airport(airport, manifest) for airport in requested))
    grouped = _query_batch_rows(parquet_path, airport_ids)
    return {
        airport: _result_from_rows(airport, source, grouped.get(airport, []))
        for airport in airport_ids
    }


def _result_from_rows(
    airport: str, source: T100SnapshotSource, rows: list[tuple[object, ...]],
) -> TrafficResult:
    annual = tuple(_calculate_year(year, rows) for year in _YEARS)
    by_year = {item.year: item for item in annual}
    growth = _growth(by_year[2023].passengers, by_year[2024].passengers)
    return TrafficResult(airport=airport, source=source, annual=annual, growth=growth)


def load_t100_snapshot(
    data_root: Path,
) -> tuple[dict[str, object], Path, T100SnapshotSource]:
    """Resolve and checksum the accepted T-100 snapshot selected by current.json."""
    pointer = _read_json(data_root / "current.json", "T-100 current snapshot pointer")
    snapshot_id = pointer.get("snapshot_id")
    manifest_relative = pointer.get("manifest")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise TrafficCalculationError("T-100 current snapshot pointer has no snapshot ID")
    expected_manifest = f"snapshots/{snapshot_id}/manifest.json"
    if manifest_relative != expected_manifest:
        raise TrafficCalculationError("T-100 current snapshot pointer has an invalid manifest path")

    manifest_path = data_root / expected_manifest
    manifest = _read_json(manifest_path, "T-100 snapshot manifest")
    if manifest.get("snapshot_id") != snapshot_id:
        raise TrafficCalculationError("T-100 snapshot manifest does not match the current pointer")
    if manifest.get("validation_status") != "accepted":
        raise TrafficCalculationError("T-100 snapshot is not marked accepted")

    parquet_file = manifest.get("parquet_file")
    if not isinstance(parquet_file, str) or not parquet_file or Path(parquet_file).name != parquet_file:
        raise TrafficCalculationError("T-100 snapshot manifest has an invalid Parquet filename")
    parquet_path = manifest_path.parent / parquet_file
    if not parquet_path.is_file():
        raise TrafficCalculationError("accepted T-100 snapshot Parquet file is unavailable")

    expected_checksum = manifest.get("parquet_sha256")
    if not isinstance(expected_checksum, str) or len(expected_checksum) != 64:
        raise TrafficCalculationError("T-100 snapshot manifest has an invalid Parquet checksum")
    try:
        with parquet_path.open("rb") as handle:
            actual_checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise TrafficCalculationError("accepted T-100 snapshot Parquet file is unavailable") from exc
    if actual_checksum != expected_checksum:
        raise TrafficCalculationError("accepted T-100 snapshot checksum does not match its manifest")
    return manifest, parquet_path, _source_from_manifest(manifest)


def _calculate_year(year: int, rows: list[tuple[object, ...]]) -> AnnualTraffic:
    year_rows = [row for row in rows if row[0] == year]
    observed = tuple(sorted({row[1] for row in year_rows if isinstance(row[1], int)}))
    missing = tuple(sorted(_EXPECTED_MONTHS.difference(observed)))
    coverage = AnnualCoverage(year, observed, missing, not missing and set(observed) == _EXPECTED_MONTHS)
    if not coverage.complete:
        reason = f"{year} coverage is incomplete; missing months: {', '.join(map(str, missing))}"
        unavailable = MetricResult(None, "unavailable", reason)
        return AnnualTraffic(year, coverage, unavailable, unavailable, unavailable, unavailable)

    passengers = _sum_count((row[2] for row in year_rows), "passengers", year)
    seats = _sum_count((row[3] for row in year_rows), "seats", year)
    departures = _sum_count((row[4] for row in year_rows), "performed departures", year)
    if passengers.status != "ok":
        occupancy = MetricResult(None, "unavailable", passengers.reason)
    elif seats.status != "ok":
        occupancy = MetricResult(None, "unavailable", seats.reason)
    elif seats.value == 0:
        occupancy = MetricResult(None, "unavailable", f"{year} seats are zero; occupancy is undefined")
    else:
        occupancy = MetricResult(100.0 * int(passengers.value) / int(seats.value), "ok", None)
    return AnnualTraffic(year, coverage, passengers, seats, departures, occupancy)


def _sum_count(values: Iterable[object], label: str, year: int) -> MetricResult:
    total = 0
    for value in values:
        parsed = _nonnegative_integer(value)
        if parsed is None:
            return MetricResult(None, "unavailable", f"{year} {label} contain an invalid count")
        total += parsed
    return MetricResult(total, "ok", None)


def _nonnegative_integer(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        return None
    return int(number)


def _growth(baseline: MetricResult, comparison: MetricResult) -> PassengerGrowth:
    if baseline.status != "ok":
        return PassengerGrowth(2023, 2024, None, "unavailable", baseline.reason)
    if comparison.status != "ok":
        return PassengerGrowth(2023, 2024, None, "unavailable", comparison.reason)
    if baseline.value == 0:
        return PassengerGrowth(
            2023, 2024, None, "unavailable", "2023 passengers are zero; growth is undefined"
        )
    percent = 100.0 * (int(comparison.value) - int(baseline.value)) / int(baseline.value)
    return PassengerGrowth(2023, 2024, percent, "ok", None)


def _validated_airport(airport: str, manifest: dict[str, object]) -> str:
    airport_id = airport.strip().upper()
    population = manifest.get("population")
    origins = population.get("origins") if isinstance(population, dict) else None
    if not isinstance(origins, list) or airport_id not in origins:
        raise TrafficCalculationError(f"airport {airport_id or '<empty>'} is outside the accepted population")
    return airport_id


def _source_from_manifest(manifest: dict[str, object]) -> T100SnapshotSource:
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise TrafficCalculationError("T-100 snapshot manifest has invalid source metadata")
    fields = (
        manifest.get("snapshot_id"),
        source.get("table"),
        source.get("name"),
        source.get("url"),
        manifest.get("imported_at_utc"),
    )
    if not all(isinstance(value, str) and value for value in fields):
        raise TrafficCalculationError("T-100 snapshot manifest has incomplete source metadata")
    snapshot_id, table, name, url, imported_raw = cast(tuple[str, str, str, str, str], fields)
    try:
        imported_at = datetime.fromisoformat(imported_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrafficCalculationError("T-100 snapshot manifest has an invalid import time") from exc
    if imported_at.tzinfo is None:
        raise TrafficCalculationError("T-100 snapshot import time must include a timezone")
    return T100SnapshotSource(
        snapshot_id=snapshot_id,
        table=table,
        name=name,
        url=url,
        period="2023-2024",
        imported_at=imported_at.astimezone(timezone.utc),
    )


def _query_rows(parquet_path: Path, airport: str) -> list[tuple[object, ...]]:
    connection = duckdb.connect()
    try:
        return connection.execute(
            "SELECT year, month, passengers, seats, departures_performed "
            "FROM read_parquet(?) WHERE origin = ? AND year IN (2023, 2024) "
            "ORDER BY year, month",
            [str(parquet_path), airport],
        ).fetchall()
    except duckdb.Error as exc:
        raise TrafficCalculationError("accepted T-100 snapshot could not be read") from exc
    finally:
        connection.close()


def _query_batch_rows(
    parquet_path: Path, airports: list[str],
) -> dict[str, list[tuple[object, ...]]]:
    connection = duckdb.connect()
    try:
        # Preserve individual null/invalid values for the same exact-integer
        # validation as single queries; SQL SUM would silently ignore nulls.
        grouped = connection.execute(
            "SELECT origin, list(row(year, month, passengers, seats, departures_performed) "
            "ORDER BY year, month) FROM read_parquet(?) "
            "WHERE origin IN (SELECT unnest(?)) AND year IN (2023, 2024) GROUP BY origin",
            [str(parquet_path), airports],
        ).fetchall()
        return dict(grouped)
    except duckdb.Error as exc:
        raise TrafficCalculationError("accepted T-100 snapshot could not be read") from exc
    finally:
        connection.close()


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrafficCalculationError(f"{label} is unavailable or invalid") from exc
    if not isinstance(payload, dict):
        raise TrafficCalculationError(f"{label} must be a JSON object")
    return payload
