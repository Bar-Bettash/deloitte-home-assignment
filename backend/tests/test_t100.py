from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from app.sources import t100
from app.sources.t100 import (
    BTS_FORM_URL,
    CSV_MEMBER,
    EXPECTED_ARCHIVES,
    FIELDS,
    RECENT_ARCHIVES,
    RECENT_ORIGINS,
    RECENT_PARTIAL_COVERAGE,
    ArchiveSpec,
    RequestIdentity,
    T100Error,
    coverage_by_airport_period,
    filter_eligible,
    import_archives,
)


def test_recent_archive_specs_bind_qualified_csv_and_request_metadata() -> None:
    assert len(RECENT_ARCHIVES) == 16
    assert {(spec.state, spec.year) for spec in RECENT_ARCHIVES} == {
        (state, year)
        for state in ("AK", "CA", "CT", "MA", "ME", "NH", "RI", "VT")
        for year in (2024, 2025)
    }
    alaska_2025 = next(spec for spec in RECENT_ARCHIVES if (spec.state, spec.year) == ("AK", 2025))
    assert alaska_2025.csv_sha256 == "ca6aa78f3ae098038898e2cd1ef112f27138f0eeeea5b47279dc36ec67442ce1"
    assert alaska_2025.retrieved_at_utc == "2026-09-27T10:44:48Z"
    assert alaska_2025.request_identity == RequestIdentity(
        BTS_FORM_URL, FIELDS, "Alaska", "All", 2025
    )
    assert all(spec.csv_sha256 is None for spec in EXPECTED_ARCHIVES)


def test_qualified_csv_content_hash_is_enforced(tmp_path) -> None:
    spec = _archive(tmp_path, "qualified.zip", [_row()], "AK", 2024)
    with zipfile.ZipFile(tmp_path / spec.filename) as archive:
        csv_bytes = archive.getinfo(CSV_MEMBER).file_size
    qualified = replace(
        spec,
        csv_bytes=csv_bytes,
        csv_sha256="0" * 64,
        retrieved_at_utc="2026-09-27T10:42:52Z",
        request_identity=RequestIdentity(BTS_FORM_URL, FIELDS, "Alaska", "All", 2024),
    )

    with pytest.raises(T100Error, match="qualified bytes"):
        import_archives(tmp_path, (qualified,))


def test_exact_cross_archive_overlap_collapses(tmp_path) -> None:
    row = _row()
    first = _archive(tmp_path, "one.zip", [row], "AK", 2024)
    second = _archive(tmp_path, "two.zip", [row], "CA", 2024)

    result = import_archives(tmp_path, (first, second))

    assert result.input_by_year[2024] == 2
    assert result.unique_by_year[2024] == 1
    assert len(result.records) == 1


def test_conflicting_measures_for_same_full_key_fail(tmp_path) -> None:
    first = _archive(tmp_path, "one.zip", [_row()], "AK", 2024)
    second_row = _row()
    second_row["PASSENGERS"] = "99.00"
    second = _archive(tmp_path, "two.zip", [second_row], "CA", 2024)

    with pytest.raises(T100Error, match="conflicting measures"):
        import_archives(tmp_path, (first, second))


def test_filter_excludes_cargo_nonscheduled_and_zero_seat_rows(tmp_path) -> None:
    rows = [
        _row(unique_carrier="AA", class_code="F", seats="10.00"),
        _row(unique_carrier="BB", class_code="G", seats="10.00"),
        _row(unique_carrier="CC", class_code="L", seats="10.00"),
        _row(unique_carrier="DD", class_code="P", seats="10.00"),
        _row(unique_carrier="EE", class_code="F", seats="0.00"),
    ]
    spec = _archive(tmp_path, "classes.zip", rows, "AK", 2024)
    imported = import_archives(tmp_path, (spec,))

    eligible = filter_eligible(imported.records)

    assert [row["UNIQUE_CARRIER"] for row in eligible] == ["AA"]
    assert Decimal(eligible[0]["SEATS"]) == Decimal("10.00")


def test_pvc_2024_december_remains_missing() -> None:
    records = {}
    for month in range(1, 12):
        row = _row(origin="PVC", year=2024, month=month, unique_carrier=f"A{month}")
        records[tuple(str(month) for _ in range(18))] = row

    coverage = coverage_by_airport_period(filter_eligible(records))

    assert coverage[("PVC", 2024)] == set(range(1, 12))
    assert 12 not in coverage[("PVC", 2024)]


