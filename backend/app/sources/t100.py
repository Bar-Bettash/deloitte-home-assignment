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
from datetime import UTC, datetime
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
RECENT_ORIGINS = REQUIRED_ORIGINS | {"EWB"}
LEGACY_PARTIAL_COVERAGE = {("PVC", 2024): set(range(1, 12))}
RECENT_PARTIAL_COVERAGE = {
    ("PVC", 2024): set(range(1, 12)),
    ("PVC", 2025): set(range(5, 12)),
}
ALLOWED_CLASSES = {"A", "C", "E", "F"}
OBSERVED_CLASSES = {"F", "G", "L", "P"}
DATA_SOURCES = {"DF", "DU", "IF", "IU"}
EXPECTED_UNIQUE_BY_YEAR = {2023: 153_307, 2024: 160_404}
EXPECTED_ELIGIBLE_BY_YEAR = {2023: 29_039, 2024: 31_428}
RECENT_UNIQUE_BY_YEAR = {2024: 160_404, 2025: 163_030}
RECENT_ELIGIBLE_BY_YEAR = {2024: 31_453, 2025: 31_839}

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
class RequestIdentity:
    url: str
    fields: tuple[str, ...]
    geography: str
    period: str
    year: int


@dataclass(frozen=True)
class ArchiveSpec:
    filename: str
    state: str
    year: int
    zip_bytes: int
    sha256: str
    csv_rows: int
    csv_bytes: int | None = None
    csv_sha256: str | None = None
    retrieved_at_utc: str | None = None
    request_identity: RequestIdentity | None = None


def _spec(filename: str, state: str, year: int, size: int, digest: str, rows: int) -> ArchiveSpec:
    return ArchiveSpec(filename, state, year, size, digest, rows)


def _recent_spec(
    state: str, year: int, geography: str, size: int, digest: str, rows: int,
    csv_bytes: int, csv_digest: str, retrieved_at: str,
) -> ArchiveSpec:
    return ArchiveSpec(
        f"{year}/{state}/archive.zip", state, year, size, digest, rows,
        csv_bytes, csv_digest, retrieved_at,
        RequestIdentity(BTS_FORM_URL, FIELDS, geography, "All", year),
    )


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

