"""Acquire, validate, and publish the bounded BTS on-time population."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import duckdb
import httpx

SOURCE_NAME = "BTS Reporting Carrier On-Time Performance"
SOURCE_TABLE = "FGJ"
BASE_URL = "https://transtats.bts.gov/PREZIP"
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "ontime"
AIRPORTS = ("LAX", "SFO", "SNA")
RECENT_AIRPORTS = ("ANC", *AIRPORTS)
MONTHS = tuple(range(1, 13))
ACCEPTED_SOURCE_YEAR = 2024
STAGED_SOURCE_YEAR = 2025
SOURCE_YEARS = (ACCEPTED_SOURCE_YEAR, STAGED_SOURCE_YEAR)
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_CSV_BYTES = 500 * 1024 * 1024
MAX_ROWS_PER_ARCHIVE = 700_000
RANGE_BYTES = 8 * 1024 * 1024
KNOWN_JANUARY_BYTES = 27_573_265
KNOWN_JANUARY_SHA256 = "fe089b45523f9d4ac0ccd0e176a543d274e914ee5ae5bd384dbaafc7dc06ebfd"

IDENTITY_FIELDS = (
    "FlightDate",
    "Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "OriginAirportID",
    "DestAirportID",
    "CRSDepTime",
)
SELECTED_FIELDS = (
    "Year", "Month", *IDENTITY_FIELDS, "CRSArrTime", "Origin", "Dest", "DepTime",
    "DepDelay", "DepDelayMinutes", "TaxiOut", "TaxiIn", "Cancelled", "CancellationCode",
    "Diverted", "CRSElapsedTime", "ActualElapsedTime", "AirTime", "Flights", "Distance",
    "CarrierDelay", "WeatherDelay", "NASDelay", "SecurityDelay", "LateAircraftDelay",
)
OUTPUT_FIELDS = (
    "year", "month", "flight_date", "reporting_airline", "flight_number",
    "origin_airport_id", "dest_airport_id", "crs_dep_time", "crs_arr_time", "origin", "dest",
    "dep_time", "dep_delay", "dep_delay_minutes", "taxi_out", "taxi_in", "cancelled",
    "cancellation_code", "diverted", "crs_elapsed_time", "actual_elapsed_time", "air_time",
    "flights", "distance", "carrier_delay", "weather_delay", "nas_delay", "security_delay",
    "late_aircraft_delay",
)
PARQUET_SCHEMA = (
    ("year", "INTEGER"), ("month", "INTEGER"), ("flight_date", "DATE"),
    ("reporting_airline", "VARCHAR"), ("flight_number", "INTEGER"),
    ("origin_airport_id", "INTEGER"), ("dest_airport_id", "INTEGER"),
    ("crs_dep_time", "INTEGER"), ("crs_arr_time", "INTEGER"), ("origin", "VARCHAR"),
    ("dest", "VARCHAR"), ("dep_time", "INTEGER"), ("dep_delay", "DOUBLE"),
    ("dep_delay_minutes", "DOUBLE"), ("taxi_out", "DOUBLE"), ("taxi_in", "DOUBLE"),
    ("cancelled", "INTEGER"), ("cancellation_code", "VARCHAR"), ("diverted", "INTEGER"),
    ("crs_elapsed_time", "DOUBLE"), ("actual_elapsed_time", "DOUBLE"), ("air_time", "DOUBLE"),
    ("flights", "INTEGER"), ("distance", "DOUBLE"), ("carrier_delay", "DOUBLE"),
    ("weather_delay", "DOUBLE"), ("nas_delay", "DOUBLE"), ("security_delay", "DOUBLE"),
    ("late_aircraft_delay", "DOUBLE"),
)


class OnTimeError(RuntimeError):
    """The on-time inputs cannot produce an accepted snapshot."""


@dataclass(frozen=True, slots=True)
class ArchiveMetadata:
    month: int
    filename: str
    url: str
    zip_bytes: int
    sha256: str
    csv_member: str
    csv_bytes: int
    csv_sha256: str
    csv_crc32: str
    source_rows: int
    scoped_rows: int
    last_modified: str | None = None
    retrieved_at_utc: str | None = None


def archive_filename(month: int, source_year: int = ACCEPTED_SOURCE_YEAR) -> str:
    _validate_source_year(source_year)
    return f"On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{source_year}_{month}.zip"


def archive_url(month: int, source_year: int = ACCEPTED_SOURCE_YEAR) -> str:
    return f"{BASE_URL}/{archive_filename(month, source_year)}"


def acquire_archives(
    output_dir: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    months: tuple[int, ...] = MONTHS,
    deadline_seconds: float = 300.0,
    source_year: int = ACCEPTED_SOURCE_YEAR,
) -> list[dict[str, object]]:
    """Download complete archives sequentially; partial files are never accepted."""
    return asyncio.run(
        _acquire_archives_async(
            output_dir, transport=transport, months=months, deadline_seconds=deadline_seconds,
            source_year=source_year,
        )
    )


async def _acquire_archives_async(
    output_dir: Path,
    *,
    transport: httpx.AsyncBaseTransport | None,
    months: tuple[int, ...],
    deadline_seconds: float,
    source_year: int,
) -> list[dict[str, object]]:
    _validate_source_year(source_year)
    output_dir.mkdir(parents=True, exist_ok=True)
    acquired: list[dict[str, object]] = []
    existing = _read_acquisition(output_dir)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0), follow_redirects=False, transport=transport
        ) as http:
            for month in months:
                acquired.append(
                    await _acquire_one(
                        http,
                        output_dir,
                        month,
                        deadline_seconds=deadline_seconds,
                        existing=existing.get(month),
                        source_year=source_year,
                    )
                )
        _write_json_replace(output_dir / "acquisition.json", {"archives": acquired})
        return acquired
    except TimeoutError as exc:
        raise OnTimeError("BTS archive acquisition exceeded its whole-request deadline") from exc


async def _acquire_one(
    http: httpx.AsyncClient,
    output_dir: Path,
    month: int,
    *,
    deadline_seconds: float,
    existing: dict[str, object] | None,
    source_year: int,
) -> dict[str, object]:
    if deadline_seconds <= 0:
        raise ValueError("deadline_seconds must be positive")
    url = archive_url(month, source_year)
    filename = archive_filename(month, source_year)
    target = output_dir / filename
    if (
        target.is_file()
        and existing
        and existing.get("filename") == filename
        and existing.get("url") == url
        and existing.get("zip_bytes") == target.stat().st_size
        and existing.get("sha256", existing.get("zip_sha256")) == _sha256(target)
    ):
        _validate_downloaded_zip(target, month)
        return existing
    if (
        source_year == ACCEPTED_SOURCE_YEAR
        and month == 1
        and target.is_file()
        and target.stat().st_size == KNOWN_JANUARY_BYTES
        and _sha256(target) == KNOWN_JANUARY_SHA256
    ):
        return _local_acquisition(month, target, url)
    temporary = output_dir / f".{filename}.{uuid4().hex}.part"
    try:
        async with asyncio.timeout(deadline_seconds):
            head = await http.head(url)
            head.raise_for_status()
            advertised = _content_length(head.headers)
            if advertised <= 0 or advertised > MAX_ZIP_BYTES:
                raise OnTimeError(f"BTS archive {month} has an invalid advertised length")
            digest = hashlib.sha256()
            written = 0
            with temporary.open("xb") as handle:
                for start in range(0, advertised, RANGE_BYTES):
                    end = min(start + RANGE_BYTES - 1, advertised - 1)
                    expected_bytes = end - start + 1
                    async with http.stream(
                        "GET", url, headers={"Range": f"bytes={start}-{end}"}
                    ) as response:
                        response.raise_for_status()
                        expected_range = f"bytes {start}-{end}/{advertised}"
                        if response.status_code != 206 or response.headers.get("content-range") != expected_range:
                            raise OnTimeError(f"BTS archive {month} returned an invalid Content-Range")
                        if _content_length(response.headers) != expected_bytes:
                            raise OnTimeError(f"BTS archive {month} range length is invalid")
                        segment_bytes = 0
                        async for chunk in response.aiter_bytes():
                            segment_bytes += len(chunk)
                            written += len(chunk)
                            if segment_bytes > expected_bytes or written > advertised:
                                raise OnTimeError(f"BTS archive {month} exceeded its byte cap")
                            handle.write(chunk)
                            digest.update(chunk)
                        if segment_bytes != expected_bytes:
                            raise OnTimeError(f"BTS archive {month} range ended early")
                handle.flush()
                os.fsync(handle.fileno())
            if written != advertised:
                raise OnTimeError(f"BTS archive {month} ended before its advertised length")
            _validate_downloaded_zip(temporary, month)
            os.replace(temporary, target)
            return {
                "month": month,
                "filename": filename,
                "url": url,
                "zip_bytes": written,
                "sha256": digest.hexdigest(),
                "last_modified": head.headers.get("last-modified"),
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
    except TimeoutError:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise OnTimeError(f"BTS archive {month} acquisition failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def publish_ontime_snapshot(
    archive_dir: Path,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    imported_at: datetime | None = None,
    source_year: int = ACCEPTED_SOURCE_YEAR,
) -> dict[str, object]:
    """Materialize one complete year; only the historical year updates the accepted pointer."""
    _validate_source_year(source_year)
    import_time = imported_at or datetime.now(timezone.utc)
    if import_time.tzinfo is None:
        raise ValueError("imported_at must include a timezone")
    import_time = import_time.astimezone(timezone.utc)
    snapshot_root = data_root / "snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=snapshot_root))
    pointer_temp = data_root / f".current-{uuid4().hex}.json"
    try:
        airports = RECENT_AIRPORTS if source_year == STAGED_SOURCE_YEAR else AIRPORTS
        normalized = staging / "normalized.csv"
        archive_metadata, coverage, carriers, scoped_rows = _validate_to_csv(
            archive_dir, normalized, source_year, airports
        )
        expected_coverage = {(airport, month) for airport in airports for month in MONTHS}
        if set(coverage) != expected_coverage or any(count <= 0 for count in coverage.values()):
            raise OnTimeError("on-time airport-month coverage is incomplete")

        parquet_path = staging / "data.parquet"
        _write_parquet(normalized, parquet_path)
        _verify_parquet(parquet_path, scoped_rows, expected_coverage)
        normalized.unlink()
        parquet_sha256 = _sha256(parquet_path)
        source_identity = hashlib.sha256(
            "".join(item.sha256 for item in archive_metadata).encode("ascii")
        ).hexdigest()
        snapshot_id = "ontime-" + hashlib.sha256(
            f"{SOURCE_TABLE}:{source_identity}:{parquet_sha256}".encode("ascii")
        ).hexdigest()
        metadata: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "source": {"name": SOURCE_NAME, "table": SOURCE_TABLE, "index_url": f"{BASE_URL}/"},
            "request": {"year": source_year, "months": list(MONTHS), "origin_airports": list(airports)},
            "imported_at_utc": import_time.isoformat().replace("+00:00", "Z"),
            "archives": [asdict(item) for item in archive_metadata],
            "row_count": scoped_rows,
            "coverage": {
                f"{airport}-{month:02d}": coverage[(airport, month)]
                for airport, month in sorted(coverage)
            },
            "carriers": {airport: sorted(carriers[airport]) for airport in airports},
            "conditional_nulls_preserved": True,
            "identity_fields": list(IDENTITY_FIELDS),
            "parquet_file": "data.parquet",
            "parquet_sha256": parquet_sha256,
            "validation_status": "accepted" if source_year == ACCEPTED_SOURCE_YEAR else "staged",
        }
        _write_json(staging / "manifest.json", metadata)
        final_dir = snapshot_root / snapshot_id
        if final_dir.exists():
            _validate_existing_snapshot(
                final_dir, snapshot_id, parquet_sha256, str(metadata["validation_status"])
            )
            published = json.loads((final_dir / "manifest.json").read_text(encoding="utf-8"))
        else:
            os.replace(staging, final_dir)
            published = metadata
        if source_year == ACCEPTED_SOURCE_YEAR:
            _write_json(pointer_temp, {"snapshot_id": snapshot_id, "manifest": f"snapshots/{snapshot_id}/manifest.json"})
            os.replace(pointer_temp, data_root / "current.json")
        return published
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        pointer_temp.unlink(missing_ok=True)


def _validate_to_csv(
    archive_dir: Path,
    normalized_path: Path,
    source_year: int = ACCEPTED_SOURCE_YEAR,
    airports: tuple[str, ...] = AIRPORTS,
) -> tuple[list[ArchiveMetadata], Counter[tuple[str, int]], dict[str, set[str]], int]:
    acquisition = _read_acquisition(archive_dir)
    identities: dict[tuple[str, ...], tuple[str | None, ...]] = {}
    coverage: Counter[tuple[str, int]] = Counter()
    carriers = {airport: set() for airport in airports}
    archive_metadata: list[ArchiveMetadata] = []
    scoped_rows = 0
    with normalized_path.open("x", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(OUTPUT_FIELDS)
        for month in MONTHS:
            filename = archive_filename(month, source_year)
            flat_path = archive_dir / filename
            nested_path = archive_dir / str(month) / filename
            path = flat_path if flat_path.is_file() else nested_path
            if not path.is_file():
                raise OnTimeError(f"required on-time archive is missing for month {month}")
            if path.stat().st_size > MAX_ZIP_BYTES:
                raise OnTimeError(f"on-time archive {month} exceeds its byte cap")
            digest = _sha256(path)
            if (
                source_year == ACCEPTED_SOURCE_YEAR
                and month == 1
                and path.stat().st_size == KNOWN_JANUARY_BYTES
                and digest != KNOWN_JANUARY_SHA256
            ):
                raise OnTimeError("January archive does not match its qualified bytes")
            metadata = acquisition.get(month, {})
            if metadata and (
                metadata.get("zip_bytes") != path.stat().st_size
                or metadata.get("sha256", metadata.get("zip_sha256")) != digest
            ):
                raise OnTimeError(f"on-time archive {month} differs from acquisition metadata")
            member_name, csv_bytes, csv_sha256, csv_crc32, source_rows, month_scoped = _consume_archive(
                path, month, writer, identities, coverage, carriers, source_year, airports
            )
            scoped_rows += month_scoped
            archive_metadata.append(
                ArchiveMetadata(
                    month=month,
                    filename=path.name,
                    url=archive_url(month, source_year),
                    zip_bytes=path.stat().st_size,
                    sha256=digest,
                    csv_member=member_name,
                    csv_bytes=csv_bytes,
                    csv_sha256=csv_sha256,
                    csv_crc32=csv_crc32,
                    source_rows=source_rows,
                    scoped_rows=month_scoped,
                    last_modified=metadata.get("last_modified") if metadata else None,
                    retrieved_at_utc=metadata.get("retrieved_at_utc") if metadata else None,
                )
            )
    return archive_metadata, coverage, carriers, scoped_rows


def _consume_archive(
    path: Path,
    month: int,
    writer: csv.writer,
    identities: dict[tuple[str, ...], tuple[str | None, ...]],
    coverage: Counter[tuple[str, int]],
    carriers: dict[str, set[str]],
    source_year: int = ACCEPTED_SOURCE_YEAR,
    airports: tuple[str, ...] = AIRPORTS,
) -> tuple[str, int, str, str, int, int]:
    try:
        with zipfile.ZipFile(path) as archive:
            files = [info for info in archive.infolist() if not info.is_dir()]
            csv_members = [info for info in files if info.filename.lower().endswith(".csv")]
            if len(csv_members) != 1 or any(info.file_size > MAX_CSV_BYTES for info in files):
                raise OnTimeError(f"on-time archive {month} has invalid members")
            for info in files:
                if info is not csv_members[0]:
                    with archive.open(info) as extra:
                        while extra.read(1024 * 1024):
                            pass
            info = csv_members[0]
            with archive.open(info) as csv_raw:
                csv_sha256 = hashlib.file_digest(csv_raw, "sha256").hexdigest()
            csv_crc32 = f"{info.CRC:08x}"
            source_rows = 0
            scoped_rows = 0
            with archive.open(info) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
                reader = csv.DictReader(text)
                fieldnames = reader.fieldnames or []
                headers = set(fieldnames)
                if len(fieldnames) != len(headers):
                    raise OnTimeError(f"on-time archive {month} has duplicate CSV headers")
                if not set(SELECTED_FIELDS).issubset(headers):
                    raise OnTimeError(f"on-time archive {month} is missing required headers")
                for source_row in reader:
                    source_rows += 1
                    if source_rows > MAX_ROWS_PER_ARCHIVE:
                        raise OnTimeError(f"on-time archive {month} exceeds its row cap")
                    if None in source_row or any(value is None for value in source_row.values()):
                        raise OnTimeError(f"on-time archive {month} has malformed CSV row cells")
                    try:
                        row_year = _required_integer(source_row["Year"], "year")
                        row_month = _required_integer(source_row["Month"], "month")
                    except OnTimeError as exc:
                        raise OnTimeError(f"on-time archive {month} has an invalid period") from exc
                    if row_year != source_year or row_month != month:
                        raise OnTimeError(f"on-time archive {month} contains an out-of-period row")
                    if source_row["Origin"].strip() not in airports:
                        continue
                    normalized = _normalize_scoped_row(source_row, month, source_year)
                    identity = tuple(normalized[index] for index in range(2, 8))
                    payload = tuple(normalized[8:])
                    previous = identities.get(identity)
                    if previous is not None:
                        if previous != payload:
                            raise OnTimeError("on-time duplicate flight identity has conflicting values")
                        continue
                    identities[identity] = payload
                    writer.writerow(normalized)
                    airport = str(normalized[9])
                    coverage[(airport, month)] += 1
                    carriers[airport].add(str(normalized[3]))
                    scoped_rows += 1
            return info.filename, info.file_size, csv_sha256, csv_crc32, source_rows, scoped_rows
    except OnTimeError:
        raise
    except (zipfile.BadZipFile, RuntimeError, UnicodeDecodeError, csv.Error) as exc:
        raise OnTimeError(f"on-time archive {month} failed ZIP/CSV validation") from exc


def _normalize_scoped_row(
    row: dict[str, str], month: int, source_year: int = ACCEPTED_SOURCE_YEAR
) -> tuple[object, ...]:
    flight_date = row["FlightDate"].strip()
    try:
        parsed_date = date.fromisoformat(flight_date)
    except ValueError as exc:
        raise OnTimeError("on-time row has an invalid flight date") from exc
    if parsed_date.year != source_year or parsed_date.month != month:
        raise OnTimeError("on-time row flight date does not match its archive")
    carrier = row["Reporting_Airline"].strip()
    origin = row["Origin"].strip()
    dest = row["Dest"].strip()
    if not carrier or not origin or not dest:
        raise OnTimeError("on-time row has a blank identity field")
    cancelled = _binary(row["Cancelled"], "cancelled")
    diverted = _binary(row["Diverted"], "diverted")
    flights = _required_integer(row["Flights"], "flights")
    if flights != 1:
        raise OnTimeError("on-time row has an invalid flight count")
    return (
        source_year,
        month,
        flight_date,
        carrier,
        _positive_integer(row["Flight_Number_Reporting_Airline"], "flight number"),
        _positive_integer(row["OriginAirportID"], "origin airport ID"),
        _positive_integer(row["DestAirportID"], "destination airport ID"),
        _time_value(row["CRSDepTime"], required=True),
        _time_value(row["CRSArrTime"], required=True),
        origin,
        dest,
        _time_value(row["DepTime"], required=False),
        _number(row["DepDelay"], nonnegative=False),
        _number(row["DepDelayMinutes"]),
        _number(row["TaxiOut"]),
        _number(row["TaxiIn"]),
        cancelled,
        row["CancellationCode"].strip() or None,
        diverted,
        _number(row["CRSElapsedTime"]),
        _number(row["ActualElapsedTime"]),
        _number(row["AirTime"]),
        flights,
        _number(row["Distance"]),
        _number(row["CarrierDelay"]),
        _number(row["WeatherDelay"]),
        _number(row["NASDelay"]),
        _number(row["SecurityDelay"]),
        _number(row["LateAircraftDelay"]),
    )


def _number(value: str, *, nonnegative: bool = True, required: bool = False) -> float | None:
    text = value.strip()
    if not text:
        if required:
            raise OnTimeError("on-time row has a blank required number")
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise OnTimeError("on-time row has an invalid number") from exc
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise OnTimeError("on-time row has an invalid number")
    return number


def _required_integer(value: str, label: str) -> int:
    try:
        number = Decimal(value.strip())
    except (InvalidOperation, ValueError) as exc:
        raise OnTimeError(f"on-time row has an invalid {label}") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise OnTimeError(f"on-time row has an invalid {label}")
    return int(number)


def _positive_integer(value: str, label: str) -> int:
    number = _required_integer(value, label)
    if number <= 0:
        raise OnTimeError(f"on-time row has an invalid {label}")
    return number


def _binary(value: str, label: str) -> int:
    number = _required_integer(value, label)
    if number not in (0, 1):
        raise OnTimeError(f"on-time row has a nonbinary {label}")
    return number


def _time_value(value: str, *, required: bool) -> int | None:
    if not value.strip() and not required:
        return None
    number = _required_integer(value, "time")
    if number < 0 or number > 2400 or number % 100 >= 60:
        raise OnTimeError("on-time row has an invalid time")
    return number


def _write_parquet(normalized: Path, parquet: Path) -> None:
    columns = ", ".join(f'"{name}" {kind}' for name, kind in PARQUET_SCHEMA)
    connection = duckdb.connect()
    try:
        connection.execute(f"CREATE TABLE snapshot ({columns})")
        normalized_literal = "'" + str(normalized).replace("'", "''") + "'"
        connection.execute(
            f"COPY snapshot FROM {normalized_literal} "
            "(FORMAT CSV, HEADER TRUE, NULL '', DATEFORMAT '%Y-%m-%d')"
        )
        literal = "'" + str(parquet).replace("'", "''") + "'"
        connection.execute(f"COPY snapshot TO {literal} (FORMAT PARQUET, COMPRESSION ZSTD)")
    except duckdb.Error as exc:
        raise OnTimeError("on-time normalized data could not be materialized") from exc
    finally:
        connection.close()


def _verify_parquet(path: Path, expected_rows: int, expected_coverage: set[tuple[str, int]]) -> None:
    connection = duckdb.connect()
    try:
        schema = [(row[0], row[1]) for row in connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
        ).fetchall()]
        count = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
        coverage = set(connection.execute(
            "SELECT DISTINCT origin, month FROM read_parquet(?)", [str(path)]
        ).fetchall())
    finally:
        connection.close()
    if schema != list(PARQUET_SCHEMA) or count != expected_rows or coverage != expected_coverage:
        raise OnTimeError("on-time Parquet failed schema, row-count, or coverage verification")


def _validate_existing_snapshot(
    directory: Path, snapshot_id: str, checksum: str, validation_status: str
) -> None:
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OnTimeError("existing on-time snapshot manifest is invalid") from exc
    parquet = directory / "data.parquet"
    if (
        not parquet.is_file()
        or _sha256(parquet) != checksum
        or not isinstance(manifest, dict)
        or manifest.get("snapshot_id") != snapshot_id
        or manifest.get("parquet_sha256") != checksum
        or manifest.get("validation_status") != validation_status
    ):
        raise OnTimeError("existing on-time snapshot does not match its content identity")


def _validate_downloaded_zip(path: Path, month: int) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise OnTimeError(f"BTS archive {month} failed ZIP CRC validation")
    except zipfile.BadZipFile as exc:
        raise OnTimeError(f"BTS archive {month} is not a valid ZIP") from exc


def _read_acquisition(directory: Path) -> dict[int, dict[str, object]]:
    path = directory / "acquisition.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        archives = payload["archives"]
        return {int(item["month"]): item for item in archives}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise OnTimeError("on-time acquisition metadata is invalid") from exc


def _local_acquisition(month: int, path: Path, url: str) -> dict[str, object]:
    return {
        "month": month,
        "filename": path.name,
        "url": url,
        "zip_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "last_modified": None,
        "retrieved_at_utc": None,
    }


def _content_length(headers: httpx.Headers) -> int:
    try:
        return int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise OnTimeError("BTS response has no valid Content-Length") from exc


def _validate_source_year(source_year: int) -> None:
    if source_year not in SOURCE_YEARS:
        raise ValueError(f"source_year must be one of {SOURCE_YEARS}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_replace(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        _write_json(temporary, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verify_ontime_snapshot(
    snapshot_id: str, qualification_path: Path, *, data_root: Path = DEFAULT_DATA_ROOT
) -> dict[str, object]:
    """Verify a saved staged snapshot without fetching or regenerating it."""
    digest = snapshot_id.removeprefix("ontime-")
    if not snapshot_id.startswith("ontime-") or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
        raise OnTimeError("on-time snapshot ID is invalid")
    directory = data_root / "snapshots" / snapshot_id
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        source = json.loads(qualification_path.read_text(encoding="utf-8"))["sources"]["ontime"]
        request, totals, partitions = source["request"], source["totals"], source["partitions"]
        rows, airports = totals["scoped_rows"], request["origin_airports"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise OnTimeError("on-time verification inputs are invalid or incomplete") from exc
    parquet = directory / "data.parquet"
    expected_coverage = {(airport, month) for airport in airports for month in MONTHS}
    coverage = manifest.get("coverage", {})
    archives = manifest.get("archives", [])
    qualified = {item.get("month"): item for item in partitions}
    source_identity = hashlib.sha256(
        "".join(qualified[month]["zip_sha256"] for month in MONTHS).encode("ascii")
    ).hexdigest() if set(qualified) == set(MONTHS) else None
    parquet_hash = manifest.get("parquet_sha256")
    expected_id = "ontime-" + hashlib.sha256(
        f"{SOURCE_TABLE}:{source_identity}:{parquet_hash}".encode("ascii")
    ).hexdigest() if source_identity and isinstance(parquet_hash, str) else None
    if (
        manifest.get("snapshot_id") != snapshot_id
        or snapshot_id != expected_id
        or manifest.get("validation_status") != "staged"
        or manifest.get("request") != {"year": 2025, "months": list(MONTHS), "origin_airports": airports}
        or request.get("year") != 2025
        or request.get("months") != list(MONTHS)
        or manifest.get("row_count") != rows
        or source.get("partition_count") != 12
        or totals.get("exact_duplicate_rows") != 0
        or totals.get("conflicting_identity_keys") != 0
        or len(archives) != 12
        or len(partitions) != 12
        or set(qualified) != set(MONTHS)
        or any(item.get("year") != 2025 for item in partitions)
        or set(coverage) != {f"{airport}-{month:02d}" for airport, month in expected_coverage}
        or sum(coverage.values()) != rows
        or not parquet.is_file()
        or _sha256(parquet) != manifest.get("parquet_sha256")
    ):
        raise OnTimeError("saved on-time snapshot does not match its manifest or qualification")
    for archive in archives:
        item = qualified.get(archive.get("month"), {})
        if (
            archive.get("url") != item.get("url")
            or archive.get("zip_bytes") != item.get("zip_bytes")
            or archive.get("sha256") != item.get("zip_sha256")
            or archive.get("csv_bytes") != item.get("csv_bytes")
            or archive.get("csv_sha256") != item.get("csv_sha256")
            or archive.get("csv_crc32") != item.get("csv_crc32")
            or archive.get("source_rows") != item.get("source_rows")
            or archive.get("scoped_rows") != item.get("scoped_rows")
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("csv_sha256", "")))
        ):
            raise OnTimeError("saved on-time archive inventory does not match qualification")
        month = archive["month"]
        if sum(value for key, value in coverage.items() if key.endswith(f"-{month:02d}")) != item.get("scoped_rows"):
            raise OnTimeError("saved on-time monthly coverage does not match qualification")
    _verify_parquet(parquet, rows, expected_coverage)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acquire and publish the bounded BTS FGJ snapshot")
    parser.add_argument("--input-dir", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--acquire", action="store_true")
    mode.add_argument("--stage", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--year", type=int, default=ACCEPTED_SOURCE_YEAR)
    parser.add_argument("--snapshot-id")
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--deadline-seconds", type=float, default=300.0)
    args = parser.parse_args(argv)
    if args.verify_only and (not args.snapshot_id or not args.qualification):
        parser.error("--verify-only requires --snapshot-id and --qualification")
    if not args.verify_only and args.input_dir is None:
        parser.error("--input-dir is required for acquisition or publication")
    if args.stage and args.year != STAGED_SOURCE_YEAR:
        parser.error("--stage requires --year 2025")
    try:
        if args.verify_only:
            metadata = verify_ontime_snapshot(args.snapshot_id, args.qualification, data_root=args.data_root)
            print(metadata["snapshot_id"])
            return 0
        if args.acquire:
            acquire_archives(args.input_dir, deadline_seconds=args.deadline_seconds, source_year=args.year)
        metadata = publish_ontime_snapshot(args.input_dir, data_root=args.data_root, source_year=args.year)
    except (OnTimeError, OSError, duckdb.Error) as exc:
        print(f"On-time import failed: {exc}", file=sys.stderr)
        return 1
    print(metadata["snapshot_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
