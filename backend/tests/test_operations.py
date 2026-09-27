import hashlib
import json

import duckdb
import pytest
from app.calculations.operations import OperationsCalculationError, calculate_operations


def publish(root, rows, *, accepted=True):
    folder = root / "snapshots" / "ontime-test"
    folder.mkdir(parents=True)
    parquet = folder / "data.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE flights(year INTEGER, month INTEGER, origin VARCHAR, dest VARCHAR, "
            "reporting_airline VARCHAR, cancelled INTEGER, diverted INTEGER, flights INTEGER, "
            "dep_delay_minutes DOUBLE, taxi_out DOUBLE, dep_delay DOUBLE)"
        )
        if rows:
            connection.executemany("INSERT INTO flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        escaped = str(parquet).replace("'", "''")
        connection.execute(f"COPY flights TO '{escaped}' (FORMAT PARQUET)")
    manifest = {
        "snapshot_id": "ontime-test", "validation_status": "accepted" if accepted else "failed",
        "parquet_file": "data.parquet",
        "parquet_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
        "source": {"table": "FGJ", "name": "BTS reporting carrier", "index_url": "https://example.test"},
        "imported_at_utc": "2026-09-27T00:00:00Z",
        "request": {"year": 2024, "origin_airports": ["LAX", "SNA", "SFO"]},
        "coverage": {f"LAX-{m:02d}": 1 for m in range(1, 13)},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    (root / "current.json").write_text(json.dumps({
        "snapshot_id": "ontime-test", "manifest": "snapshots/ontime-test/manifest.json",
    }))
    return parquet


def row(month=1, *, origin="LAX", year=2024, carrier="AA", cancel=0, divert=0,
        delay=0, taxi=10, flights=1):
    return (year, month, origin, "LAX" if origin != "LAX" else "SFO", carrier,
            cancel, divert, flights, delay, taxi, -15)


def complete(**kwargs):
    return [row(month, **kwargs) for month in range(1, 13)]


def test_hand_calculated_independent_denominators_and_origin_scope(tmp_path):
    rows = complete() + [
        row(cancel=1, delay=999, taxi=999), row(divert=1, delay=999, taxi=999),
        row(delay=30, taxi=None, carrier="UA"), row(delay=None, taxi=20),
        row(origin="SNA", delay=500), row(year=2023, delay=500),
    ]
    publish(tmp_path, rows)
    result = calculate_operations("lax", tmp_path)
    assert result.scheduled_count == 16
    assert result.cancellation_rate.value == 6.25
    assert result.diversion_rate.value == 6.25
    assert result.cancellation_rate.denominator == 16
    assert result.departure_delay_minutes.value == pytest.approx(30 / 13)
    assert result.departure_delay_minutes.numerator == 30
    assert result.departure_delay_minutes.denominator == 13
    assert result.departure_delay_minutes.eligible_count == 14
    assert result.taxi_out_minutes.value == pytest.approx(140 / 13)
    assert result.taxi_out_minutes.denominator == 13
    assert result.carriers == ("AA", "UA")
    assert result.source.snapshot_id == "ontime-test"
    assert result.missing_months == ()


def test_early_departures_use_nonnegative_delay_minutes_not_signed_delay(tmp_path):
    publish(tmp_path, complete())
    result = calculate_operations("LAX", tmp_path)
    assert result.departure_delay_minutes.value == 0
    assert result.departure_delay_minutes.status == "ok"


@pytest.mark.parametrize("kind", ["empty", "incomplete", "invalid_flags", "invalid_flights"])
def test_no_valid_annual_denominator_is_unavailable(tmp_path, kind):
    rows = {"empty": [], "incomplete": complete()[:-1],
            "invalid_flags": complete(cancel=None), "invalid_flights": complete(flights=0)}[kind]
    publish(tmp_path, rows)
    result = calculate_operations("LAX", tmp_path)
    for metric in (result.cancellation_rate, result.diversion_rate,
                   result.departure_delay_minutes, result.taxi_out_minutes):
        assert metric.value is None
        assert metric.status == "unavailable"
        assert "insufficient" in metric.reason


@pytest.mark.parametrize("cancel,divert", [(1, 0), (0, 1)])
def test_all_cancelled_or_diverted_has_rates_but_no_means(tmp_path, cancel, divert):
    publish(tmp_path, complete(cancel=cancel, divert=divert, delay=100, taxi=100))
    result = calculate_operations("LAX", tmp_path)
    assert result.cancellation_rate.value == 100 * cancel
    assert result.diversion_rate.value == 100 * divert
    assert result.departure_delay_minutes.value is None
    assert result.taxi_out_minutes.denominator == 0


def test_missing_fields_do_not_drop_valid_scheduled_rows(tmp_path):
    publish(tmp_path, complete(delay=None, taxi=None) + [row(cancel=None)])
    result = calculate_operations("LAX", tmp_path)
    assert result.scheduled_count == 12
    assert result.invalid_row_count == 1
    assert result.cancellation_rate.value == 0
    assert result.departure_delay_minutes.denominator == 0
    assert result.departure_delay_minutes.eligible_count == 12
    assert result.taxi_out_minutes.value is None


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf")])
def test_invalid_nonnull_measure_fails_that_metric(tmp_path, bad):
    publish(tmp_path, complete(delay=bad))
    result = calculate_operations("LAX", tmp_path)
    assert result.departure_delay_minutes.value is None
    assert result.taxi_out_minutes.value == 10


def test_unaccepted_or_modified_snapshot_rejected(tmp_path):
    parquet = publish(tmp_path, complete(), accepted=False)
    with pytest.raises(OperationsCalculationError):
        calculate_operations("LAX", tmp_path)
    manifest_path = parquet.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["validation_status"] = "accepted"
    manifest_path.write_text(json.dumps(manifest))
    with parquet.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(OperationsCalculationError):
        calculate_operations("LAX", tmp_path)


@pytest.mark.parametrize("airport,year", [("BOS", 2024), ("LAX", 2023)])
def test_unsupported_scope_rejected(tmp_path, airport, year):
    with pytest.raises(OperationsCalculationError):
        calculate_operations(airport, tmp_path, year=year)
