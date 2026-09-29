"""Validate, deduplicate, filter, and publish the bounded BTS T-100 inputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

import duckdb

BTS_FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx?QO_fu146_anzr=&gnoyr_VQ=FMG"
CSV_MEMBER = "T_T100_SEGMENT_ALL_CARRIER.csv"
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "t100"

FIELDS = (
    "DEPARTURES_SCHEDULED", "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS", "DISTANCE",
    "UNIQUE_CARRIER", "AIRLINE_ID", "UNIQUE_CARRIER_NAME", "UNIQUE_CARRIER_ENTITY", "REGION",
    "ORIGIN_AIRPORT_ID", "ORIGIN", "ORIGIN_STATE_ABR", "ORIGIN_COUNTRY",
    "DEST_AIRPORT_ID", "DEST", "DEST_STATE_ABR", "DEST_COUNTRY",
    "AIRCRAFT_TYPE", "AIRCRAFT_CONFIG", "YEAR", "MONTH", "CLASS", "DATA_SOURCE",
)
RAW_KEY = (
    "UNIQUE_CARRIER", "AIRLINE_ID", "UNIQUE_CARRIER_ENTITY", "REGION",
    "ORIGIN_AIRPORT_ID", "ORIGIN", "ORIGIN_STATE_ABR", "ORIGIN_COUNTRY",
    "DEST_AIRPORT_ID", "DEST", "DEST_STATE_ABR", "DEST_COUNTRY",
    "AIRCRAFT_TYPE", "AIRCRAFT_CONFIG", "YEAR", "MONTH", "CLASS", "DATA_SOURCE",
)
PAYLOAD = (
    "UNIQUE_CARRIER_NAME", "DEPARTURES_SCHEDULED", "DEPARTURES_PERFORMED",
    "SEATS", "PASSENGERS", "DISTANCE",
)
MEASURES = (
    "DEPARTURES_SCHEDULED", "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS", "DISTANCE",
)
REQUIRED_ORIGINS = {
    "ANC", "LAX", "SFO", "SNA", "BDL", "HVN", "PWM", "BGR", "PQI", "RKD", "BHB",
    "AUG", "BOS", "ACK", "ORH", "MVY", "HYA", "PVC", "MHT", "PSM", "LEB", "PVD",
    "WST", "BID", "BTV", "RUT",
}
ALLOWED_CLASSES = {"A", "C", "E", "F"}
OBSERVED_CLASSES = {"F", "G", "L", "P"}
DATA_SOURCES = {"DF", "DU", "IF", "IU"}
EXPECTED_UNIQUE_BY_YEAR = {2023: 153_307, 2024: 160_404}
EXPECTED_ELIGIBLE_BY_YEAR = {2023: 29_039, 2024: 31_428}
HISTORICAL_YEARS = (2023, 2024)
RECENT_YEARS = (2024, 2025)
RECENT_ORIGINS = REQUIRED_ORIGINS | {"EWB"}
_SUPPORTED_ORIGINS = {
    HISTORICAL_YEARS: frozenset(REQUIRED_ORIGINS),
    RECENT_YEARS: frozenset(RECENT_ORIGINS),
}
_QUALIFIED_COUNTS = {
    HISTORICAL_YEARS: (EXPECTED_UNIQUE_BY_YEAR, EXPECTED_ELIGIBLE_BY_YEAR),
    RECENT_YEARS: ({2024: 160_404, 2025: 163_030}, {2024: 31_453, 2025: 31_839}),
}
_NO_ELIGIBLE_MONTHS = {
    HISTORICAL_YEARS: {("PVC", 2024): {12}},
    RECENT_YEARS: {("PVC", 2024): {12}, ("PVC", 2025): {1, 2, 3, 4, 12}},
}

PARQUET_SCHEMA = (
    ("departures_scheduled", "DECIMAL(18,2)"),
    ("departures_performed", "DECIMAL(18,2)"),
    ("seats", "DECIMAL(18,2)"),
    ("passengers", "DECIMAL(18,2)"),
    ("distance", "DECIMAL(18,2)"),
    ("unique_carrier", "VARCHAR"),
    ("airline_id", "INTEGER"),
    ("unique_carrier_name", "VARCHAR"),
    ("unique_carrier_entity", "VARCHAR"),
    ("region", "VARCHAR"),
    ("origin_airport_id", "INTEGER"),
    ("origin", "VARCHAR"),
    ("origin_state_abr", "VARCHAR"),
    ("origin_country", "VARCHAR"),
    ("dest_airport_id", "INTEGER"),
    ("dest", "VARCHAR"),
    ("dest_state_abr", "VARCHAR"),
    ("dest_country", "VARCHAR"),
    ("aircraft_type", "VARCHAR"),
    ("aircraft_config", "VARCHAR"),
    ("year", "INTEGER"),
    ("month", "INTEGER"),
    ("class", "VARCHAR"),
    ("data_source", "VARCHAR"),
)


class T100Error(RuntimeError):
    """The supplied T-100 archives cannot produce an accepted snapshot."""


@dataclass(frozen=True)
class ArchiveSpec:
    filename: str
    state: str
    year: int
    zip_bytes: int
    sha256: str
    csv_rows: int


def _spec(filename: str, state: str, year: int, size: int, digest: str, rows: int) -> ArchiveSpec:
    return ArchiveSpec(filename, state, year, size, digest, rows)


EXPECTED_ARCHIVES = (
    _spec("deloitte-t100-alaska-2023-fullgrain.zip", "AK", 2023, 565872, "28006a1578d864c62cdf281ec2e1957741c54aff73674cf66e67e81faebce542", 52541),
    _spec("deloitte-t100-california-2023-fullgrain.zip", "CA", 2023, 986411, "59137e86d53197a0da3ea9d76c3f51a027f27dc33c5c88fa6fbe6632f5506468", 74692),
    _spec("deloitte-t100-connecticut-2023-fullgrain.zip", "CT", 2023, 52267, "1998197ac494419053cef4bd09d998904bb7cb4a9c2b3fe7fb2b88c34e2f7a98", 4526),
    _spec("deloitte-t100-massachusetts-2023-fullgrain.zip", "MA", 2023, 198455, "e9384213417e3df406e77bd6063e000f4d1f42afb65f4923be8bb5902464687b", 15894),
    _spec("deloitte-t100-maine-2023-fullgrain.zip", "ME", 2023, 32160, "9b6f3956d215534233bbe8f9deea7f50a14a89351014ba56cf9ac0c9949f3249", 2555),
    _spec("deloitte-t100-new-hampshire-2023-fullgrain.zip", "NH", 2023, 17607, "1645ea7a21bf4f71f76ac0f3b4be1162f25ec0287d846744d3f5d36e01edb653", 1485),
    _spec("deloitte-t100-rhode-island-2023-fullgrain.zip", "RI", 2023, 30557, "3ed3828343cd0deb4570ca29c00f3a0f40cf79deef7b1470df964ccdd660889e", 2519),
    _spec("deloitte-t100-vermont-2023-fullgrain.zip", "VT", 2023, 13564, "f21f88fbf7d7a8a7be229df8bb2b0e1f8c7522d255a0488678baadde425803d1", 1118),
    _spec("deloitte-t100-alaska-2024-fullgrain.zip", "AK", 2024, 589484, "98b07e096577a7a23aa0e11a8a176db090b7de04094dc96456b55f7b805e1b92", 54759),
    _spec("deloitte-t100-california-2024-fullgrain.zip", "CA", 2024, 1021860, "38368bbfe9d811beabcc882ce66f4b0d6fe06ff582ac1b0eb48f2a41bc86baef", 77193),
    _spec("deloitte-t100-connecticut-2024-fullgrain.zip", "CT", 2024, 57669, "2f857138640dcfb0a74ff818ea4c0f9b5ec2d766ac1a656ab83a3e2997e0e558", 5064),
    _spec("deloitte-t100-massachusetts-2024-fullgrain.zip", "MA", 2024, 212165, "0849df3c15c305fb03408b893f497a0e09e308e854aa14025d4b8fc604299242", 17116),
    _spec("deloitte-t100-maine-2024-fullgrain.zip", "ME", 2024, 36243, "ff1e7732241b541b858b653195b9aa030a49dd6e7fca9558cbb710df9a51006a", 2912),
    _spec("deloitte-t100-new-hampshire-2024-fullgrain.zip", "NH", 2024, 18514, "f8044a53d06ff10780233381dd15bd8d614bace46b94e69f3bbc49d7c41dcb7d", 1542),
    _spec("deloitte-t100-rhode-island-2024-fullgrain.zip", "RI", 2024, 31529, "d37c0c2614d7cbd33ccabc4ba67a84310577c0fe8e6a599cc36d5c8e67ce1475", 2636),
    _spec("deloitte-t100-vermont-2024-fullgrain.zip", "VT", 2024, 15562, "1d4781ebaf535faf28408aac65d5e296cd701daa9b04531004d71e1bae68e4c2", 1279),
)


@dataclass
class ImportResult:
    records: dict[tuple[str, ...], dict[str, str]]
    archive_metadata: list[dict[str, object]]
    input_by_year: Counter[int]
    unique_by_year: Counter[int]


def import_archives(input_dir: Path, specs: tuple[ArchiveSpec, ...] = EXPECTED_ARCHIVES) -> ImportResult:
    """Stream validated ZIP members and deduplicate on the complete raw key."""
    records: dict[tuple[str, ...], dict[str, str]] = {}
    archive_metadata: list[dict[str, object]] = []
    input_by_year: Counter[int] = Counter()
    for spec in specs:
        path = input_dir / spec.filename
        if not path.is_file():
            raise T100Error(f"required T-100 archive is missing: {spec.filename}")
        content_sha256 = _sha256(path)
        if path.stat().st_size != spec.zip_bytes or content_sha256 != spec.sha256:
            raise T100Error(f"T-100 archive does not match its qualified bytes: {spec.filename}")
        try:
            archive = zipfile.ZipFile(path)
        except zipfile.BadZipFile as exc:
            raise T100Error(f"T-100 archive is invalid: {spec.filename}") from exc
        with archive:
            if archive.testzip() is not None:
                raise T100Error(f"T-100 archive failed CRC validation: {spec.filename}")
            members = [info for info in archive.infolist() if not info.is_dir()]
            if len(members) != 1 or members[0].filename != CSV_MEMBER:
                raise T100Error(f"T-100 archive has an unexpected member: {spec.filename}")
            row_count = 0
            with archive.open(members[0]) as raw, io.TextIOWrapper(
                raw, encoding="utf-8-sig", newline=""
            ) as text:
                reader = csv.DictReader(text)
                if tuple(reader.fieldnames or ()) != FIELDS:
                    raise T100Error(f"T-100 CSV schema is invalid: {spec.filename}")
                for source_row in reader:
                    row_count += 1
                    row = _validate_row(source_row, spec)
                    key = tuple(row[field] for field in RAW_KEY)
                    previous = records.get(key)
                    if previous is None:
                        records[key] = row
                    elif tuple(previous[field] for field in PAYLOAD) != tuple(
                        row[field] for field in PAYLOAD
                    ):
                        raise T100Error("T-100 duplicate raw key has conflicting measures")
            if row_count != spec.csv_rows:
                raise T100Error(f"T-100 CSV row count changed: {spec.filename}")
        input_by_year[spec.year] += row_count
        archive_metadata.append(
            {
                "filename": spec.filename,
                "state": spec.state,
                "year": spec.year,
                "zip_bytes": spec.zip_bytes,
                "sha256": content_sha256,
                "csv_member": CSV_MEMBER,
                "csv_rows": row_count,
            }
        )
    unique_by_year = Counter(int(row["YEAR"]) for row in records.values())
    return ImportResult(records, archive_metadata, input_by_year, unique_by_year)


def filter_eligible(
    records: dict[tuple[str, ...], dict[str, str]], origins=REQUIRED_ORIGINS
) -> list[dict[str, str]]:
    """Select declared origin-direction scheduled passenger rows with positive seats."""
    return [
        row
        for row in records.values()
        if row["ORIGIN"] in origins
        and row["CLASS"] in ALLOWED_CLASSES
        and Decimal(row["SEATS"]) > 0
    ]


def coverage_by_airport_period(rows: list[dict[str, str]]) -> dict[tuple[str, int], set[int]]:
    coverage: dict[tuple[str, int], set[int]] = defaultdict(set)
    for row in rows:
        coverage[(row["ORIGIN"], int(row["YEAR"]))].add(int(row["MONTH"]))
    return dict(coverage)


def publish_t100_snapshot(
    input_dir: Path,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    specs: tuple[ArchiveSpec, ...] = EXPECTED_ARCHIVES,
    years: tuple[int, int] = HISTORICAL_YEARS,
    origins=None,
    publish_current: bool | None = None,
    expected_unique_by_year: dict[int, int] | None = None,
    expected_eligible_by_year: dict[int, int] | None = None,
    imported_at: datetime | None = None,
) -> dict[str, object]:
    supported_origins = _SUPPORTED_ORIGINS.get(years)
    selected_origins = supported_origins if origins is None else frozenset(origins)
    if supported_origins is None or selected_origins != supported_origins:
        raise ValueError("unsupported T-100 year/origin scope")
    if publish_current is None:
        publish_current = years == HISTORICAL_YEARS
    if publish_current and years != HISTORICAL_YEARS:
        raise ValueError("the 2024/25 T-100 snapshot must remain a staged candidate")
    expected_partitions = {(state, year) for state in ("AK", "CA", "CT", "MA", "ME", "NH", "RI", "VT") for year in years}
    if {(spec.state, spec.year) for spec in specs} != expected_partitions:
        raise T100Error("T-100 archive specs do not match the selected scope")
    qualified_unique, qualified_eligible = _QUALIFIED_COUNTS[years]
    expected_unique_by_year = expected_unique_by_year or qualified_unique
    expected_eligible_by_year = expected_eligible_by_year or qualified_eligible
    imported = import_archives(input_dir, specs)
    if dict(imported.unique_by_year) != expected_unique_by_year:
        raise T100Error("T-100 union unique counts do not match qualification")
    rows = filter_eligible(imported.records, selected_origins)
    eligible_by_year = Counter(int(row["YEAR"]) for row in rows)
    if dict(eligible_by_year) != expected_eligible_by_year:
        raise T100Error("T-100 eligible row counts do not match qualification")
    coverage = coverage_by_airport_period(rows)
    _validate_coverage(coverage, years, selected_origins)

    import_time = imported_at or datetime.now(timezone.utc)
    if import_time.tzinfo is None:
        raise ValueError("imported_at must include a timezone")
    import_time = import_time.astimezone(timezone.utc)
    snapshot_root = data_root / "snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=".staging-", dir=snapshot_root))
    pointer_temp = data_root / f".current-{uuid4().hex}.json"
    try:
        parquet_path = staging_dir / "data.parquet"
        _write_parquet(parquet_path, rows)
        _verify_parquet(parquet_path, len(rows))
        parquet_sha256 = _sha256(parquet_path)
        input_identity = hashlib.sha256(
            "".join(item["sha256"] for item in imported.archive_metadata).encode("ascii")
        ).hexdigest()
        identity = hashlib.sha256(
            f"bts-t100-fmg:{input_identity}:{parquet_sha256}".encode("ascii")
        ).hexdigest()
        snapshot_id = f"t100-{identity}"
        missing_months = {
            f"{airport}-{year}": sorted(set(range(1, 13)) - months)
            for (airport, year), months in sorted(coverage.items())
            if months != set(range(1, 13))
        }
        raw_classes = Counter(
            (int(row["YEAR"]), row["CLASS"])
            for row in imported.records.values()
            if row["ORIGIN"] in selected_origins
        )
        source_families = Counter((int(row["YEAR"]), row["DATA_SOURCE"]) for row in rows)
        metadata: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "source": {"name": "BTS T-100 Segment All Carriers", "table": "FMG", "url": BTS_FORM_URL},
            "imported_at_utc": import_time.isoformat().replace("+00:00", "Z"),
            "archives": imported.archive_metadata,
            "input_rows": {str(year): imported.input_by_year[year] for year in sorted(imported.input_by_year)},
            "unique_rows": {str(year): imported.unique_by_year[year] for year in sorted(imported.unique_by_year)},
            "exact_overlap_rows": {
                str(year): imported.input_by_year[year] - imported.unique_by_year[year]
                for year in sorted(imported.input_by_year)
            },
            "eligible_rows": {str(year): eligible_by_year[year] for year in sorted(eligible_by_year)},
            "population": {
                "origins": sorted(selected_origins),
                "years": list(years),
                "classes": sorted(ALLOWED_CLASSES),
                "seats": "> 0",
                "direction": "origin",
            },
            "raw_class_counts_at_origins": {
                f"{year}-{class_code}": count
                for (year, class_code), count in sorted(raw_classes.items())
            },
            "eligible_source_family_counts": {
                f"{year}-{source}": count
                for (year, source), count in sorted(source_families.items())
            },
            "coverage_cells": len(coverage),
            "missing_months": missing_months,
            "source_complete_no_eligible_months": missing_months,
            "parquet_file": "data.parquet",
            "parquet_sha256": parquet_sha256,
            "validation_status": "accepted",
        }
        _write_json(staging_dir / "manifest.json", metadata)
        final_dir = snapshot_root / snapshot_id
        if final_dir.exists():
            existing_parquet = final_dir / "data.parquet"
            existing_manifest = final_dir / "manifest.json"
            if not existing_parquet.is_file() or _sha256(existing_parquet) != parquet_sha256:
                raise T100Error("existing T-100 snapshot does not match its content identity")
            try:
                published_metadata = json.loads(existing_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise T100Error("existing T-100 snapshot manifest is invalid") from exc
            if (
                not isinstance(published_metadata, dict)
                or published_metadata.get("snapshot_id") != snapshot_id
                or published_metadata.get("parquet_sha256") != parquet_sha256
                or published_metadata.get("validation_status") != "accepted"
            ):
                raise T100Error("existing T-100 snapshot manifest is invalid")
        else:
            os.replace(staging_dir, final_dir)
            published_metadata = metadata
        if publish_current:
            pointer = {"snapshot_id": snapshot_id, "manifest": f"snapshots/{snapshot_id}/manifest.json"}
            _write_json(pointer_temp, pointer)
            os.replace(pointer_temp, data_root / "current.json")
        return published_metadata
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        pointer_temp.unlink(missing_ok=True)


def _validate_row(source_row: dict[str, str], spec: ArchiveSpec) -> dict[str, str]:
    row = {field: (source_row[field] or "").strip() for field in FIELDS}
    required_nonblank = set(FIELDS) - {"ORIGIN_STATE_ABR", "DEST_STATE_ABR"}
    if any(not row[field] for field in required_nonblank):
        raise T100Error(f"T-100 row has a blank required field: {spec.filename}")
    try:
        year = int(row["YEAR"])
        month = int(row["MONTH"])
        int(row["AIRLINE_ID"])
        int(row["ORIGIN_AIRPORT_ID"])
        int(row["DEST_AIRPORT_ID"])
    except ValueError as exc:
        raise T100Error(f"T-100 row has an invalid integer field: {spec.filename}") from exc
    if year != spec.year or month not in range(1, 13):
        raise T100Error(f"T-100 row has an invalid period: {spec.filename}")
    if row["CLASS"] not in OBSERVED_CLASSES | ALLOWED_CLASSES or row["DATA_SOURCE"] not in DATA_SOURCES:
        raise T100Error(f"T-100 row has an invalid class or data source: {spec.filename}")
    for field in MEASURES:
        try:
            value = Decimal(row[field])
            if not value.is_finite() or value < 0:
                raise InvalidOperation
            quantized = value.quantize(Decimal("0.01"))
            if value != quantized:
                raise InvalidOperation
            row[field] = str(quantized)
        except (InvalidOperation, ValueError) as exc:
            raise T100Error(f"T-100 row has an invalid measure: {spec.filename}") from exc
    return row


def _validate_coverage(coverage, years, origins) -> None:
    expected_keys = {(airport, year) for airport in origins for year in years}
    if set(coverage) != expected_keys:
        raise T100Error("T-100 eligible airport-year coverage is incomplete")
    full_year = set(range(1, 13))
    for key, months in coverage.items():
        expected = full_year - _NO_ELIGIBLE_MONTHS[years].get(key, set())
        if months != expected:
            raise T100Error(f"T-100 month coverage is invalid for {key[0]}-{key[1]}")


def _write_parquet(path: Path, rows: list[dict[str, str]]) -> None:
    columns = ", ".join(f'"{name}" {data_type}' for name, data_type in PARQUET_SCHEMA)
    placeholders = ", ".join("?" for _ in PARQUET_SCHEMA)
    ordered = sorted(rows, key=lambda row: tuple(row[field] for field in RAW_KEY))
    values = [
        (
            *(Decimal(row[field]) for field in MEASURES),
            row["UNIQUE_CARRIER"], int(row["AIRLINE_ID"]), row["UNIQUE_CARRIER_NAME"],
            row["UNIQUE_CARRIER_ENTITY"], row["REGION"], int(row["ORIGIN_AIRPORT_ID"]),
            row["ORIGIN"], row["ORIGIN_STATE_ABR"] or None, row["ORIGIN_COUNTRY"],
            int(row["DEST_AIRPORT_ID"]), row["DEST"], row["DEST_STATE_ABR"] or None,
            row["DEST_COUNTRY"], row["AIRCRAFT_TYPE"], row["AIRCRAFT_CONFIG"],
            int(row["YEAR"]), int(row["MONTH"]), row["CLASS"], row["DATA_SOURCE"],
        )
        for row in ordered
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
        schema = [
            (row[0], row[1])
            for row in connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
        ]
        count = connection.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
    finally:
        connection.close()
    if schema != list(PARQUET_SCHEMA) or count != expected_rows:
        raise T100Error("T-100 Parquet failed schema or row-count verification")


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the qualified BTS T-100 archives")
    parser.add_argument("--input-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        metadata = publish_t100_snapshot(args.input_dir)
    except (T100Error, OSError, duckdb.Error) as exc:
        print(f"T-100 import failed: {exc}", file=sys.stderr)
        return 1
    print(metadata["snapshot_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