RECENT_ARCHIVES = (
    _recent_spec("AK", 2024, "Alaska", 589484, "cde3874661af59138e9e7f1898719ead9e7a23d8729e6babee56ebc0ec4379a7", 54759, 6851718, "9c815a72c909ff167b062d489d6a26aeed66bc9222adf873f4c8fa0da2c0ec74", "2026-09-27T10:42:52Z"),
    _recent_spec("CA", 2024, "California", 1021860, "758218ff304763979909585b15f2082f17a2e2676e9311c353e8db5406977586", 77193, 9608003, "ec61355d29c610f2464c63ddbfa0a95c53b128be85cf466bc2c6e21351efa3c7", "2026-09-27T10:43:16Z"),
    _recent_spec("CT", 2024, "Connecticut", 57669, "a8d7688c6c642b2efb0e32ccae38d5f4f2e40c54d46248361dcc9c2eb74e7008", 5064, 639346, "baf64771500926215470851bc5aa82b0d78d0bfedfa2742bad8cbe4b0aa3ff80", "2026-09-27T10:43:37Z"),
    _recent_spec("MA", 2024, "Massachusetts", 212165, "60661bdb8f5e8414a1ba24de147984e7af1023957d216d17d7aed1f8a612e641", 17116, 2077327, "a40470e27946ff3a52d1715091cf22a2ea99bfacb71d97bd7875c840f3121dfb", "2026-09-27T10:43:49Z"),
    _recent_spec("ME", 2024, "Maine", 36243, "0a79016e728f2be410d1d64eedcd0df80c1c883b5fbf822194802b536a0e48ce", 2912, 356868, "3f00e8e830c3a7b0cfd9a2920f10e701a6ee5b4ae01ca89aa1a353a8d762a89f", "2026-09-27T10:44:02Z"),
    _recent_spec("NH", 2024, "New Hampshire", 18514, "f4671c622dc498ca227eac8a67a0880b3b4b17ca34166b44e073669391ef0dd7", 1542, 189114, "394973a4723e9353a0ca3b1c3f0a9599eb056e8df8dc9b78ecf163190259d9dd", "2026-09-27T10:44:13Z"),
    _recent_spec("RI", 2024, "Rhode Island", 31529, "f9158261934dd1b994211d950d7f5b94fa97726d02cb7c1925470e175b77111a", 2636, 328996, "92acfdc83687baf27edb023d46c2dfdd56de1cb009fce5bf22aca8d523c71f18", "2026-09-27T10:44:22Z"),
    _recent_spec("VT", 2024, "Vermont", 15562, "1817906dc87b2c321a4eb179aea8c267165b78d840e3edb6b4dbc4e2e495f0d5", 1279, 156826, "12f398ff51f43aaf7ce5d89d5b4981d8cf9457aa1e68b10236b8c21011b52d6f", "2026-09-27T10:44:32Z"),
    _recent_spec("AK", 2025, "Alaska", 613831, "acc8898f4d5b82a1a25e2c7f91dbed6403818de9e16c1552661cd8c1f2d81f09", 57138, 7196562, "ca6aa78f3ae098038898e2cd1ef112f27138f0eeeea5b47279dc36ec67442ce1", "2026-09-27T10:44:48Z"),
    _recent_spec("CA", 2025, "California", 1013912, "f327575c539f7db4369dff67dc75bd6915645c71b76f0eea2fb4b7d10f252b33", 77020, 9582457, "23a784d65cc5bafdfcfc6e8aa4c928deeb72c08f88bc62ebc1b56a7bc6e036f4", "2026-09-27T10:45:09Z"),
    _recent_spec("CT", 2025, "Connecticut", 62444, "e8f0d6a1d9006ee8a2722660911207499ece2e99df906bc01eaf5f35db6002bf", 5451, 695589, "1898729e500a54fae9b8ea6df5b2f3cdb112c1d0a69b6ed0d754cb080a0dca61", "2026-09-27T10:45:26Z"),
    _recent_spec("MA", 2025, "Massachusetts", 208764, "2ec89c2f7490b70c10fa37d1e6440c4ec919b50f755aa51a7efc8d46557d2c48", 16664, 2025435, "6d632d53095e9a4b7e4e7f54c80f47df208a2ac4debde367807ff160186c6e71", "2026-09-27T10:45:40Z"),
    _recent_spec("ME", 2025, "Maine", 35709, "6ffcb88ea7ac105e70db48fbc65ea8bf7d0edebdca7fb04259c017dcb886f104", 2913, 358179, "1316d4d59c7500e18ac842bbd98191b052b99e96568aba5bbc06817de2e9c347", "2026-09-27T10:45:52Z"),
    _recent_spec("NH", 2025, "New Hampshire", 19633, "ee28a1a804e0632b826ca48c4e2984993dfbedc72d9c7addcfcf4051e3c52555", 1697, 208288, "e3a3584e19d543395a5627091e854c62d405761a8a151f20a60233367e34e54b", "2026-09-27T10:46:02Z"),
    _recent_spec("RI", 2025, "Rhode Island", 33325, "12784c04f907a6389d0c09c19c82ef196eac5c62434fff68a2fe64f750ba92b4", 2869, 357740, "13e4bc1308859c86e8580651f377a8b50adea560c0cf6b838fe3a28c1b759425", "2026-09-27T10:46:12Z"),
    _recent_spec("VT", 2025, "Vermont", 15366, "8757c116be4d6bb129478fd0b804a3f2e91d05ff9e2bb4737bb5bc73bc516dcb", 1295, 159259, "665994e4dce54c3e13eb1ec755f3c2f478cb56bb3f3f04d7ba8d80652a835a51", "2026-09-27T10:42:16Z"),
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
            _validate_qualified_metadata(spec, members[0].file_size)
            with archive.open(members[0]) as raw:
                csv_sha256 = hashlib.file_digest(raw, "sha256").hexdigest()
            if spec.csv_sha256 is not None and csv_sha256 != spec.csv_sha256:
                raise T100Error(f"T-100 CSV does not match its qualified bytes: {spec.filename}")
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
                "csv_bytes": members[0].file_size,
                "csv_sha256": csv_sha256,
                "csv_rows": row_count,
                "retrieved_at_utc": spec.retrieved_at_utc,
                "request_identity": (
                    {
                        "url": spec.request_identity.url,
                        "fields": list(spec.request_identity.fields),
                        "geography": spec.request_identity.geography,
                        "period": spec.request_identity.period,
                        "year": spec.request_identity.year,
                    }
                    if spec.request_identity is not None else None
                ),
            }
        )
    unique_by_year = Counter(int(row["YEAR"]) for row in records.values())
    return ImportResult(records, archive_metadata, input_by_year, unique_by_year)


