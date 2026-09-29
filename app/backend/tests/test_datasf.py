from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import socketserver
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qs

import duckdb
import httpx
import pytest
from app.sources import datasf
from app.sources.datasf import (
    DATASET_URL,
    MAX_BYTES,
    PREDICATE,
    REQUIRED_COLUMNS,
    DataSFError,
    fetch_datasf,
    publish_datasf_snapshot,
)


def test_fetches_sequential_pages_with_fixed_scope_and_matching_counts() -> None:
    source_rows = [_row(index) for index in range(5_001)]
    requests: list[dict[str, list[str]]] = []
    client = _client_for_rows(source_rows, observed=requests)

    rows = _run_fetch(client)

    assert len(rows) == 5_001
    assert [request.get("$offset") for request in requests] == [
        None,
        ["0"],
        ["5000"],
        None,
    ]
    assert requests[0] == {"$select": ["count(*) AS count"], "$where": [PREDICATE]}
    assert requests[1]["$order"] == [":id"]
    assert requests[1]["$limit"] == ["5000"]
    assert requests[1]["$select"] == [",".join(REQUIRED_COLUMNS)]
    assert requests[-1] == requests[0]


def test_fetches_2024_2025_with_exact_derived_predicate() -> None:
    year_pair = (2024, 2025)
    requests: list[dict[str, list[str]]] = []
    rows = _run_fetch(
        _client_for_rows([_row(index, year_pair=year_pair) for index in range(48)], observed=requests),
        year_pair=year_pair,
    )

    assert len(rows) == 48
    assert {request["$where"][0] for request in requests} == {"activity_period >= '202401' AND activity_period <= '202512'"}


def test_rejects_unsupported_year_pair_before_request() -> None:
    with pytest.raises(ValueError, match="2023, 2024.*2024, 2025"):
        _run_fetch(_client_for_rows([]), year_pair=(2025, 2026))


@pytest.mark.parametrize("post_count", [5_000, 5_002])
def test_rejects_post_count_mismatch(post_count: int) -> None:
    client = _client_for_rows([_row(index) for index in range(5_001)], post_count=post_count)

    with pytest.raises(DataSFError, match="count changed"):
        _run_fetch(client)


def test_rejects_early_short_page() -> None:
    rows = [_row(index) for index in range(5_001)]
    client = _client_for_rows(rows, page_mutator=lambda offset, page: page[:-1] if offset == 0 else page)

    with pytest.raises(DataSFError, match="early short"):
        _run_fetch(client)


def test_rejects_extra_rows_beyond_pre_count() -> None:
    rows = [_row(index) for index in range(48)]
    client = _client_for_rows(rows, page_mutator=lambda _offset, page: page + [_row(999)])

    with pytest.raises(DataSFError, match="extra rows"):
        _run_fetch(client)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda row: row.__setitem__("terminal", ""), "missing or blank"),
        (lambda row: row.__setitem__("passenger_count", "-1"), "passenger count"),
        (lambda row: row.__setitem__("geo_summary", "Unknown"), "geography"),
        (lambda row: row.__setitem__("data_loaded_at", "not-a-date"), "data_loaded_at"),
    ],
)
def test_rejects_invalid_schema_or_values(mutate: Callable[[dict[str, str]], object], message: str) -> None:
    rows = [_row(index) for index in range(48)]
    mutate(rows[0])

    with pytest.raises(DataSFError, match=message):
        _run_fetch(_client_for_rows(rows))


def test_rejects_missing_required_column() -> None:
    rows = [_row(index) for index in range(48)]
    for row in rows:
        row.pop("terminal")

    with pytest.raises(DataSFError, match="schema"):
        _run_fetch(_client_for_rows(rows))


def test_rejects_duplicate_declared_raw_key() -> None:
    rows = [_row(index) for index in range(48)]
    rows[1] = dict(rows[0])

    with pytest.raises(DataSFError, match="duplicate"):
        _run_fetch(_client_for_rows(rows))