def test_recent_coverage_requires_ewb_and_exact_pvc_partial_months() -> None:
    full = set(range(1, 13))
    coverage = {
        (origin, year): RECENT_PARTIAL_COVERAGE.get((origin, year), full)
        for origin in RECENT_ORIGINS for year in (2024, 2025)
    }

    t100._validate_coverage(coverage, {2024, 2025}, RECENT_ORIGINS, RECENT_PARTIAL_COVERAGE)
    assert coverage[("EWB", 2024)] == coverage[("EWB", 2025)] == full
    assert coverage[("PVC", 2024)] == set(range(1, 12))
    assert coverage[("PVC", 2025)] == set(range(5, 12))
    with pytest.raises(T100Error, match="month coverage"):
        t100._validate_coverage(
            {**coverage, ("PVC", 2025): set(range(1, 12))},
            {2024, 2025}, RECENT_ORIGINS, RECENT_PARTIAL_COVERAGE,
        )


def test_archive_schema_and_qualified_bytes_are_enforced(tmp_path) -> None:
    spec = _archive(tmp_path, "valid.zip", [_row()], "AK", 2024)
    wrong = replace(spec, sha256="0" * 64)

    with pytest.raises(T100Error, match="qualified bytes"):
        import_archives(tmp_path, (wrong,))


def test_unquantizable_measure_is_rejected_as_t100_error(tmp_path) -> None:
    row = _row()
    row["PASSENGERS"] = "1e999999999"
    spec = _archive(tmp_path, "invalid-measure.zip", [row], "AK", 2024)

    with pytest.raises(T100Error, match="invalid measure"):
        import_archives(tmp_path, (spec,))


def test_bulk_parquet_write_preserves_schema_order_and_cleans_csv(tmp_path) -> None:
    path = tmp_path / "data.parquet"
    rows = [_row(unique_carrier="BB"), _row(unique_carrier="AA")]

    t100._write_parquet(path, rows)

    t100._verify_parquet(path, 2)
    assert not path.with_suffix(".csv").exists()
    assert t100.duckdb.sql(
        "SELECT unique_carrier, passengers FROM read_parquet(?)", params=[str(path)]
    ).fetchall() == [("AA", Decimal("5.00")), ("BB", Decimal("5.00"))]


def test_bulk_parquet_write_cleans_csv_when_duckdb_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "data.parquet"

    def fail_connect():
        raise t100.duckdb.Error("forced failure")

    monkeypatch.setattr(t100.duckdb, "connect", fail_connect)
    with pytest.raises(t100.duckdb.Error, match="forced failure"):
        t100._write_parquet(path, [_row()])
    assert not path.with_suffix(".csv").exists()


