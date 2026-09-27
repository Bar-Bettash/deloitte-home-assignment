from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pytest
from app.calculations.traffic import (
    DEFAULT_DATA_ROOT,
    TrafficCalculationError,
    TrafficResult,
    calculate_traffic,
    calculate_traffic_batch,
)
from app.contracts import NEW_ENGLAND


def test_calculates_annual_levels_occupancy_and_growth_from_performed_departures(
    tmp_path: Path,
) -> None:
    rows = _complete_rows()
    _publish_snapshot(tmp_path, rows)

    result = calculate_traffic("bos", tmp_path)

    assert isinstance(result, TrafficResult)
    assert result.airport == "BOS"
    assert result.source.snapshot_id == "t100-test-snapshot"
    assert [item.coverage.complete for item in result.annual] == [True, True]
    assert _values(result.annual[0]) == (1_200, 2_400, 120, 50.0)
    assert _values(result.annual[1]) == (1_440, 2_880, 144, 50.0)
    assert result.growth.status == "ok"
    assert result.growth.percent == 20.0


def test_sums_performed_not_scheduled_departures(tmp_path: Path) -> None:
    _publish_snapshot(tmp_path, _complete_rows(scheduled=10_000, performed_2024=3))

    result = calculate_traffic("BOS", tmp_path)

    assert result.annual[1].performed_departures.value == 36


def test_incomplete_year_makes_that_year_and_growth_unavailable(tmp_path: Path) -> None:
    rows = [row for row in _complete_rows() if not (row[0] == 2024 and row[1] == 12)]
    _publish_snapshot(tmp_path, rows)

    result = calculate_traffic("BOS", tmp_path)

    assert result.annual[0].passengers.status == "ok"
    assert result.annual[1].coverage.missing_months == (12,)
    assert result.annual[1].passengers.status == "unavailable"
    assert result.growth.status == "unavailable"


def test_zero_baseline_and_zero_seats_have_explicit_unavailable_ratios(tmp_path: Path) -> None:
    rows = _complete_rows(passengers_2023=0, seats_2024=0)
    _publish_snapshot(tmp_path, rows)

    result = calculate_traffic("BOS", tmp_path)

    assert result.annual[1].seats.value == 0
    assert result.annual[1].occupancy_percent.status == "unavailable"
    assert "zero" in (result.annual[1].occupancy_percent.reason or "")
    assert result.growth.status == "unavailable"
    assert "zero" in (result.growth.reason or "")


@pytest.mark.parametrize(
    ("field_index", "bad_value", "metric_name"),
    [
        (2, -1, "passengers"),
        (2, float("nan"), "passengers"),
        (3, None, "seats"),
        (3, 1.5, "seats"),
        (5, -2, "performed_departures"),
        (5, float("inf"), "performed_departures"),
    ],
)
def test_invalid_counts_never_become_plausible_zeros(
    tmp_path: Path, field_index: int, bad_value: float | None, metric_name: str
) -> None:
    rows = _complete_rows()
    changed = list(rows[12])
    changed[field_index] = bad_value
    rows[12] = tuple(changed)
    _publish_snapshot(tmp_path, rows)

    metric = getattr(calculate_traffic("BOS", tmp_path).annual[1], metric_name)

    assert metric.status == "unavailable"
    assert metric.value is None


def test_actual_accepted_snapshot_calculates_anc_bos_and_rejects_incomplete_pvc() -> None:
    if not (DEFAULT_DATA_ROOT / "current.json").is_file():
        pytest.skip("accepted local T-100 snapshot is not installed")

    batch = calculate_traffic_batch(["ANC", "BOS", "PVC"])
    anc, bos, pvc = (batch[airport] for airport in ("ANC", "BOS", "PVC"))

    assert _values(anc.annual[1]) == (2_660_772, 3_568_481, 40_017, pytest.approx(74.563154, abs=1e-6))
    assert _values(bos.annual[1]) == (21_294_487, 25_618_825, 187_549, pytest.approx(83.120467, abs=1e-6))
    assert pvc.annual[0].passengers.value == 7_535
    assert pvc.annual[1].coverage.missing_months == (12,)
    assert pvc.annual[1].passengers.status == "unavailable"
    assert pvc.growth.status == "unavailable"


def _values(annual: object) -> tuple[object, object, object, object]:
    return (
        annual.passengers.value,
        annual.seats.value,
        annual.performed_departures.value,
        annual.occupancy_percent.value,
    )


def _complete_rows(
    *,
    passengers_2023: float = 100,
    passengers_2024: float = 120,
    seats_2023: float = 200,
    seats_2024: float = 240,
    scheduled: float = 999,
    performed_2023: float = 10,
    performed_2024: float = 12,
) -> list[tuple[object, ...]]:
    rows = []
    for year in (2023, 2024):
        for month in range(1, 13):
            rows.append(
                (
                    year,
                    month,
                    passengers_2023 if year == 2023 else passengers_2024,
                    seats_2023 if year == 2023 else seats_2024,
                    scheduled,
                    performed_2023 if year == 2023 else performed_2024,
                    1_000.0,
                )
            )
    return rows


