from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections import Counter
from pathlib import Path

import duckdb
import httpx
import pytest
from app.sources.ontime import (
    MONTHS,
    OUTPUT_FIELDS,
    SELECTED_FIELDS,
    OnTimeError,
    _consume_archive,
    acquire_archives,
    archive_filename,
    publish_ontime_snapshot,
)


def test_publishes_complete_typed_snapshot_and_preserves_conditional_nulls(tmp_path: Path) -> None:
    archives = tmp_path / "archives"
    data_root = tmp_path / "data"
    _complete_archives(archives)

    manifest = publish_ontime_snapshot(archives, data_root=data_root)

    assert manifest["validation_status"] == "accepted"
    assert manifest["row_count"] == 36
    assert len(manifest["coverage"]) == 36
    pointer = json.loads((data_root / "current.json").read_text())
    assert pointer["snapshot_id"] == manifest["snapshot_id"]
    parquet = data_root / pointer["manifest"]
    parquet = parquet.parent / manifest["parquet_file"]
    connection = duckdb.connect()
    try:
        nulls = connection.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE dep_delay_minutes IS NULL "
            "AND taxi_out IS NULL AND cancellation_code IS NULL",
            [str(parquet)],
        ).fetchone()[0]
    finally:
        connection.close()
    assert nulls == 36


def test_missing_month_fails_before_pointer_replacement(tmp_path: Path) -> None:
    archives = tmp_path / "archives"
    data_root = tmp_path / "data"
    _complete_archives(archives)
    published = publish_ontime_snapshot(archives, data_root=data_root)
    previous_pointer = (data_root / "current.json").read_bytes()
    (archives / archive_filename(12)).unlink()

    with pytest.raises(OnTimeError, match="missing.*month 12"):
        publish_ontime_snapshot(archives, data_root=data_root)

    assert (data_root / "current.json").read_bytes() == previous_pointer
    assert (data_root / "snapshots" / published["snapshot_id"] / "data.parquet").is_file()


def test_conflicting_duplicate_flight_identity_fails(tmp_path: Path) -> None:
    archives = tmp_path / "archives"
    _complete_archives(archives)
    row = _row(1, "LAX", 1)
    conflict = dict(row)
    conflict["Cancelled"] = "1.00"
    _write_archive(archives / archive_filename(1), [row, conflict, _row(1, "SFO", 2), _row(1, "SNA", 3)])

    with pytest.raises(OnTimeError, match="conflicting"):
        publish_ontime_snapshot(archives, data_root=tmp_path / "data")


@pytest.mark.parametrize(("field", "value"), [("Cancelled", "2.00"), ("Diverted", "-1")])
def test_binary_flags_are_enforced(tmp_path: Path, field: str, value: str) -> None:
    archives = tmp_path / "archives"
    _complete_archives(archives)
    rows = [_row(1, airport, index) for index, airport in enumerate(("LAX", "SFO", "SNA"), 1)]
    rows[0][field] = value
    _write_archive(archives / archive_filename(1), rows)

    with pytest.raises(OnTimeError, match="nonbinary"):
        publish_ontime_snapshot(archives, data_root=tmp_path / "data")


def test_missing_required_header_and_bad_zip_fail(tmp_path: Path) -> None:
    archives = tmp_path / "archives"
    _complete_archives(archives)
    row = _row(1, "LAX", 1)
    row.pop("TaxiOut")
    _write_archive(archives / archive_filename(1), [row], fields=tuple(row))
    with pytest.raises(OnTimeError, match="required headers"):
        publish_ontime_snapshot(archives, data_root=tmp_path / "data-one")

    (archives / archive_filename(1)).write_bytes(b"not a zip")
    with pytest.raises(OnTimeError, match="ZIP/CSV"):
        publish_ontime_snapshot(archives, data_root=tmp_path / "data-two")


def test_acquisition_rejects_content_range_and_never_installs_partial_file(tmp_path: Path) -> None:
    body = _archive_bytes([_row(2, "LAX", 1)])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(body))})
        headers = {
            "Content-Length": str(len(body)),
            "Content-Range": f"bytes 1-{len(body)}/{len(body)}",
        }
        return httpx.Response(206, headers=headers, content=body)

    with pytest.raises(OnTimeError, match="invalid Content-Range"):
        acquire_archives(tmp_path, transport=httpx.MockTransport(handler), months=(2,))

    assert not (tmp_path / archive_filename(2)).exists()


