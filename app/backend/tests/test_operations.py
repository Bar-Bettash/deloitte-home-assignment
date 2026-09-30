import hashlib
import json
from pathlib import Path

import duckdb
import pytest
from app.calculations.operations import OperationsCalculationError, calculate_operations
from app.sources.bundle import BundleContext, SnapshotRef, load_bundle


def publish(root, rows, *, accepted=True, year=2024, origins=None):
    origins = origins or ["LAX", "SNA", "SFO"]
    folder = root / "snapshots" / "ontime-test"
    folder.mkdir(parents=True)
    parquet = folder / "data.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE flights(year INTEGER, month INTEGER, origin VARCHAR, dest VARCHAR, "
            "reporting_airline VARCHAR, cancelled INTEGER, diverted INTEGER, flights INTEGER, "
            "dep_delay_minutes DOUBLE, taxi_out DOUBLE, dep_delay DOUBLE, carrier_delay DOUBLE, "
            "weather_delay DOUBLE, nas_delay DOUBLE, security_delay DOUBLE, late_aircraft_delay DOUBLE)"
        )
        if rows:
            connection.executemany("INSERT INTO flights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        escaped = str(parquet).replace("'", "''")
        connection.execute(f"COPY flights TO '{escaped}' (FORMAT PARQUET)")
    manifest = {
        "snapshot_id": "ontime-test", "validation_status": "accepted" if accepted else "failed",
        "parquet_file": "data.parquet",
        "parquet_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
        "source": {"table": "FGJ", "name": "BTS reporting carrier", "index_url": "https://example.test"},
        "imported_at_utc": "2026-09-27T00:00:00Z",
        "request": {"year": year, "origin_airports": origins},
        "coverage": {f"LAX-{m:02d}": 1 for m in range(1, 13)},
    }
    (folder / "manifest.json").write_text(json.dumps(manifest))
    (root / "current.json").write_text(json.dumps({
        "snapshot_id": "ontime-test", "manifest": "snapshots/ontime-test/manifest.json",
    }))
    return parquet


def bundle_for(root, parquet, *, year=2025):
    manifest_path = parquet.parent / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    evidence = root / "evidence.json"
    evidence.write_text("{}")
    ref = SnapshotRef(
        source="ontime",
        snapshot_id="ontime-test",
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        content_sha256=hashlib.sha256(parquet.read_bytes()).hexdigest(),
        manifest_path=manifest_path,
        data_path=parquet,
        years=(year,),
        vintage_id=f"FGJ-{year}-test",
        publication_status="staged-candidate",
    )
    return BundleContext(
        bundle_id=f"annual-{year}-test",
        manifest_sha256="0" * 64,
        baseline_year=year - 1,
        comparison_year=year,
        cohort=("LAX",),
        sources={"ontime": ref},
        evidence_path=evidence,
        evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest(),
    )


def row(month=1, *, origin="LAX", year=2024, carrier="AA", cancel=0, divert=0,
        delay=0, taxi=10, flights=1, causes=(None,) * 5):
    return (year, month, origin, "LAX" if origin != "LAX" else "SFO", carrier,
            cancel, divert, flights, delay, taxi, -15, *causes)


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


def test_means_do_not_depend_on_scan_order():
    from functools import reduce
    from operator import add

    from app.calculations import operations

    values = [1e16, 1.0, 1.0, 0.1, 0.2]
    forward = operations._mean([(v,) for v in values], 0, None)
    backward = operations._mean([(v,) for v in reversed(values)], 0, None)
    # Left-to-right float addition differs by order (3.12's sum() compensates, 3.11's does not).
    assert reduce(add, values) != reduce(add, reversed(values))
    assert forward == backward
    assert operations._mean([], 0, None).numerator == 0


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


def test_bundle_uses_selected_year_and_exact_referenced_snapshot(tmp_path):
    parquet = publish(tmp_path, complete(year=2025), year=2025, origins=["LAX"])
    bundle = bundle_for(tmp_path, parquet)

    result = calculate_operations("LAX", tmp_path / "missing-legacy", bundle=bundle)

    assert result.year == 2025
    assert result.source.period == "2025"
    assert result.source.snapshot_id == bundle.sources["ontime"].snapshot_id
    assert result.scheduled_count == 12


@pytest.mark.parametrize("year", [2024, 2026, True])
def test_bundle_rejects_year_outside_selected_comparison(tmp_path, year):
    parquet = publish(tmp_path, complete(year=2025), year=2025, origins=["LAX"])
    bundle = bundle_for(tmp_path, parquet)

    with pytest.raises(OperationsCalculationError, match="year"):
        calculate_operations("LAX", tmp_path, year=year, bundle=bundle)


def test_bundle_never_falls_back_to_missing_or_corrupt_legacy_pointer(tmp_path):
    parquet = publish(tmp_path, complete(year=2025), year=2025, origins=["LAX"])
    bundle = bundle_for(tmp_path, parquet)
    (tmp_path / "current.json").write_text("not json")

    assert calculate_operations("LAX", tmp_path, bundle=bundle).year == 2025
    (tmp_path / "current.json").unlink()
    assert calculate_operations("LAX", tmp_path, bundle=bundle).scheduled_count == 12


def test_bundle_rejects_corrupted_referenced_data(tmp_path):
    parquet = publish(tmp_path, complete(year=2025), year=2025, origins=["LAX"])
    bundle = bundle_for(tmp_path, parquet)
    with parquet.open("ab") as handle:
        handle.write(b"changed")

    with pytest.raises(OperationsCalculationError, match="bundle on-time"):
        calculate_operations("LAX", bundle=bundle)


def test_historical_bundle_matches_legacy_result(tmp_path):
    parquet = publish(tmp_path, complete() + [row(delay=24, taxi=22, carrier="UA")])
    expected = calculate_operations("LAX", tmp_path)
    historical = bundle_for(tmp_path, parquet, year=2024)

    assert calculate_operations("LAX", tmp_path, bundle=historical) == expected


def test_real_packaged_2025_candidate_uses_bundle_ref() -> None:
    data_root = Path(__file__).resolve().parents[1] / "data"
    bundle = load_bundle("annual-2025-r1", data_root=data_root)

    result = calculate_operations("ANC", bundle=bundle)

    assert result.year == 2025
    assert result.source.snapshot_id == bundle.sources["ontime"].snapshot_id
    assert result.observed_months == tuple(range(1, 13))
    assert result.missing_months == ()
    assert result.scheduled_count > 0
    assert result.departure_delay_minutes.denominator <= result.departure_delay_minutes.eligible_count


def test_delay_cause_mix_is_hand_calculated_from_attributed_minutes(tmp_path):
    # Cause minutes (carrier, weather, NAS, security, late aircraft) on two late flights;
    # rows without causes, and a cancelled row that carries some, do not count.
    rows = complete() + [
        row(delay=40, causes=(10.0, 0.0, 20.0, 0.0, 10.0)),
        row(delay=30, causes=(5.0, 5.0, 0.0, 0.0, 30.0)),
        row(cancel=1, causes=(500.0, 0.0, 0.0, 0.0, 0.0)),
    ]
    publish(tmp_path, rows)
    mix = calculate_operations("LAX", tmp_path).delay_causes
    assert mix.status == "ok" and mix.reason is None
    assert mix.delayed_flights == 2
    assert mix.total_minutes == 80
    assert [field for field, _minutes, _share in mix.causes] == [
        "carrier_delay", "weather_delay", "nas_delay", "security_delay", "late_aircraft_delay"]
    assert [minutes for _field, minutes, _share in mix.causes] == [15, 5, 20, 0, 40]
    assert [share for _field, _minutes, share in mix.causes] == [18.75, 6.25, 25.0, 0.0, 50.0]


@pytest.mark.parametrize("causes, reason", [
    ((None,) * 5, "insufficient data: no attributed delay minutes"),
    ((-1.0, 0.0, 0.0, 0.0, 0.0), "insufficient data: invalid delay-cause field"),
    ((float("nan"), 0.0, 0.0, 0.0, 0.0), "insufficient data: invalid delay-cause field"),
])
def test_delay_cause_mix_without_valid_minutes_is_unavailable(tmp_path, causes, reason):
    publish(tmp_path, complete() + [row(delay=20, causes=causes)])
    mix = calculate_operations("LAX", tmp_path).delay_causes
    assert (mix.status, mix.causes, mix.reason) == ("unavailable", (), reason)


def test_delay_cause_mix_is_unavailable_for_incomplete_coverage(tmp_path):
    rows = [row(month, causes=(1.0, 0.0, 0.0, 0.0, 1.0)) for month in range(1, 12)]
    publish(tmp_path, rows)
    mix = calculate_operations("LAX", tmp_path).delay_causes
    assert mix.status == "unavailable"
    assert mix.reason == "insufficient data: incomplete annual origin coverage"