def _validate_qualified_metadata(spec: ArchiveSpec, actual_csv_bytes: int) -> None:
    metadata = (spec.csv_bytes, spec.csv_sha256, spec.retrieved_at_utc, spec.request_identity)
    if all(value is None for value in metadata):
        return
    if any(value is None for value in metadata) or actual_csv_bytes != spec.csv_bytes:
        raise T100Error(f"T-100 CSV qualification metadata is incomplete: {spec.filename}")
    request = spec.request_identity
    assert request is not None and spec.retrieved_at_utc is not None
    if (
        request.url != BTS_FORM_URL or request.fields != FIELDS or request.period != "All"
        or request.year != spec.year or not request.geography
    ):
        raise T100Error(f"T-100 request identity is invalid: {spec.filename}")
    try:
        retrieved_at = datetime.fromisoformat(spec.retrieved_at_utc)
    except ValueError as exc:
        raise T100Error(f"T-100 retrieval timestamp is invalid: {spec.filename}") from exc
    if not spec.retrieved_at_utc.endswith("Z") or retrieved_at.utcoffset() != UTC.utcoffset(None):
        raise T100Error(f"T-100 retrieval timestamp is not UTC: {spec.filename}")


def filter_eligible(
    records: dict[tuple[str, ...], dict[str, str]], origins: set[str] = REQUIRED_ORIGINS
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
    expected_unique_by_year: dict[int, int] = EXPECTED_UNIQUE_BY_YEAR,
    expected_eligible_by_year: dict[int, int] = EXPECTED_ELIGIBLE_BY_YEAR,
    imported_at: datetime | None = None,
    promote: bool = True,
    origins: set[str] = REQUIRED_ORIGINS,
    partial_coverage: dict[tuple[str, int], set[int]] = LEGACY_PARTIAL_COVERAGE,
) -> dict[str, object]:
    imported = import_archives(input_dir, specs)
    if dict(imported.unique_by_year) != expected_unique_by_year:
        raise T100Error("T-100 union unique counts do not match qualification")
    rows = filter_eligible(imported.records, origins)
    eligible_by_year = Counter(int(row["YEAR"]) for row in rows)
    if dict(eligible_by_year) != expected_eligible_by_year:
        raise T100Error("T-100 eligible row counts do not match qualification")
    coverage = coverage_by_airport_period(rows)
    _validate_coverage(coverage, set(expected_unique_by_year), origins, partial_coverage)

    import_time = imported_at or datetime.now(UTC)
    if import_time.tzinfo is None:
        raise ValueError("imported_at must include a timezone")
    import_time = import_time.astimezone(UTC)
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
            if row["ORIGIN"] in origins
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
                "origins": sorted(origins),
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
            "parquet_file": "data.parquet",
            "parquet_sha256": parquet_sha256,
            "validation_status": "accepted" if promote else "staged",
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
                or published_metadata.get("validation_status") != metadata["validation_status"]
            ):
                raise T100Error("existing T-100 snapshot manifest is invalid")
        else:
            os.replace(staging_dir, final_dir)
            published_metadata = metadata
        if promote:
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