def test_acquisition_deadline_wraps_header_and_body(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": "10"})
        import asyncio

        await asyncio.sleep(0.05)
        return httpx.Response(200, headers={"Content-Length": "10"}, content=b"0123456789")

    with pytest.raises(OnTimeError, match="whole-request deadline"):
        acquire_archives(
            tmp_path,
            transport=httpx.MockTransport(handler),
            months=(2,),
            deadline_seconds=0.01,
        )


def test_duplicate_headers_and_extra_cells_are_rejected(tmp_path: Path) -> None:
    archives = tmp_path / "archives"
    _complete_archives(archives)
    path = archives / archive_filename(1)
    header = list(SELECTED_FIELDS) + ["TaxiOut"]
    values = [_row(1, "LAX", 1).get(field, "") for field in header]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.csv", ",".join(header) + "\n" + ",".join(values) + "\n")
    with pytest.raises(OnTimeError, match="duplicate CSV headers"):
        publish_ontime_snapshot(archives, data_root=tmp_path / "data-one")

    header = list(SELECTED_FIELDS)
    values = [_row(1, "LAX", 1).get(field, "") for field in header] + ["extra"]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.csv", ",".join(header) + "\n" + ",".join(values) + "\n")
    with pytest.raises(OnTimeError, match="malformed CSV row cells"):
        publish_ontime_snapshot(archives, data_root=tmp_path / "data-two")


def test_qualified_january_probe_matches_recorded_scope(tmp_path: Path) -> None:
    source = Path("/private/tmp/deloitte-ontime-2024-01.zip")
    if not source.is_file():
        pytest.skip("qualified January archive is unavailable")
    assert source.stat().st_size == 27_573_265
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "fe089b45523f9d4ac0ccd0e176a543d274e914ee5ae5bd384dbaafc7dc06ebfd"
    )
    identities: dict[tuple[str, ...], tuple[str | None, ...]] = {}
    coverage: Counter[tuple[str, int]] = Counter()
    carriers = {airport: set() for airport in ("LAX", "SFO", "SNA")}
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(OUTPUT_FIELDS)

    _, _, source_rows, scoped_rows = _consume_archive(
        source, 1, writer, identities, coverage, carriers
    )

    assert source_rows == 547_271
    assert scoped_rows == 29_003
    assert coverage == Counter({("LAX", 1): 15_228, ("SFO", 1): 10_133, ("SNA", 1): 3_642})


def _complete_archives(directory: Path) -> None:
    directory.mkdir(parents=True)
    for month in MONTHS:
        rows = [_row(month, airport, index) for index, airport in enumerate(("LAX", "SFO", "SNA"), 1)]
        _write_archive(directory / archive_filename(month), rows)


def _row(month: int, origin: str, number: int) -> dict[str, str]:
    row = {field: "" for field in SELECTED_FIELDS}
    row.update(
        {
            "Year": "2024",
            "Month": str(month),
            "FlightDate": f"2024-{month:02d}-01",
            "Reporting_Airline": "AA",
            "Flight_Number_Reporting_Airline": str(number),
            "OriginAirportID": {"LAX": "12892", "SFO": "14771", "SNA": "14908"}[origin],
            "DestAirportID": "12478",
            "CRSDepTime": "0800",
            "CRSArrTime": "1600",
            "Origin": origin,
            "Dest": "JFK",
            "Cancelled": "0.00",
            "Diverted": "0.00",
            "CRSElapsedTime": "300.00",
            "Flights": "1.00",
            "Distance": "2475.00",
        }
    )
    return row


def _write_archive(path: Path, rows: list[dict[str, str]], fields: tuple[str, ...] = SELECTED_FIELDS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"On_Time_Reporting_Carrier_On_Time_Performance_(1987_present)_2024_{rows[0].get('Month', '1')}.csv",
            csv_buffer.getvalue().encode(),
        )


def _archive_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.BytesIO()
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=SELECTED_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.csv", csv_buffer.getvalue().encode())
    return buffer.getvalue()
