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
from app.sources import ontime
from app.sources.ontime import (
    MONTHS,
    OUTPUT_FIELDS,
    SELECTED_FIELDS,
    OnTimeError,
    _consume_archive,
    acquire_archives,
    archive_filename,
    archive_url,
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


def test_stages_all_2025_archives_without_changing_accepted_pointer(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    historical = tmp_path / "historical"
    recent = tmp_path / "recent"
    _complete_archives(historical)
    publish_ontime_snapshot(historical, data_root=data_root)
    accepted_pointer = (data_root / "current.json").read_bytes()
    _complete_archives(recent, year=2025)

    manifest = publish_ontime_snapshot(recent, data_root=data_root, source_year=2025)

    assert manifest["validation_status"] == "staged"
    assert manifest["request"]["year"] == 2025
    assert [item["month"] for item in manifest["archives"]] == list(MONTHS)
    assert all(item["filename"] == archive_filename(item["month"], 2025) for item in manifest["archives"])
    assert all(item["url"] == archive_url(item["month"], 2025) for item in manifest["archives"])
    assert (data_root / "current.json").read_bytes() == accepted_pointer


def test_stage_cli_reads_month_nested_qualification_archives(tmp_path: Path, capsys) -> None:
    data_root = tmp_path / "data"
    historical = tmp_path / "historical"
    recent = tmp_path / "recent"
    _complete_archives(historical)
    publish_ontime_snapshot(historical, data_root=data_root)
    accepted_pointer = (data_root / "current.json").read_bytes()
    _complete_archives(recent, year=2025, monthly_layout=True)

    assert ontime.main([
        "--stage", "--year", "2025", "--input-dir", str(recent),
        "--data-root", str(data_root),
    ]) == 0

    snapshot_id = capsys.readouterr().out.strip()
    manifest = json.loads(
        (data_root / "snapshots" / snapshot_id / "manifest.json").read_text()
    )
    assert manifest["validation_status"] == "staged"
    assert [item["month"] for item in manifest["archives"]] == list(MONTHS)
    assert (data_root / "current.json").read_bytes() == accepted_pointer


def test_stage_and_verify_only_cli_preserve_pointer_and_do_not_regenerate(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    data_root, historical, recent = (tmp_path / name for name in ("data", "historical", "recent"))
    _complete_archives(historical)
    publish_ontime_snapshot(historical, data_root=data_root)
    pointer = (data_root / "current.json").read_bytes()
    _complete_archives(recent, year=2025)

    assert ontime.main([
        "--stage", "--year", "2025", "--input-dir", str(recent),
        "--data-root", str(data_root),
    ]) == 0
    snapshot_id = capsys.readouterr().out.strip()
    manifest = json.loads(
        (data_root / "snapshots" / snapshot_id / "manifest.json").read_text()
    )
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps(_qualification(manifest)), encoding="utf-8")
    monkeypatch.setattr(ontime, "acquire_archives", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetch")))
    monkeypatch.setattr(ontime, "publish_ontime_snapshot", lambda *a, **k: (_ for _ in ()).throw(AssertionError("regenerate")))

    assert ontime.main([
        "--verify-only", "--snapshot-id", snapshot_id,
        "--qualification", str(qualification), "--data-root", str(data_root),
    ]) == 0
    assert (data_root / "current.json").read_bytes() == pointer
    final = data_root / "snapshots" / snapshot_id
    alias_id = "ontime-" + "0" * 64
    alias = data_root / "snapshots" / alias_id
    final.rename(alias)
    manifest["snapshot_id"] = alias_id
    (alias / "manifest.json").write_text(json.dumps(manifest))
    assert ontime.main([
        "--verify-only", "--snapshot-id", alias_id,
        "--qualification", str(qualification), "--data-root", str(data_root),
    ]) == 1
    manifest["snapshot_id"] = snapshot_id
    (alias / "manifest.json").write_text(json.dumps(manifest))
    alias.rename(final)
    bad_qualification = _qualification(manifest)
    bad_qualification["sources"]["ontime"]["request"]["year"] = 2024
    qualification.write_text(json.dumps(bad_qualification))
    assert ontime.main([
        "--verify-only", "--snapshot-id", snapshot_id,
        "--qualification", str(qualification), "--data-root", str(data_root),
    ]) == 1
    bad_qualification = _qualification(manifest)
    bad_qualification["sources"]["ontime"]["partitions"].pop()
    qualification.write_text(json.dumps(bad_qualification))
    assert ontime.main([
        "--verify-only", "--snapshot-id", snapshot_id,
        "--qualification", str(qualification), "--data-root", str(data_root),
    ]) == 1
    qualification.write_text(json.dumps(_qualification(manifest)))
    manifest["archives"][0]["csv_sha256"] = "0" * 64
    (data_root / "snapshots" / snapshot_id / "manifest.json").write_text(json.dumps(manifest))
    assert ontime.main([
        "--verify-only", "--snapshot-id", snapshot_id,
        "--qualification", str(qualification), "--data-root", str(data_root),
    ]) == 1


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

    _, _, csv_sha256, csv_crc32, source_rows, scoped_rows = _consume_archive(
        source, 1, writer, identities, coverage, carriers
    )

    assert source_rows == 547_271
    assert scoped_rows == 29_003
    assert len(csv_sha256) == 64
    assert len(csv_crc32) == 8
    assert coverage == Counter({("LAX", 1): 15_228, ("SFO", 1): 10_133, ("SNA", 1): 3_642})


def _complete_archives(
    directory: Path, *, year: int = 2024, monthly_layout: bool = False
) -> None:
    directory.mkdir(parents=True)
    airports = ("ANC", "LAX", "SFO", "SNA") if year == 2025 else ("LAX", "SFO", "SNA")
    for month in MONTHS:
        rows = [
            _row(month, airport, index, year=year)
            for index, airport in enumerate(airports, 1)
        ]
        archive_dir = directory / str(month) if monthly_layout else directory
        _write_archive(archive_dir / archive_filename(month, year), rows)


def _qualification(manifest: dict[str, object]) -> dict[str, object]:
    coverage = manifest["coverage"]
    partitions = []
    for archive in manifest["archives"]:
        month = archive["month"]
        scoped_rows = sum(value for key, value in coverage.items() if key.endswith(f"-{month:02d}"))
        partitions.append({
            "month": month, "year": 2025, "url": archive["url"],
            "zip_bytes": archive["zip_bytes"], "zip_sha256": archive["sha256"],
            "csv_bytes": archive["csv_bytes"], "csv_sha256": archive["csv_sha256"],
            "csv_crc32": archive["csv_crc32"],
            "source_rows": archive["source_rows"], "scoped_rows": scoped_rows,
        })
    return {"sources": {"ontime": {
        "request": manifest["request"], "partition_count": 12, "partitions": partitions,
        "totals": {"scoped_rows": manifest["row_count"], "exact_duplicate_rows": 0,
                   "conflicting_identity_keys": 0},
    }}}


def _row(month: int, origin: str, number: int, *, year: int = 2024) -> dict[str, str]:
    row = {field: "" for field in SELECTED_FIELDS}
    row.update(
        {
            "Year": str(year),
            "Month": str(month),
            "FlightDate": f"{year}-{month:02d}-01",
            "Reporting_Airline": "AA",
            "Flight_Number_Reporting_Airline": str(number),
            "OriginAirportID": {
                "ANC": "10299", "LAX": "12892", "SFO": "14771", "SNA": "14908"
            }[origin],
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
            f"On_Time_Reporting_Carrier_On_Time_Performance_(1987_present)_{rows[0].get('Year', '2024')}_{rows[0].get('Month', '1')}.csv",
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


def _ranged_server(bodies: dict[str, bytes], requests: list[tuple[str, str, str | None]]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = bodies.get(str(request.url))
        requests.append((request.method, str(request.url), request.headers.get("range")))
        if body is None:
            return httpx.Response(404)
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(body)), "Last-Modified": "Mon, 01 Sep 2025 00:00:00 GMT"})
        start, end = (int(value) for value in request.headers["range"].removeprefix("bytes=").split("-"))
        return httpx.Response(206, content=body[start:end + 1], headers={
            "Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{len(body)}",
        })

    return httpx.MockTransport(handler)


def test_acquisition_downloads_in_ranges_writes_metadata_and_reuses_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ontime, "RANGE_BYTES", 97)
    bodies = {archive_url(month): _archive_bytes([_row(month, "LAX", 1)]) for month in (2, 3)}
    requests: list[tuple[str, str, str | None]] = []

    acquired = acquire_archives(tmp_path, transport=_ranged_server(bodies, requests), months=(2, 3))

    for item, month in zip(acquired, (2, 3), strict=True):
        body = bodies[archive_url(month)]
        assert (tmp_path / archive_filename(month)).read_bytes() == body
        assert item["month"] == month and item["url"] == archive_url(month)
        assert item["zip_bytes"] == len(body) and item["sha256"] == hashlib.sha256(body).hexdigest()
        assert item["last_modified"] == "Mon, 01 Sep 2025 00:00:00 GMT"
        ranges = [header for method, url, header in requests if method == "GET" and url == archive_url(month)]
        assert len(ranges) == -(-len(body) // 97) > 1 and ranges[0] == "bytes=0-96"
    assert json.loads((tmp_path / "acquisition.json").read_text()) == {"archives": acquired}
    assert not list(tmp_path.glob(".*.part"))

    requests.clear()
    assert acquire_archives(tmp_path, transport=_ranged_server(bodies, requests), months=(2, 3)) == acquired
    assert requests == []

    (tmp_path / archive_filename(3)).write_bytes(b"stale")
    again = acquire_archives(tmp_path, transport=_ranged_server(bodies, requests), months=(2, 3))
    assert again[0] == acquired[0]
    assert (tmp_path / archive_filename(3)).read_bytes() == bodies[archive_url(3)]
    assert {url for _method, url, _range in requests} == {archive_url(3)}


def test_acquisition_http_error_is_ontime_error_and_keeps_metadata(tmp_path: Path) -> None:
    (tmp_path / "acquisition.json").write_text('{"archives": []}')
    with pytest.raises(OnTimeError, match="archive 2 acquisition failed"):
        acquire_archives(tmp_path, transport=_ranged_server({}, []), months=(2,))
    assert json.loads((tmp_path / "acquisition.json").read_text()) == {"archives": []}
    assert not (tmp_path / archive_filename(2)).exists()