def _publish_snapshot(
    data_root: Path, rows: list[tuple[object, ...]], *, airport_rows=None,
) -> None:
    airport_rows = airport_rows if airport_rows is not None else {"BOS": rows}
    snapshot_id = "t100-test-snapshot"
    snapshot_dir = data_root / "snapshots" / snapshot_id
    snapshot_dir.mkdir(parents=True)
    parquet_path = snapshot_dir / "data.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            "CREATE TABLE source(year INTEGER, month INTEGER, passengers DOUBLE, seats DOUBLE, "
            "departures_scheduled DOUBLE, departures_performed DOUBLE, distance DOUBLE, origin VARCHAR)"
        )
        for airport, records in airport_rows.items():
            if records:
                connection.executemany(
                    "INSERT INTO source VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(*record, airport) for record in records],
                )
        escaped = str(parquet_path).replace("'", "''")
        connection.execute(f"COPY source TO '{escaped}' (FORMAT PARQUET)")
    finally:
        connection.close()
    manifest = {
        "snapshot_id": snapshot_id,
        "source": {"name": "BTS T-100 Segment All Carriers", "table": "FMG", "url": "https://example.test"},
        "imported_at_utc": "2026-09-26T21:03:39Z",
        "population": {"origins": list(airport_rows)},
        "parquet_file": "data.parquet",
        "parquet_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        "validation_status": "accepted",
    }
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (data_root / "current.json").write_text(
        json.dumps({"snapshot_id": snapshot_id, "manifest": f"snapshots/{snapshot_id}/manifest.json"}),
        encoding="utf-8",
    )


def test_batch_equals_single_for_22_airports_with_one_checksum_and_query(tmp_path, monkeypatch):
    from app.calculations import traffic

    airports = sorted(NEW_ENGLAND)
    assert len(airports) == 22
    records = {airport: _complete_rows(passengers_2024=120 + index)
               for index, airport in enumerate(airports)}
    _publish_snapshot(tmp_path, [], airport_rows=records)
    expected = {airport: calculate_traffic(airport, tmp_path) for airport in airports}
    calls = {"checksum": 0, "query": 0}
    digest, query = traffic.hashlib.file_digest, traffic._query_batch_rows

    def count_digest(*args, **kwargs):
        calls["checksum"] += 1
        return digest(*args, **kwargs)

    def count_query(*args, **kwargs):
        calls["query"] += 1
        return query(*args, **kwargs)

    monkeypatch.setattr(traffic.hashlib, "file_digest", count_digest)
    monkeypatch.setattr(traffic, "_query_batch_rows", count_query)
    actual = calculate_traffic_batch(airports, tmp_path)
    assert actual == expected
    assert calls == {"checksum": 1, "query": 1}


def test_batch_missing_invalid_and_zero_cases_match_single_results(tmp_path):
    invalid = _complete_rows()
    invalid[12] = (*invalid[12][:2], None, *invalid[12][3:])
    records = {"BOS": invalid, "PVC": _complete_rows()[:-1], "BDL": [],
               "BGR": _complete_rows(passengers_2023=0, seats_2024=0)}
    _publish_snapshot(tmp_path, [], airport_rows=records)
    actual = calculate_traffic_batch(records, tmp_path)
    assert actual == {airport: calculate_traffic(airport, tmp_path) for airport in records}
    assert actual["BOS"].annual[1].passengers.status == "unavailable"
    assert actual["PVC"].annual[1].coverage.missing_months == (12,)
    assert actual["BDL"].annual[0].coverage.missing_months == tuple(range(1, 13))
    assert actual["BGR"].growth.status == "unavailable"


def test_batch_normalizes_and_deduplicates_airports(tmp_path):
    _publish_snapshot(tmp_path, _complete_rows())
    assert list(calculate_traffic_batch(["bos", " BOS ", "BOS"], tmp_path)) == ["BOS"]


def test_empty_batch_needs_no_snapshot(tmp_path):
    assert calculate_traffic_batch([], tmp_path) == {}


def test_batch_rejects_unsupported_airport_before_query(tmp_path, monkeypatch):
    from app.calculations import traffic

    _publish_snapshot(tmp_path, _complete_rows())

    def unexpected_query(*args):
        pytest.fail("unsupported request must not query the snapshot")

    monkeypatch.setattr(traffic, "_query_batch_rows", unexpected_query)
    with pytest.raises(TrafficCalculationError, match="outside the accepted population"):
        calculate_traffic_batch(["BOS", "XYZ"], tmp_path)


def test_batch_missing_or_corrupt_snapshot_is_rejected(tmp_path):
    with pytest.raises(TrafficCalculationError, match="unavailable or invalid"):
        calculate_traffic_batch(["BOS"], tmp_path)
    _publish_snapshot(tmp_path, _complete_rows())
    parquet = tmp_path / "snapshots/t100-test-snapshot/data.parquet"
    with parquet.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(TrafficCalculationError, match="checksum"):
        calculate_traffic_batch(["BOS"], tmp_path)
