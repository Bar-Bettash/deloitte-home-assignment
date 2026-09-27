"""Bounded acquisition and validation for the DataSF SFO passenger CSV."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import duckdb
import httpx

DATASET_URL = "https://data.sf.gov/resource/rkru-6vcg.csv"
ACCEPTED_YEAR_PAIR = (2023, 2024)
STAGED_YEAR_PAIR = (2024, 2025)
SUPPORTED_YEAR_PAIRS = {ACCEPTED_YEAR_PAIR, STAGED_YEAR_PAIR}


def _predicate_for_year_pair(year_pair: tuple[int, int]) -> str:
    if year_pair not in SUPPORTED_YEAR_PAIRS:
        raise ValueError("DataSF year_pair must be (2023, 2024) or (2024, 2025)")
    return f"activity_period >= '{year_pair[0]}01' AND activity_period <= '{year_pair[1]}12'"


PREDICATE = _predicate_for_year_pair(ACCEPTED_YEAR_PAIR)
REQUIRED_COLUMNS = (
    "activity_period",
    "activity_period_start_date",
    "operating_airline",
    "operating_airline_iata_code",
    "published_airline",
    "published_airline_iata_code",
    "geo_summary",
    "geo_region",
    "activity_type_code",
    "price_category_code",
    "terminal",
    "boarding_area",
    "passenger_count",
    "data_as_of",
    "data_loaded_at",
)
RAW_KEY = (
    "activity_period",
    "operating_airline",
    "operating_airline_iata_code",
    "published_airline",
    "published_airline_iata_code",
    "geo_summary",
    "geo_region",
    "activity_type_code",
    "price_category_code",
    "terminal",
    "boarding_area",
)

PAGE_SIZE = 5_000
MAX_PAGES = 4
MAX_ROWS = PAGE_SIZE * MAX_PAGES
MAX_BYTES = 10 * 1024 * 1024
TIME_LIMIT_SECONDS = 60.0
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "datasf"

PARQUET_SCHEMA = (
    ("activity_period", "VARCHAR"),
    ("activity_period_start_date", "TIMESTAMP"),
    ("operating_airline", "VARCHAR"),
    ("operating_airline_iata_code", "VARCHAR"),
    ("published_airline", "VARCHAR"),
    ("published_airline_iata_code", "VARCHAR"),
    ("geo_summary", "VARCHAR"),
    ("geo_region", "VARCHAR"),
    ("activity_type_code", "VARCHAR"),
    ("price_category_code", "VARCHAR"),
    ("terminal", "VARCHAR"),
    ("boarding_area", "VARCHAR"),
    ("passenger_count", "BIGINT"),
    ("data_as_of", "TIMESTAMP"),
    ("data_loaded_at", "TIMESTAMP"),
)

_GEOGRAPHIES = {"Domestic", "International"}
_ACTIVITY_TYPES = {"Enplaned", "Deplaned", "Thru / Transit"}


class DataSFError(RuntimeError):
    """The upstream response could not produce a complete accepted retrieval."""


async def fetch_datasf(
    client: httpx.AsyncClient,
    *,
    year_pair: tuple[int, int] = ACCEPTED_YEAR_PAIR,
    monotonic: Callable[[], float] = time.monotonic,
    time_limit_seconds: float = TIME_LIMIT_SECONDS,
) -> list[dict[str, str]]:
    """Fetch one supported 24-month scope or raise without publishing any data."""
    predicate = _predicate_for_year_pair(year_pair)
    months = {f"{year}{month:02d}" for year in year_pair for month in range(1, 13)}
    if not 0 < time_limit_seconds <= TIME_LIMIT_SECONDS:
        raise ValueError("time_limit_seconds must be within the fixed 60 second cap")
    started_at = monotonic()
    byte_count = 0

    async def get(params: dict[str, str | int]) -> bytes:
        nonlocal byte_count
        remaining = time_limit_seconds - (monotonic() - started_at)
        if remaining <= 0:
            raise DataSFError("DataSF refresh exceeded the 60 second limit")
        try:
            async with asyncio.timeout(remaining):
                async with client.stream("GET", DATASET_URL, params=params, timeout=remaining) as response:
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    async for chunk in response.aiter_bytes():
                        if monotonic() - started_at > time_limit_seconds:
                            raise DataSFError("DataSF refresh exceeded the 60 second limit")
                        byte_count += len(chunk)
                        if byte_count > MAX_BYTES:
                            raise DataSFError("DataSF refresh exceeded the 10 MiB response limit")
                        chunks.append(chunk)
        except TimeoutError as exc:
            raise DataSFError("DataSF refresh exceeded the 60 second limit") from exc
        except httpx.TimeoutException as exc:
            raise DataSFError("DataSF request timed out") from exc
        except httpx.HTTPError as exc:
            raise DataSFError("DataSF request failed") from exc
        if monotonic() - started_at > time_limit_seconds:
            raise DataSFError("DataSF refresh exceeded the 60 second limit")
        return b"".join(chunks)

    count_params = {"$select": "count(*) AS count", "$where": predicate}
    pre_count = _parse_count(await get(count_params))
    if pre_count > MAX_ROWS:
        raise DataSFError("DataSF row count exceeds the 20,000 row limit")

    rows: list[dict[str, str]] = []
    seen_keys: set[tuple[str, ...]] = set()
    offset = 0
    while offset < pre_count:
        if offset // PAGE_SIZE >= MAX_PAGES:
            raise DataSFError("DataSF retrieval exceeded the four page limit")
        page = _parse_page(
            await get(
                {
                    "$select": ",".join(REQUIRED_COLUMNS),
                    "$where": predicate,
                    "$order": ":id",
                    "$limit": PAGE_SIZE,
                    "$offset": offset,
                }
            )
        )
        expected_size = min(PAGE_SIZE, pre_count - offset)
        if len(page) != expected_size:
            detail = "extra rows" if len(page) > expected_size else "an early short or empty page"
            raise DataSFError(f"DataSF returned {detail}")
        for row in page:
            _validate_row(row, months)
            raw_key = tuple(row[column] for column in RAW_KEY)
            if raw_key in seen_keys:
                raise DataSFError("DataSF returned a duplicate declared raw key")
            seen_keys.add(raw_key)
        rows.extend(page)
        offset += len(page)

    post_count = _parse_count(await get(count_params))
    if pre_count != post_count or post_count != len(rows):
        raise DataSFError("DataSF row count changed during retrieval")
    _validate_enplaned_coverage(rows, months)
    return rows


async def publish_datasf_snapshot(
    client: httpx.AsyncClient,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    year_pair: tuple[int, int] = ACCEPTED_YEAR_PAIR,
    monotonic: Callable[[], float] = time.monotonic,
    retrieved_at: datetime | None = None,
) -> dict[str, object]:
    """Publish an immutable snapshot; only the historical pair moves the pointer."""
    predicate = _predicate_for_year_pair(year_pair)
    rows = await fetch_datasf(client, year_pair=year_pair, monotonic=monotonic)
    retrieval_time = retrieved_at or datetime.now(UTC)
    if retrieval_time.tzinfo is None:
        raise ValueError("retrieved_at must include a timezone")
    retrieval_time = retrieval_time.astimezone(UTC)

    snapshot_root = data_root / "snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=".staging-", dir=snapshot_root))
    pointer_temp = data_root / f".current-{uuid4().hex}.json"
    try:
        parquet_path = staging_dir / "data.parquet"
        _write_parquet(parquet_path, rows)
        _verify_parquet(parquet_path, len(rows))
        content_sha256 = hashlib.sha256(parquet_path.read_bytes()).hexdigest()
        identity_digest = hashlib.sha256(f"datasf:rkru-6vcg:{content_sha256}".encode("ascii")).hexdigest()
        snapshot_id = f"datasf-{identity_digest}"
        enplaned_cells = {(row["activity_period"], row["geo_summary"]) for row in rows if row["activity_type_code"] == "Enplaned"}
        metadata: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "source": {
                "name": "DataSF SFO passenger statistics",
                "dataset_id": "rkru-6vcg",
                "url": DATASET_URL,
            },
            "request": {
                "count": {"$select": "count(*) AS count", "$where": predicate},
                "pages": {
                    "$select": ",".join(REQUIRED_COLUMNS),
                    "$where": predicate,
                    "$order": ":id",
                    "$limit": PAGE_SIZE,
                    "$offsets": list(range(0, len(rows), PAGE_SIZE)),
                },
            },
            "scope": {
                "activity_period_start": f"{year_pair[0]}01",
                "activity_period_end": f"{year_pair[1]}12",
                "enplaned_geographies": sorted(_GEOGRAPHIES),
            },
            "retrieved_at_utc": retrieval_time.isoformat().replace("+00:00", "Z"),
            "row_count": len(rows),
            "source_counts": {"pre": len(rows), "post": len(rows)},
            "enplaned_cell_count": len(enplaned_cells),
            "content_sha256": content_sha256,
            "validation_status": ("accepted" if year_pair == ACCEPTED_YEAR_PAIR else "staged"),
            "parquet_file": "data.parquet",
        }
        _write_json(staging_dir / "manifest.json", metadata)

        final_dir = snapshot_root / snapshot_id
        if final_dir.exists():
            existing_parquet = final_dir / "data.parquet"
            existing_manifest = final_dir / "manifest.json"
            if not existing_parquet.is_file() or not existing_manifest.is_file() or hashlib.sha256(existing_parquet.read_bytes()).hexdigest() != content_sha256:
                raise DataSFError("existing DataSF snapshot does not match its content identity")
            try:
                published_metadata = json.loads(existing_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DataSFError("existing DataSF snapshot manifest is invalid") from exc
            if (
                published_metadata.get("snapshot_id") != snapshot_id
                or published_metadata.get("content_sha256") != content_sha256
                or published_metadata.get("validation_status") != metadata["validation_status"]
            ):
                raise DataSFError("existing DataSF snapshot manifest is invalid")
        else:
            os.replace(staging_dir, final_dir)
            published_metadata = metadata

        if year_pair == ACCEPTED_YEAR_PAIR:
            pointer = {
                "snapshot_id": snapshot_id,
                "manifest": f"snapshots/{snapshot_id}/manifest.json",
            }
            _write_json(pointer_temp, pointer)
            os.replace(pointer_temp, data_root / "current.json")
        return published_metadata
    finally:
        if staging_dir.exists():
            # ExFAT may race creation/removal of AppleDouble sidecars. Staging is
            # never referenced by current.json, so cleanup must not turn a
            # completed or safely rejected publication into a false failure.
            shutil.rmtree(staging_dir, ignore_errors=True)
        pointer_temp.unlink(missing_ok=True)


def _write_parquet(path: Path, rows: list[dict[str, str]]) -> None:
    columns = ", ".join(f'"{name}" {data_type}' for name, data_type in PARQUET_SCHEMA)
    placeholders = ", ".join("?" for _ in PARQUET_SCHEMA)
    values = [
        (
            row["activity_period"],
            datetime.fromisoformat(row["activity_period_start_date"]),
            row["operating_airline"],
            row["operating_airline_iata_code"],
            row["published_airline"],
            row["published_airline_iata_code"],
            row["geo_summary"],
            row["geo_region"],
            row["activity_type_code"],
            row["price_category_code"],
            row["terminal"],
            row["boarding_area"],
            int(row["passenger_count"]),
            datetime.fromisoformat(row["data_as_of"]),
            datetime.fromisoformat(row["data_loaded_at"]),
        )
        for row in rows
    ]
    connection = duckdb.connect()
    try:
        connection.execute(f"CREATE TABLE snapshot ({columns})")
        connection.executemany(f"INSERT INTO snapshot VALUES ({placeholders})", values)
        parquet_literal = "'" + str(path).replace("'", "''") + "'"
        connection.execute(f"COPY snapshot TO {parquet_literal} (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        connection.close()


def _verify_parquet(path: Path, expected_rows: int) -> None:
    connection = duckdb.connect()
    try:
        actual_schema = [(row[0], row[1]) for row in connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()]
        actual_rows = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
    finally:
        connection.close()
    if actual_schema != list(PARQUET_SCHEMA) or actual_rows != expected_rows:
        raise DataSFError("published DataSF Parquet failed schema or row-count verification")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def verify_datasf_snapshot(snapshot_id: str, qualification_path: Path, *, data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, object]:
    """Verify one saved staged snapshot against the source qualification receipt."""
    digest = snapshot_id.removeprefix("datasf-")
    if not snapshot_id.startswith("datasf-") or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
        raise DataSFError("DataSF snapshot ID is invalid")
    snapshot_dir = data_root / "snapshots" / snapshot_id
    try:
        manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
        qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
        source = qualification["sources"]["datasf"]
        request = source["request"]
        totals = source["totals"]
        validation = source["validation"]
        expected_rows = totals["rows"]
        expected_cells = totals["enplaned_month_geography_cells"]
        expected_hash = totals["canonical_content_sha256"]
        count_request = source["count_request"]
        period_start = request["activity_period_start"]
        period_end = request["activity_period_end"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DataSFError("DataSF verification inputs are invalid or incomplete") from exc
    parquet = snapshot_dir / "data.parquet"
    content_hash = manifest.get("content_sha256") if isinstance(manifest, dict) else None
    expected_id = "datasf-" + hashlib.sha256(
        f"datasf:rkru-6vcg:{content_hash}".encode("ascii")
    ).hexdigest() if isinstance(content_hash, str) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("snapshot_id") != snapshot_id
        or snapshot_id != expected_id
        or manifest.get("validation_status") != "staged"
        or manifest.get("source", {}).get("dataset_id") != source.get("dataset_id")
        or manifest.get("row_count") != expected_rows
        or manifest.get("source_counts") != {"pre": expected_rows, "post": expected_rows}
        or manifest.get("enplaned_cell_count") != expected_cells
        or manifest.get("scope", {}).get("activity_period_start") != period_start
        or manifest.get("scope", {}).get("activity_period_end") != period_end
        or manifest.get("request", {}).get("count", {}).get("$where") != request.get("predicate")
        or request.get("order") != ":id"
        or count_request.get("pre_count") != expected_rows
        or count_request.get("post_count") != expected_rows
        or totals.get("duplicate_raw_keys") != 0
        or totals.get("conflicting_raw_keys") != 0
        or validation.get("schema_exact_15_columns") is not True
        or validation.get("accepted_pointer_updated") is not False
        or not isinstance(expected_rows, int)
        or not isinstance(expected_cells, int)
        or not isinstance(expected_hash, str)
        or not parquet.is_file()
        or hashlib.sha256(parquet.read_bytes()).hexdigest() != content_hash
    ):
        raise DataSFError("saved DataSF snapshot does not match its manifest or qualification")
    _verify_parquet(parquet, expected_rows)
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            f"SELECT {', '.join(name for name, _ in PARQUET_SCHEMA)} FROM read_parquet(?)",
            [str(parquet)],
        ).fetchall()
    finally:
        connection.close()
    if _canonical_sha256(rows) != expected_hash:
        raise DataSFError("saved DataSF content does not match qualification canonical hash")
    months = {f"{year}{month:02d}" for year in (2024, 2025) for month in range(1, 13)}
    cells = {(row[0], row[6]) for row in rows if row[8] == "Enplaned"}
    if cells != {(month, geography) for month in months for geography in _GEOGRAPHIES}:
        raise DataSFError("saved DataSF content does not match qualification coverage")
    return manifest


def _canonical_sha256(rows: list[tuple[object, ...]]) -> str:
    normalized: list[tuple[str, ...]] = []
    for row in rows:
        values = []
        for index, value in enumerate(row):
            if index in {1, 13, 14}:
                parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone(UTC).replace(tzinfo=None)
                values.append(parsed.isoformat(timespec="milliseconds"))
            elif index == 12:
                values.append(str(int(value)))
            else:
                values.append("" if value is None else str(value).strip())
        normalized.append(tuple(values))
    payload = json.dumps(sorted(normalized), separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh or verify a DataSF snapshot")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--years", nargs=2, type=int, metavar=("START", "END"))
    parser.add_argument("--snapshot-id")
    parser.add_argument("--qualification", type=Path)
    args = parser.parse_args(argv)
    if args.refresh:
        if tuple(args.years or ()) != STAGED_YEAR_PAIR:
            parser.error("--refresh requires --years 2024 2025")
        if args.snapshot_id or args.qualification:
            parser.error("--snapshot-id and --qualification are only valid with --verify-only")
    else:
        if args.years:
            parser.error("--years is only valid with --refresh")
        if not args.snapshot_id or not args.qualification:
            parser.error("--verify-only requires --snapshot-id and --qualification")
    try:
        if args.verify_only:
            metadata = verify_datasf_snapshot(args.snapshot_id, args.qualification, data_root=DEFAULT_DATA_ROOT)
            print(metadata["snapshot_id"])
            return 0

        async def refresh() -> dict[str, object]:
            async with httpx.AsyncClient() as client:
                return await publish_datasf_snapshot(
                    client,
                    data_root=DEFAULT_DATA_ROOT,
                    year_pair=STAGED_YEAR_PAIR,
                )

        metadata = asyncio.run(refresh())
    except (DataSFError, OSError, duckdb.Error) as exc:
        print(f"DataSF refresh failed: {exc}", file=sys.stderr)
        return 1
    print(metadata["snapshot_id"])
    return 0


def _parse_count(content: bytes) -> int:
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        rows = list(reader)
        if reader.fieldnames != ["count"] or len(rows) != 1:
            raise ValueError
        value = rows[0]["count"]
        if value is None or not value.isascii() or not value.isdigit():
            raise ValueError
        return int(value)
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        raise DataSFError("DataSF count response is invalid") from exc


def _parse_page(content: bytes) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        if reader.fieldnames != list(REQUIRED_COLUMNS):
            raise DataSFError("DataSF CSV schema does not match the required 15 columns")
        return [dict(row) for row in reader]
    except (UnicodeDecodeError, csv.Error) as exc:
        raise DataSFError("DataSF CSV response is invalid") from exc


def _validate_row(row: dict[str, str], months: set[str]) -> None:
    missing_or_blank = set(row) != set(REQUIRED_COLUMNS) or any(not isinstance(row[column], str) or not row[column].strip() for column in REQUIRED_COLUMNS)
    if missing_or_blank:
        raise DataSFError("DataSF row has a missing or blank required field")
    if row["activity_period"] not in months:
        raise DataSFError("DataSF row has an invalid activity period")
    if row["geo_summary"] not in _GEOGRAPHIES:
        raise DataSFError("DataSF row has an unexpected geography value")
    if row["activity_type_code"] not in _ACTIVITY_TYPES:
        raise DataSFError("DataSF row has an unexpected activity type")
    passenger_count = row["passenger_count"]
    if not passenger_count.isascii() or not passenger_count.isdigit():
        raise DataSFError("DataSF row has an invalid passenger count")
    for column in ("activity_period_start_date", "data_as_of", "data_loaded_at"):
        try:
            datetime.fromisoformat(row[column])
        except ValueError as exc:
            raise DataSFError(f"DataSF row has an invalid {column}") from exc


def _validate_enplaned_coverage(rows: list[dict[str, str]], months: set[str]) -> None:
    cells = {(row["activity_period"], row["geo_summary"]) for row in rows if row["activity_type_code"] == "Enplaned"}
    expected = {(month, geography) for month in months for geography in _GEOGRAPHIES}
    if cells != expected:
        raise DataSFError("DataSF is missing one or more required Enplaned month/geography cells")


if __name__ == "__main__":
    raise SystemExit(main())
