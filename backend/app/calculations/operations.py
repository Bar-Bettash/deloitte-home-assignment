"""Origin operational indicators from the accepted, immutable FGJ snapshot."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import duckdb
from app.sources.ontime import AIRPORTS, DEFAULT_DATA_ROOT


class OperationsCalculationError(RuntimeError):
    """The requested scope or accepted snapshot cannot be used safely."""


@dataclass(frozen=True, slots=True)
class OperationsSource:
    snapshot_id: str
    name: str
    table: str
    url: str
    imported_at: datetime
    period: str = "2024"


@dataclass(frozen=True, slots=True)
class OperationalMetric:
    value: float | None
    status: Literal["ok", "unavailable"]
    numerator: int | float
    denominator: int
    eligible_count: int
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class OperationsResult:
    airport: str
    year: int
    source: OperationsSource
    observed_months: tuple[int, ...]
    missing_months: tuple[int, ...]
    carriers: tuple[str, ...]
    scheduled_count: int
    invalid_row_count: int
    cancellation_rate: OperationalMetric
    diversion_rate: OperationalMetric
    departure_delay_minutes: OperationalMetric
    taxi_out_minutes: OperationalMetric
    population: str = "Domestic reporting-carrier scheduled departures at origin; not all airlines or international traffic"


def calculate_operations(
    airport: str, data_root: Path = DEFAULT_DATA_ROOT, *, year: int = 2024
) -> OperationsResult:
    """Return percentages and independent non-null means; never impute missing data."""
    airport = airport.strip().upper()
    if airport not in AIRPORTS or type(year) is not int or year != 2024:
        raise OperationsCalculationError("operations support LAX/SNA/SFO in CY2024 only")
    manifest, parquet, source = _load_snapshot(Path(data_root))
    request = manifest.get("request", {})
    if request.get("year") != year or airport not in request.get("origin_airports", []):
        raise OperationsCalculationError("requested scope is outside the accepted snapshot")
    with duckdb.connect() as connection:
        try:
            rows = connection.execute(
                "SELECT month, reporting_airline, cancelled, diverted, flights, "
                "dep_delay_minutes, taxi_out FROM read_parquet(?) WHERE origin = ? AND year = ?",
                [str(parquet), airport, year],
            ).fetchall()
        except duckdb.Error as exc:
            raise OperationsCalculationError("accepted on-time snapshot cannot be queried") from exc
    months = tuple(sorted({row[0] for row in rows}))
    missing = tuple(month for month in range(1, 13) if month not in months)
    valid = [row for row in rows if row[2] in (0, 1) and row[3] in (0, 1) and row[4] == 1]
    eligible = [row for row in valid if row[2] == 0 and row[3] == 0]
    coverage = manifest.get("coverage", {})
    incomplete = missing or any(
        coverage.get(f"{airport}-{month:02d}", 0) <= 0 for month in range(1, 13)
    )
    reason = "insufficient data: incomplete annual origin coverage" if incomplete else None
    carriers = tuple(sorted({row[1] for row in valid if row[1]}))
    return OperationsResult(
        airport, year, source, months, missing, carriers, len(valid), len(rows) - len(valid),
        _metric(sum(row[2] for row in valid), len(valid), len(valid), reason, percent=True),
        _metric(sum(row[3] for row in valid), len(valid), len(valid), reason, percent=True),
        _mean(eligible, 5, reason), _mean(eligible, 6, reason),
    )


def _metric(numerator, denominator, eligible_count, reason=None, *, percent=False):
    reason = reason or ("insufficient data: zero observed denominator" if denominator == 0 else None)
    value = None if reason else (100.0 if percent else 1.0) * numerator / denominator
    return OperationalMetric(
        value, "unavailable" if reason else "ok", numerator, denominator, eligible_count, reason
    )


def _mean(rows, index, reason):
    values = [row[index] for row in rows if row[index] is not None]
    if any(not math.isfinite(value) or value < 0 for value in values):
        return _metric(0, len(values), len(rows), "insufficient data: invalid non-null field")
    return _metric(sum(values), len(values), len(rows), reason)


def _load_snapshot(data_root: Path):
    try:
        pointer = json.loads((data_root / "current.json").read_text())
        snapshot_id = pointer["snapshot_id"]
        if not isinstance(snapshot_id, str) or not snapshot_id or Path(snapshot_id).name != snapshot_id:
            raise ValueError("invalid snapshot ID")
        relative = f"snapshots/{snapshot_id}/manifest.json"
        if pointer["manifest"] != relative:
            raise ValueError("invalid manifest pointer")
        manifest_path = data_root / relative
        manifest = json.loads(manifest_path.read_text())
        if manifest["snapshot_id"] != snapshot_id or manifest["validation_status"] != "accepted":
            raise ValueError("snapshot is not accepted")
        if manifest["parquet_file"] != "data.parquet" or manifest["source"]["table"] != "FGJ":
            raise ValueError("invalid source or Parquet path")
        parquet = manifest_path.parent / "data.parquet"
        if not parquet.resolve().is_relative_to(data_root.resolve()):
            raise ValueError("snapshot escapes data root")
        with parquet.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != manifest["parquet_sha256"]:
                raise ValueError("snapshot checksum mismatch")
        imported = datetime.fromisoformat(manifest["imported_at_utc"].replace("Z", "+00:00"))
        if imported.tzinfo is None:
            raise ValueError("import time requires timezone")
        source = OperationsSource(
            snapshot_id, manifest["source"]["name"], "FGJ", manifest["source"]["index_url"], imported
        )
        return manifest, parquet, source
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise OperationsCalculationError("on-time accepted snapshot is missing or invalid") from exc