def _validate_coverage(
    coverage: dict[tuple[str, int], set[int]], years: set[int], origins: set[str],
    partial_coverage: dict[tuple[str, int], set[int]],
) -> None:
    expected_keys = {(airport, year) for airport in origins for year in years}
    if set(coverage) != expected_keys:
        raise T100Error("T-100 eligible airport-year coverage is incomplete")
    full_year = set(range(1, 13))
    for key, months in coverage.items():
        expected = partial_coverage.get(key, full_year)
        if months != expected:
            raise T100Error(f"T-100 month coverage is invalid for {key[0]}-{key[1]}")


def _write_parquet(path: Path, rows: list[dict[str, str]]) -> None:
    columns = ", ".join(f'"{name}" {data_type}' for name, data_type in PARQUET_SCHEMA)
    ordered = sorted(rows, key=lambda row: tuple(row[field] for field in RAW_KEY))
    normalized = path.with_suffix(".csv")
    values = (
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
    )
    connection = None
    try:
        with normalized.open("x", encoding="utf-8", newline="") as handle:
            csv.writer(handle, lineterminator="\n").writerows(values)
        connection = duckdb.connect()
        connection.execute(f"CREATE TABLE snapshot ({columns})")
        normalized_literal = "'" + str(normalized).replace("'", "''") + "'"
        connection.execute(
            f"COPY snapshot FROM {normalized_literal} (FORMAT CSV, HEADER FALSE, NULL '')"
        )
        parquet_literal = "'" + str(path).replace("'", "''") + "'"
        connection.execute(f"COPY snapshot TO {parquet_literal} (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        if connection is not None:
            connection.close()
        normalized.unlink(missing_ok=True)


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


def _verify_recent_parquet(path: Path) -> None:
    connection = duckdb.connect()
    try:
        counts = dict(connection.execute(
            "SELECT year, count(*) FROM read_parquet(?) GROUP BY year", [str(path)]
        ).fetchall())
        coverage = {
            (origin, year): set(months)
            for origin, year, months in connection.execute(
                "SELECT origin, year, list_sort(list_distinct(list(month))) "
                "FROM read_parquet(?) GROUP BY origin, year", [str(path)]
            ).fetchall()
        }
    finally:
        connection.close()
    expected_coverage = {
        (origin, year): RECENT_PARTIAL_COVERAGE.get((origin, year), set(range(1, 13)))
        for origin in RECENT_ORIGINS for year in RECENT_ELIGIBLE_BY_YEAR
    }
    if counts != RECENT_ELIGIBLE_BY_YEAR or coverage != expected_coverage:
        raise T100Error("saved T-100 content does not match qualified year or month coverage")


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


def verify_t100_snapshot(
    snapshot_id: str, qualification_path: Path, *, data_root: Path = DEFAULT_DATA_ROOT
) -> dict[str, object]:
    """Verify saved staged bytes and bind their manifest to qualification evidence."""
    snapshot_dir = data_root / "snapshots" / snapshot_id
    try:
        manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
        source = json.loads(qualification_path.read_text(encoding="utf-8"))["sources"]["t100"]
        totals, partitions, validation = source["totals"], source["partitions"], source["validation"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise T100Error("T-100 verification inputs are invalid or incomplete") from exc
    parquet = snapshot_dir / "data.parquet"
    qualified_archives = [
        {
            "filename": f"{item['year']}/{item['state']}/archive.zip",
            "state": item["state"], "year": item["year"], "zip_bytes": item["zip_bytes"], "sha256": item["zip_sha256"],
            "csv_member": item["csv_member"], "csv_bytes": item["csv_bytes"], "csv_sha256": item["csv_sha256"],
            "csv_rows": item["rows"], "retrieved_at_utc": item["retrieved_at_utc"],
            "request_identity": {
                "url": source["request_url"], "fields": source["request_fields"],
                "geography": item["geography"], "period": source["request_period"], "year": item["year"],
            },
        }
        for item in partitions
    ]
    expected_input = Counter()
    for item in partitions:
        expected_input[item["year"]] += item["rows"]
    digest = _sha256(parquet) if parquet.is_file() else None
    input_identity = hashlib.sha256(
        "".join(item["sha256"] for item in qualified_archives).encode("ascii")
    ).hexdigest()
    expected_id = f"t100-{hashlib.sha256(f'bts-t100-fmg:{input_identity}:{digest}'.encode('ascii')).hexdigest()}"
    if (
        not isinstance(manifest, dict) or manifest.get("snapshot_id") != snapshot_id
        or snapshot_id != expected_id
        or manifest.get("validation_status") != "staged"
        or manifest.get("archives") != qualified_archives
        or manifest.get("parquet_sha256") != digest
        or manifest.get("population", {}).get("origins") != sorted(RECENT_ORIGINS)
        or manifest.get("coverage_cells") != len(RECENT_ORIGINS) * 2
        or manifest.get("missing_months") != {"PVC-2024": [12], "PVC-2025": [1, 2, 3, 4, 12]}
        or manifest.get("input_rows") != {str(y): n for y, n in sorted(expected_input.items())}
        or manifest.get("unique_rows") != {str(y): n for y, n in RECENT_UNIQUE_BY_YEAR.items()}
        or manifest.get("eligible_rows") != {str(y): n for y, n in RECENT_ELIGIBLE_BY_YEAR.items()}
        or totals.get("eligible_rows_by_year") != {str(y): n for y, n in RECENT_ELIGIBLE_BY_YEAR.items()}
        or totals["unique_rows"] != sum(RECENT_UNIQUE_BY_YEAR.values())
        or totals["eligible_rows"] != sum(RECENT_ELIGIBLE_BY_YEAR.values())
        or totals["input_rows"] != sum(expected_input.values())
        or totals["exact_cross_partition_overlaps"] != totals["input_rows"] - totals["unique_rows"]
        or totals["conflicting_raw_keys"] != 0
        or set(validation) != {"all_http_status_200", "all_zip_crc_passed", "all_schemas_exact", "all_row_validations_passed"}
        or not all(validation.values())
    ):
        raise T100Error("saved T-100 snapshot does not match its manifest or qualification")
    _verify_parquet(parquet, totals["eligible_rows"])
    _verify_recent_parquet(parquet)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the qualified BTS T-100 archives")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stage", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    parser.add_argument("--years", type=int, nargs="+")
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--snapshot-id")
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args(argv)
    if args.stage and (args.years != [2024, 2025] or args.input_dir is None):
        parser.error("--stage requires --years 2024 2025 and --input-dir")
    if args.verify_only and (not args.snapshot_id or not args.qualification):
        parser.error("--verify-only requires --snapshot-id and --qualification")
    try:
        if args.verify_only:
            metadata = verify_t100_snapshot(args.snapshot_id, args.qualification, data_root=args.data_root)
        else:
            metadata = publish_t100_snapshot(
                args.input_dir, data_root=args.data_root, specs=RECENT_ARCHIVES,
                expected_unique_by_year=RECENT_UNIQUE_BY_YEAR,
                expected_eligible_by_year=RECENT_ELIGIBLE_BY_YEAR, promote=False,
                origins=RECENT_ORIGINS, partial_coverage=RECENT_PARTIAL_COVERAGE)
    except (T100Error, OSError, duckdb.Error, KeyError, TypeError, ValueError, AttributeError) as exc:
        print(f"T-100 import failed: {exc}", file=sys.stderr)
        return 1
    print(metadata["snapshot_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