def test_verify_only_checks_saved_hash_identity_and_qualification(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "raw" / "t100"
    snapshot = data_root / "snapshots" / "pending"
    snapshot.mkdir(parents=True)
    parquet = snapshot / "data.parquet"
    parquet.write_bytes(b"qualified parquet")
    digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
    archives = _recent_manifest_archives()
    input_identity = hashlib.sha256("".join(item["sha256"] for item in archives).encode()).hexdigest()
    snapshot_id = "t100-" + hashlib.sha256(f"bts-t100-fmg:{input_identity}:{digest}".encode()).hexdigest()
    final = data_root / "snapshots" / snapshot_id
    snapshot.rename(final)
    parquet = final / "data.parquet"
    manifest = {
        "snapshot_id": snapshot_id, "validation_status": "staged", "archives": archives,
        "parquet_sha256": digest, "input_rows": {"2024": 162501, "2025": 165047},
        "unique_rows": {"2024": 160404, "2025": 163030},
        "eligible_rows": {"2024": 31453, "2025": 31839},
        "population": {"origins": sorted(RECENT_ORIGINS)}, "coverage_cells": 54,
        "missing_months": {"PVC-2024": [12], "PVC-2025": [1, 2, 3, 4, 12]},
    }
    (final / "manifest.json").write_text(json.dumps(manifest))
    qualification = Path(__file__).parents[1] / "docs/evidence/recent-source-qualification-20260927.json"
    monkeypatch.setattr(t100, "_verify_parquet", lambda path, rows: None)
    monkeypatch.setattr(t100, "_verify_recent_parquet", lambda path: None)

    args = ["--verify-only", "--snapshot-id", snapshot_id, "--qualification",
            str(qualification), "--data-root", str(data_root)]
    assert t100.main(args) == 0
    manifest["eligible_rows"] = {"2024": 63292, "2025": 0}
    (final / "manifest.json").write_text(json.dumps(manifest))
    assert t100.main(args) == 1
    manifest["eligible_rows"] = {"2024": 31453, "2025": 31839}
    (final / "manifest.json").write_text(json.dumps(manifest))
    parquet.write_bytes(b"tampered")
    assert t100.main(args) == 1


def test_verify_only_rejects_rehashed_parquet_with_corrupted_years(tmp_path) -> None:
    data_root = tmp_path / "raw" / "t100"
    pending = data_root / "snapshots" / "pending"
    pending.mkdir(parents=True)
    parquet = pending / "data.parquet"
    columns = ", ".join(f'"{name}" {kind}' for name, kind in t100.PARQUET_SCHEMA)
    connection = t100.duckdb.connect()
    try:
        connection.execute(f"CREATE TABLE snapshot ({columns})")
        connection.execute(
            "INSERT INTO snapshot SELECT 1,1,10,5,100,'AA',10001,'Carrier','00001','D',"
            "10000,'EWB','MA','US',10002,'LAX','CA','US','100','1',2024,(i%12)+1,'F','DU' "
            "FROM range(63292) t(i)"
        )
        connection.execute(f"COPY snapshot TO '{parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        connection.close()
    digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
    archives = _recent_manifest_archives()
    input_identity = hashlib.sha256("".join(item["sha256"] for item in archives).encode()).hexdigest()
    snapshot_id = "t100-" + hashlib.sha256(f"bts-t100-fmg:{input_identity}:{digest}".encode()).hexdigest()
    final = pending.with_name(snapshot_id)
    pending.rename(final)
    manifest = {
        "snapshot_id": snapshot_id, "validation_status": "staged", "archives": archives,
        "parquet_sha256": digest, "input_rows": {"2024": 162501, "2025": 165047},
        "unique_rows": {"2024": 160404, "2025": 163030},
        "eligible_rows": {"2024": 31453, "2025": 31839},
        "population": {"origins": sorted(RECENT_ORIGINS)}, "coverage_cells": 54,
        "missing_months": {"PVC-2024": [12], "PVC-2025": [1, 2, 3, 4, 12]},
    }
    (final / "manifest.json").write_text(json.dumps(manifest))
    qualification = Path(__file__).parents[1] / "docs/evidence/recent-source-qualification-20260927.json"

    assert t100.main(["--verify-only", "--snapshot-id", snapshot_id, "--qualification",
                      str(qualification), "--data-root", str(data_root)]) == 1


def _recent_manifest_archives() -> list[dict[str, object]]:
    return [{
        "filename": spec.filename, "state": spec.state, "year": spec.year,
        "zip_bytes": spec.zip_bytes, "sha256": spec.sha256, "csv_member": CSV_MEMBER,
        "csv_bytes": spec.csv_bytes, "csv_sha256": spec.csv_sha256, "csv_rows": spec.csv_rows,
        "retrieved_at_utc": spec.retrieved_at_utc, "request_identity": {
            "url": BTS_FORM_URL, "fields": list(FIELDS), "geography": spec.request_identity.geography,
            "period": "All", "year": spec.year,
        },
    } for spec in RECENT_ARCHIVES]


def _archive(
    directory,
    filename: str,
    rows: list[dict[str, str]],
    state: str,
    year: int,
) -> ArchiveSpec:
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=FIELDS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    path = directory / filename
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(CSV_MEMBER, csv_buffer.getvalue().encode())
    content = path.read_bytes()
    return ArchiveSpec(filename, state, year, len(content), hashlib.sha256(content).hexdigest(), len(rows))


def _row(
    *,
    unique_carrier: str = "AA",
    class_code: str = "F",
    seats: str = "10.00",
    origin: str = "ANC",
    year: int = 2024,
    month: int = 1,
) -> dict[str, str]:
    return {
        "DEPARTURES_SCHEDULED": "1.00",
        "DEPARTURES_PERFORMED": "1.00",
        "SEATS": seats,
        "PASSENGERS": "5.00",
        "DISTANCE": "100.00",
        "UNIQUE_CARRIER": unique_carrier,
        "AIRLINE_ID": "10001",
        "UNIQUE_CARRIER_NAME": f"Carrier {unique_carrier}",
        "UNIQUE_CARRIER_ENTITY": "00001",
        "REGION": "D",
        "ORIGIN_AIRPORT_ID": "10000",
        "ORIGIN": origin,
        "ORIGIN_STATE_ABR": "AK" if origin == "ANC" else "MA",
        "ORIGIN_COUNTRY": "US",
        "DEST_AIRPORT_ID": "10002",
        "DEST": "LAX",
        "DEST_STATE_ABR": "CA",
        "DEST_COUNTRY": "US",
        "AIRCRAFT_TYPE": "100",
        "AIRCRAFT_CONFIG": "1",
        "YEAR": str(year),
        "MONTH": str(month),
        "CLASS": class_code,
        "DATA_SOURCE": "DU",
    }
