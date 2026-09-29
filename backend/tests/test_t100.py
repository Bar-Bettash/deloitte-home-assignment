from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from app.sources import t100
from app.sources.t100 import (
    CSV_MEMBER,
    FIELDS,
    RECENT_ORIGINS,
    RECENT_YEARS,
    ArchiveSpec,
    ImportResult,
    T100Error,
    coverage_by_airport_period,
    filter_eligible,
    import_archives,
    publish_t100_snapshot,
)


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
        _row(unique_carrier="FF", class_code="F", origin="EWB"),
    ]
    spec = _archive(tmp_path, "classes.zip", rows, "AK", 2024)
    imported = import_archives(tmp_path, (spec,))

    eligible = filter_eligible(imported.records)

    assert [row["UNIQUE_CARRIER"] for row in eligible] == ["AA"]
    assert Decimal(eligible[0]["SEATS"]) == Decimal("10.00")
    assert [row["UNIQUE_CARRIER"] for row in filter_eligible(imported.records, RECENT_ORIGINS)] == ["AA", "FF"]


def test_pvc_2024_december_remains_missing() -> None:
    records = {}
    for month in range(1, 12):
        row = _row(origin="PVC", year=2024, month=month, unique_carrier=f"A{month}")
        records[tuple(str(month) for _ in range(18))] = row

    coverage = coverage_by_airport_period(filter_eligible(records))

    assert coverage[("PVC", 2024)] == set(range(1, 12))
    assert 12 not in coverage[("PVC", 2024)]


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


def test_recent_scope_stages_snapshot_and_preserves_current_pointer(tmp_path, monkeypatch) -> None:
    rows = _recent_coverage_rows()
    counts = Counter(int(row["YEAR"]) for row in rows)
    imported = ImportResult(
        {("row", str(index)): row for index, row in enumerate(rows)},
        [{"sha256": "a" * 64}], counts, counts,
    )
    monkeypatch.setattr(t100, "import_archives", lambda _path, _specs: imported)
    monkeypatch.setattr(t100, "_write_parquet", lambda path, _rows: path.write_bytes(b"parquet"))
    monkeypatch.setattr(t100, "_verify_parquet", lambda _path, _count: None)
    data_root = tmp_path / "published"
    data_root.mkdir()
    (data_root / "current.json").write_bytes(b"legacy-pointer")

    metadata = publish_t100_snapshot(
        tmp_path, data_root=data_root, specs=_recent_specs(), years=RECENT_YEARS,
        origins=RECENT_ORIGINS, expected_unique_by_year=dict(counts),
        expected_eligible_by_year=dict(counts),
        imported_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )

    assert (data_root / "current.json").read_bytes() == b"legacy-pointer"
    assert metadata["population"]["years"] == [2024, 2025]
    assert "EWB" in metadata["population"]["origins"]
    assert metadata["source_complete_no_eligible_months"] == {
        "PVC-2024": [12], "PVC-2025": [1, 2, 3, 4, 12]
    }


def test_recent_scope_rejects_forced_promotion_before_import(tmp_path) -> None:
    with pytest.raises(ValueError, match="staged candidate"):
        publish_t100_snapshot(
            tmp_path, years=RECENT_YEARS, origins=RECENT_ORIGINS, publish_current=True
        )


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


def _recent_specs() -> tuple[ArchiveSpec, ...]:
    return tuple(
        ArchiveSpec(f"{state}-{year}.zip", state, year, 0, "0" * 64, 0)
        for year in RECENT_YEARS
        for state in ("AK", "CA", "CT", "MA", "ME", "NH", "RI", "VT")
    )


def _recent_coverage_rows() -> list[dict[str, str]]:
    missing = {("PVC", 2024): {12}, ("PVC", 2025): {1, 2, 3, 4, 12}}
    return [
        _row(origin=origin, year=year, month=month, unique_carrier=f"{origin}{year}{month}")
        for origin in sorted(RECENT_ORIGINS)
        for year in RECENT_YEARS
        for month in range(1, 13)
        if month not in missing.get((origin, year), set())
    ]


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