@pytest.mark.parametrize("year_pair", [(2023, 2024), (2024, 2025)])
def test_rejects_missing_enplaned_month_geography_cell(
    year_pair: tuple[int, int],
) -> None:
    rows = [_row(index, year_pair=year_pair) for index in range(47)]

    with pytest.raises(DataSFError, match="missing.*Enplaned"):
        _run_fetch(_client_for_rows(rows), year_pair=year_pair)


def test_rejects_row_count_above_four_page_cap_without_fetching_pages() -> None:
    requests: list[dict[str, list[str]]] = []
    client = _client_for_rows([], pre_count=20_001, observed=requests)

    with pytest.raises(DataSFError, match="20,000"):
        _run_fetch(client)
    assert len(requests) == 1


def test_rejects_cumulative_response_byte_cap() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, content=b"count\r\n48\r\n", request=request)
        return httpx.Response(200, content=b"x" * (MAX_BYTES + 1), request=request)

    with pytest.raises(DataSFError, match="10 MiB"):
        _run_fetch(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_rejects_http_timeout_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(DataSFError, match="timed out"):
        _run_fetch(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert calls == 1


def test_rejects_total_elapsed_time_cap() -> None:
    times = iter([0.0, 0.0, 0.0, 61.0])

    with pytest.raises(DataSFError, match="60 second"):
        _run_fetch(
            _client_for_rows([_row(index) for index in range(48)]),
            monotonic=lambda: next(times),
        )


def test_absolute_deadline_interrupts_stalled_stream_and_closes_response() -> None:
    release = threading.Event()

    class StalledStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b"count\r\n"
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.closed = True
            release.set()

    stream = StalledStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=request)

    with pytest.raises(DataSFError, match="60 second"):
        _run_fetch(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            time_limit_seconds=0.05,
        )
    assert stream.closed is True


def test_absolute_deadline_interrupts_real_httpx_trickling_headers(monkeypatch) -> None:
    class TrickleHandler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.recv(4096)
            response_prefix = b"HTTP/1.1 200 OK\r\nContent-Type: text/csv\r\n"
            for byte in response_prefix:
                try:
                    self.request.sendall(bytes([byte]))
                except OSError:
                    return
                time.sleep(0.03)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", 0), TrickleHandler) as server:
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        thread.start()
        monkeypatch.setattr(
            datasf,
            "DATASET_URL",
            f"http://127.0.0.1:{server.server_address[1]}/resource.csv",
        )

        async def probe() -> tuple[float, DataSFError]:
            client = httpx.AsyncClient(trust_env=False)
            started_at = time.monotonic()
            error: DataSFError | None = None
            try:
                await fetch_datasf(client, time_limit_seconds=0.15)
            except DataSFError as caught:
                elapsed = time.monotonic() - started_at
                error = caught
            else:
                raise AssertionError("deadline did not reject trickling headers")
            finally:
                await client.aclose()
            assert error is not None
            return elapsed, error

        elapsed, error = asyncio.run(probe())
        server.shutdown()

    assert "60 second" in str(error)
    assert elapsed < 0.35


def test_publishes_typed_parquet_manifest_and_current_pointer(tmp_path) -> None:
    data_root = tmp_path / "raw" / "datasf"
    retrieved_at = datetime(2026, 9, 26, 12, 30, tzinfo=UTC)

    metadata = _run_publish(
        _client_for_rows([_row(index) for index in range(48)]),
        data_root=data_root,
        retrieved_at=retrieved_at,
    )

    pointer = json.loads((data_root / "current.json").read_text())
    snapshot_dir = data_root / "snapshots" / metadata["snapshot_id"]
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    parquet_path = snapshot_dir / "data.parquet"
    assert pointer == {
        "snapshot_id": metadata["snapshot_id"],
        "manifest": f"snapshots/{metadata['snapshot_id']}/manifest.json",
    }
    assert manifest == metadata
    assert metadata["retrieved_at_utc"] == "2026-09-26T12:30:00Z"
    assert metadata["row_count"] == 48
    assert metadata["source_counts"] == {"pre": 48, "post": 48}
    assert metadata["enplaned_cell_count"] == 48
    assert metadata["validation_status"] == "accepted"
    assert metadata["content_sha256"] == hashlib.sha256(parquet_path.read_bytes()).hexdigest()
    assert metadata["request"]["pages"]["$order"] == ":id"
    assert duckdb.sql(
        "SELECT count(*), min(passenger_count), max(passenger_count) FROM read_parquet(?)",
        params=[str(parquet_path)],
    ).fetchone() == (48, 0, 47)
    assert duckdb.sql(
        "SELECT typeof(passenger_count), typeof(activity_period_start_date) FROM read_parquet(?) LIMIT 1",
        params=[str(parquet_path)],
    ).fetchone() == ("BIGINT", "TIMESTAMP")


def test_2024_2025_snapshot_is_staged_without_moving_current_pointer(tmp_path) -> None:
    data_root = tmp_path / "raw" / "datasf"
    _run_publish(_client_for_rows([_row(index) for index in range(48)]), data_root=data_root)
    pointer_before = (data_root / "current.json").read_bytes()
    year_pair = (2024, 2025)

    metadata = _run_publish(
        _client_for_rows([_row(index, year_pair=year_pair) for index in range(48)]),
        data_root=data_root,
        year_pair=year_pair,
    )

    assert metadata["validation_status"] == "staged"
    assert metadata["scope"]["activity_period_start"] == "202401"
    assert metadata["scope"]["activity_period_end"] == "202512"
    assert (data_root / "current.json").read_bytes() == pointer_before
    assert (data_root / "snapshots" / metadata["snapshot_id"] / "manifest.json").is_file()


def test_refresh_cli_stages_2024_2025_without_moving_current_pointer(
    tmp_path, monkeypatch, capsys
) -> None:
    data_root = tmp_path / "raw" / "datasf"
    _run_publish(_client_for_rows([_row(index) for index in range(48)]), data_root=data_root)
    pointer_before = (data_root / "current.json").read_bytes()
    rows = [_row(index, year_pair=(2024, 2025)) for index in range(48)]
    client = _client_for_rows(rows)
    monkeypatch.setattr(datasf, "DEFAULT_DATA_ROOT", data_root)
    monkeypatch.setattr(datasf.httpx, "AsyncClient", lambda: client)

    result = datasf.main(["--refresh", "--years", "2024", "2025"])

    assert result == 0
    snapshot_id = capsys.readouterr().out.strip()
    manifest = json.loads(
        (data_root / "snapshots" / snapshot_id / "manifest.json").read_text()
    )
    assert manifest["scope"]["activity_period_start"] == "202401"
    assert manifest["scope"]["activity_period_end"] == "202512"
    assert manifest["validation_status"] == "staged"
    assert (data_root / "current.json").read_bytes() == pointer_before


@pytest.mark.parametrize(
    "args, message",
    [
        (["--refresh"], "--refresh requires --years 2024 2025"),
        (
            ["--refresh", "--years", "2023", "2024"],
            "--refresh requires --years 2024 2025",
        ),
        (
            ["--verify-only", "--years", "2024", "2025"],
            "--years is only valid with --refresh",
        ),
        (
            ["--refresh", "--years", "2024", "2025", "--snapshot-id", "unused"],
            "--snapshot-id and --qualification are only valid with --verify-only",
        ),
    ],
)
def test_cli_rejects_invalid_mode_arguments(args, message, capsys) -> None:
    with pytest.raises(SystemExit, match="2"):
        datasf.main(args)

    assert message in capsys.readouterr().err


def test_verify_only_binds_saved_snapshot_to_qualification_without_network_or_pointer_change(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "raw" / "datasf"
    _run_publish(_client_for_rows([_row(index) for index in range(48)]), data_root=data_root)
    pointer_before = (data_root / "current.json").read_bytes()
    rows = [_row(index, year_pair=(2024, 2025)) for index in range(48)]
    metadata = _run_publish(_client_for_rows(rows), data_root=data_root, year_pair=(2024, 2025))
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps(_qualification(rows)), encoding="utf-8")
    monkeypatch.setattr(datasf, "DEFAULT_DATA_ROOT", data_root)
    monkeypatch.setattr(
        datasf.httpx,
        "AsyncClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )

    result = datasf.main(
        [
            "--verify-only",
            "--snapshot-id",
            str(metadata["snapshot_id"]),
            "--qualification",
            str(qualification),
        ]
    )

    assert result == 0
    assert (data_root / "current.json").read_bytes() == pointer_before


def test_verify_only_fails_closed_on_incomplete_qualification(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "raw" / "datasf"
    rows = [_row(index, year_pair=(2024, 2025)) for index in range(48)]
    metadata = _run_publish(_client_for_rows(rows), data_root=data_root, year_pair=(2024, 2025))
    qualification_payload = _qualification(rows)
    qualification_payload["sources"]["datasf"]["totals"].pop("canonical_content_sha256")
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps(qualification_payload), encoding="utf-8")
    monkeypatch.setattr(datasf, "DEFAULT_DATA_ROOT", data_root)

    args = [
        "--verify-only",
        "--snapshot-id",
        str(metadata["snapshot_id"]),
        "--qualification",
        str(qualification),
    ]
    assert datasf.main(args) == 1


def test_verify_only_rejects_valid_shaped_alias_snapshot_id(tmp_path) -> None:
    data_root = tmp_path / "raw" / "datasf"
    rows = [_row(index, year_pair=(2024, 2025)) for index in range(48)]
    metadata = _run_publish(
        _client_for_rows(rows), data_root=data_root, year_pair=(2024, 2025)
    )
    original = data_root / "snapshots" / str(metadata["snapshot_id"])
    alias_id = "datasf-" + "0" * 64
    alias = data_root / "snapshots" / alias_id
    original.rename(alias)
    manifest = json.loads((alias / "manifest.json").read_text())
    manifest["snapshot_id"] = alias_id
    (alias / "manifest.json").write_text(json.dumps(manifest))
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps(_qualification(rows)))

    with pytest.raises(DataSFError, match="manifest or qualification"):
        datasf.verify_datasf_snapshot(alias_id, qualification, data_root=data_root)


@pytest.mark.parametrize("failure_stage", ["fetch", "validation", "write"])
def test_failed_publication_preserves_previous_pointer_and_snapshot(tmp_path, monkeypatch, failure_stage: str) -> None:
    data_root = tmp_path / "raw" / "datasf"
    _run_publish(
        _client_for_rows([_row(index) for index in range(48)]),
        data_root=data_root,
        retrieved_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    pointer_before = (data_root / "current.json").read_bytes()
    snapshots_before = {path.relative_to(data_root): path.read_bytes() for path in (data_root / "snapshots").rglob("*") if path.is_file()}

    if failure_stage == "fetch":

        def failing_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
    else:
        failing_rows = [_row(index) for index in range(47 if failure_stage == "validation" else 48)]
        client = _client_for_rows(failing_rows)
        if failure_stage == "write":
            monkeypatch.setattr(
                datasf,
                "_write_parquet",
                lambda _path, _rows: (_ for _ in ()).throw(OSError("simulated write failure")),
            )

    with pytest.raises((DataSFError, OSError)):
        _run_publish(client, data_root=data_root)

    assert (data_root / "current.json").read_bytes() == pointer_before
    assert {path.relative_to(data_root): path.read_bytes() for path in (data_root / "snapshots").rglob("*") if path.is_file()} == snapshots_before
    assert not list((data_root / "snapshots").glob(".staging-*"))


def _run_fetch(client: httpx.AsyncClient, **kwargs) -> list[dict[str, str]]:
    async def run() -> list[dict[str, str]]:
        try:
            return await fetch_datasf(client, **kwargs)
        finally:
            await client.aclose()

    return asyncio.run(run())


def _run_publish(client: httpx.AsyncClient, **kwargs) -> dict[str, object]:
    async def run() -> dict[str, object]:
        try:
            return await publish_datasf_snapshot(client, **kwargs)
        finally:
            await client.aclose()

    return asyncio.run(run())


def _client_for_rows(
    rows: list[dict[str, str]],
    *,
    pre_count: int | None = None,
    post_count: int | None = None,
    page_mutator: Callable[[int, list[dict[str, str]]], list[dict[str, str]]] | None = None,
    observed: list[dict[str, list[str]]] | None = None,
) -> httpx.AsyncClient:
    count_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count_calls
        assert str(request.url).startswith(DATASET_URL)
        params = parse_qs(request.url.query.decode())
        if observed is not None:
            observed.append(params)
        if params.get("$select") == ["count(*) AS count"]:
            count_calls += 1
            value = (len(rows) if pre_count is None else pre_count) if count_calls == 1 else (len(rows) if post_count is None else post_count)
            return httpx.Response(200, content=f"count\r\n{value}\r\n".encode(), request=request)
        offset = int(params["$offset"][0])
        page = rows[offset : offset + 5_000]
        if page_mutator is not None:
            page = page_mutator(offset, page)
        return httpx.Response(200, content=_csv(page), request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _csv(rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    columns = list(rows[0]) if rows else list(REQUIRED_COLUMNS)
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def _row(index: int, *, year_pair: tuple[int, int] = (2023, 2024)) -> dict[str, str]:
    month_index = index % 24
    year = year_pair[month_index // 12]
    month = month_index % 12 + 1
    geography = "Domestic" if (index // 24) % 2 == 0 else "International"
    return {
        "activity_period": f"{year}{month:02d}",
        "activity_period_start_date": f"{year}-{month:02d}-01T00:00:00.000",
        "operating_airline": f"Airline {index}",
        "operating_airline_iata_code": f"X{index}",
        "published_airline": f"Published {index}",
        "published_airline_iata_code": f"P{index}",
        "geo_summary": geography,
        "geo_region": "US" if geography == "Domestic" else "Europe",
        "activity_type_code": "Enplaned",
        "price_category_code": "Other",
        "terminal": f"Terminal {index}",
        "boarding_area": f"Area {index}",
        "passenger_count": str(index),
        "data_as_of": "2026-09-22T00:00:00.000",
        "data_loaded_at": "2026-09-22T12:00:00.000",
    }


def _qualification(rows: list[dict[str, str]]) -> dict[str, object]:
    digest = datasf._canonical_sha256([tuple(row[column] for column in REQUIRED_COLUMNS) for row in rows])
    request = {
        "predicate": "activity_period >= '202401' AND activity_period <= '202512'",
        "order": ":id",
        "activity_period_start": "202401",
        "activity_period_end": "202512",
    }
    totals = {
        "rows": len(rows),
        "enplaned_month_geography_cells": 48,
        "canonical_content_sha256": digest,
        "duplicate_raw_keys": 0,
        "conflicting_raw_keys": 0,
    }
    return {
        "sources": {
            "datasf": {
                "dataset_id": "rkru-6vcg",
                "request": request,
                "count_request": {"pre_count": len(rows), "post_count": len(rows)},
                "totals": totals,
                "validation": {"schema_exact_15_columns": True, "accepted_pointer_updated": False},
            }
        }
    }


def test_verify_only_honors_data_root_and_labels_failures_as_verification(tmp_path, capsys) -> None:
    data_root = tmp_path / "raw" / "datasf"
    rows = [_row(index, year_pair=(2024, 2025)) for index in range(48)]
    metadata = _run_publish(_client_for_rows(rows), data_root=data_root, year_pair=(2024, 2025))
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps(_qualification(rows)), encoding="utf-8")
    base = ["--verify-only", "--snapshot-id", str(metadata["snapshot_id"]), "--qualification", str(qualification)]

    assert datasf.main([*base, "--data-root", str(data_root)]) == 0
    assert capsys.readouterr().out.strip() == metadata["snapshot_id"]

    assert datasf.main([*base, "--data-root", str(tmp_path / "missing")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("DataSF verification failed:")
    assert "refresh" not in err
