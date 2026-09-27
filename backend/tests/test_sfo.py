from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from app.calculations import sfo
from app.calculations.sfo import (
    SFOTrendError,
    SFOTrendResult,
    calculate_sfo_enplaned_trend,
)
from app.sources.bundle import BundleContext, SnapshotRef, load_bundle


def test_combines_domestic_and_international_into_one_24_month_series(tmp_path: Path) -> None:
    rows = _complete_rows()
    _publish_snapshot(tmp_path, rows)

    result = calculate_sfo_enplaned_trend(tmp_path)

    assert isinstance(result, SFOTrendResult)
    assert len(result.series) == 24
    assert [(point.period, point.passengers) for point in result.series[:2]] == [
        ("202301", 150),
        ("202302", 150),
    ]
    assert [(point.period, point.passengers) for point in result.series[-2:]] == [
        ("202411", 180),
        ("202412", 180),
    ]
    assert [(total.year, total.passengers) for total in result.annual_totals] == [
        (2023, 1_800),
        (2024, 2_160),
    ]
    assert result.growth.status == "ok"
    assert result.growth.percent == 20.0
    assert result.growth.reason is None
    assert result.source.snapshot_id == "datasf-test-snapshot"
    assert result.source.dataset_id == "rkru-6vcg"
    assert result.source.retrieved_at == datetime(2026, 9, 26, 12, 30, tzinfo=timezone.utc)


def test_zero_2023_baseline_returns_unavailable_growth(tmp_path: Path) -> None:
    rows = _complete_rows(baseline_domestic=0, baseline_international=0)
    _publish_snapshot(tmp_path, rows)

    result = calculate_sfo_enplaned_trend(tmp_path)

    assert result.annual_totals[0].passengers == 0
    assert result.annual_totals[1].passengers == 2_160
    assert result.growth.status == "unavailable"
    assert result.growth.percent is None
    assert result.growth.reason == "2023 enplaned passenger total is zero; growth is undefined"


def test_excludes_non_enplaned_activity_from_series_and_annual_totals(tmp_path: Path) -> None:
    rows = _complete_rows()
    rows.extend(
        (f"{year}{month:02d}", "Domestic", "Deplaned", 1_000_000)
        for year in (2023, 2024)
        for month in range(1, 13)
    )
    _publish_snapshot(tmp_path, rows)

    result = calculate_sfo_enplaned_trend(tmp_path)

    assert result.series[0].passengers == 150
    assert result.annual_totals[0].passengers == 1_800
    assert result.annual_totals[1].passengers == 2_160


def test_rejects_snapshot_missing_one_of_48_enplaned_cells(tmp_path: Path) -> None:
    rows = _complete_rows()
    rows.remove(("202412", "International", "Enplaned", 60))
    _publish_snapshot(tmp_path, rows)

    with pytest.raises(SFOTrendError, match="48.*month/geography"):
        calculate_sfo_enplaned_trend(tmp_path)


def test_rejects_monthly_totals_that_do_not_reconcile_to_source_annual_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish_snapshot(tmp_path, _complete_rows())
    monkeypatch.setattr(sfo, "_query_annual_source_totals", lambda _path: {2023: 1_801, 2024: 2_160})

    with pytest.raises(SFOTrendError, match="annual source totals"):
        calculate_sfo_enplaned_trend(tmp_path)


def test_rejects_snapshot_not_marked_accepted(tmp_path: Path) -> None:
    _publish_snapshot(tmp_path, _complete_rows(), validation_status="rejected")

    with pytest.raises(SFOTrendError, match="accepted"):
        calculate_sfo_enplaned_trend(tmp_path)


def test_pressure_bundle_uses_matched_t100_formula_and_preserves_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.calculations.sfo as sfo_module

    trend = SimpleNamespace(
        source=SimpleNamespace(
            snapshot_id="datasf-accepted-1", name="DataSF passengers", url="https://data.sf.gov/ds", period="2023-2024"
        )
    )
    traffic = _traffic_fixture()
    operations = SimpleNamespace(
        source=SimpleNamespace(
            snapshot_id="ontime-accepted-1", name="BTS on-time", url="https://transtats.bts.gov/ontime", period="2024"
        )
    )
    monkeypatch.setattr(sfo_module, "calculate_sfo_enplaned_trend", lambda _root: trend)
    monkeypatch.setattr(sfo_module, "calculate_traffic", lambda _airport, _root: traffic)
    monkeypatch.setattr(sfo_module, "calculate_operations", lambda _airport, _root: operations)

    result = sfo_module.calculate_sfo_pressure(Path("datasf"), Path("t100"), Path("ontime"))

    assert result.status == "ok"
    assert result.growth_gap_pp.value == pytest.approx(5.0)
    assert result.growth_gap_pp.unit == "percentage_points"
    assert result.t100_traffic.result.annual[0].occupancy_percent.value == 50.0
    assert result.t100_traffic.result.annual[1].occupancy_percent.value == pytest.approx(52.38, abs=0.01)
    assert [source.source_id for source in result.lineage] == ["datasf", "t100", "bts_ontime"]
    assert "do not identify unmet demand" in result.limitation


def test_pressure_bundle_retains_independent_results_when_operations_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.calculations.sfo as sfo_module
    from app.calculations.operations import OperationsCalculationError

    trend = SimpleNamespace(
        source=SimpleNamespace(
            snapshot_id="datasf-accepted-1", name="DataSF passengers", url="https://data.sf.gov/ds", period="2023-2024"
        )
    )
    traffic = _traffic_fixture()
    monkeypatch.setattr(sfo_module, "calculate_sfo_enplaned_trend", lambda _root: trend)
    monkeypatch.setattr(sfo_module, "calculate_traffic", lambda _airport, _root: traffic)

    def unavailable_operations(_airport: str, _root: Path):
        raise OperationsCalculationError("test-only source failure")

    monkeypatch.setattr(sfo_module, "calculate_operations", unavailable_operations)

    result = sfo_module.calculate_sfo_pressure(Path("datasf"), Path("t100"), Path("ontime"))

    assert result.status == "partial"
    assert result.enplaned_trend.result is trend
    assert result.t100_traffic.result is traffic
    assert result.operations.status == "unavailable"
    assert result.operations.reason == "accepted CY2024 SFO on-time snapshot is unavailable"
    assert result.growth_gap_pp.status == "ok"
    assert [source.source_id for source in result.lineage] == ["datasf", "t100"]


def test_growth_gap_is_unavailable_for_incomplete_or_zero_baselines() -> None:
    import app.calculations.sfo as sfo_module

    incomplete = _traffic_fixture(complete_2024=False)
    zero_baseline = _traffic_fixture(passengers_2023=0)
    zero_seat_baseline = _traffic_fixture(seats_2023=0)

    assert sfo_module._growth_gap(incomplete).status == "unavailable"
    assert sfo_module._growth_gap(zero_baseline).status == "unavailable"
    assert sfo_module._growth_gap(zero_seat_baseline).status == "unavailable"


def test_bundle_uses_exact_recent_datasf_ref_without_legacy_pointer(tmp_path: Path) -> None:
    rows = _complete_rows(years=(2024, 2025))
    parquet = _publish_snapshot(tmp_path, rows, write_pointer=False)
    bundle = _bundle_for_datasf(tmp_path, parquet)
    (tmp_path / "current.json").write_text("corrupt")

    result = calculate_sfo_enplaned_trend(tmp_path, bundle=bundle)

    assert [(total.year, total.passengers) for total in result.annual_totals] == [
        (2024, 1_800),
        (2025, 2_160),
    ]
    assert len(result.series) == 24
    assert result.source.period == "2024-2025"
    assert (result.growth.baseline_year, result.growth.comparison_year) == (2024, 2025)


def test_bundle_requires_all_48_selected_enplaned_cells(tmp_path: Path) -> None:
    rows = _complete_rows(years=(2024, 2025))
    rows.remove(("202512", "International", "Enplaned", 60))
    parquet = _publish_snapshot(tmp_path, rows, write_pointer=False)
    bundle = _bundle_for_datasf(tmp_path, parquet)

    with pytest.raises(SFOTrendError, match="48.*month/geography"):
        calculate_sfo_enplaned_trend(bundle=bundle)


@pytest.mark.parametrize("artifact", ["manifest.json", "data.parquet"])
def test_bundle_rejects_corrupt_datasf_ref(tmp_path: Path, artifact: str) -> None:
    parquet = _publish_snapshot(
        tmp_path, _complete_rows(years=(2024, 2025)), write_pointer=False
    )
    bundle = _bundle_for_datasf(tmp_path, parquet)
    with (parquet.parent / artifact).open("ab") as handle:
        handle.write(b"changed")

    with pytest.raises(SFOTrendError, match="checksum"):
        calculate_sfo_enplaned_trend(bundle=bundle)


def test_historical_bundle_matches_legacy_enplaned_result(tmp_path: Path) -> None:
    parquet = _publish_snapshot(tmp_path, _complete_rows())
    expected = calculate_sfo_enplaned_trend(tmp_path)
    bundle = _bundle_for_datasf(tmp_path, parquet, years=(2023, 2024))

    assert calculate_sfo_enplaned_trend(tmp_path, bundle=bundle) == expected


def test_pressure_forwards_supplied_bundle_to_all_components(monkeypatch, tmp_path: Path) -> None:
    parquet = _publish_snapshot(
        tmp_path, _complete_rows(years=(2024, 2025)), write_pointer=False
    )
    bundle = _bundle_for_datasf(tmp_path, parquet)
    seen = []
    trend = SimpleNamespace(source=SimpleNamespace(
        snapshot_id="d", name="DataSF", url="https://example.test/d", period="2024-2025"
    ))
    traffic = _traffic_fixture(years=(2024, 2025))
    operations = SimpleNamespace(source=SimpleNamespace(
        snapshot_id="o", name="On-time", url="https://example.test/o", period="2025"
    ))

    def capture(result):
        def call(*_args, **kwargs):
            seen.append(kwargs["bundle"])
            return result
        return call

    monkeypatch.setattr(sfo, "calculate_sfo_enplaned_trend", capture(trend))
    monkeypatch.setattr(sfo, "calculate_traffic", capture(traffic))
    monkeypatch.setattr(sfo, "calculate_operations", capture(operations))

    result = sfo.calculate_sfo_pressure(bundle=bundle)

    assert result.status == "ok"
    assert seen == [bundle, bundle, bundle]


def test_real_packaged_recent_pressure_controls_and_separate_populations() -> None:
    data_root = Path(__file__).resolve().parents[1] / "data"
    bundle = load_bundle("annual-2025-r1", data_root=data_root)

    result = sfo.calculate_sfo_pressure(bundle=bundle)

    assert result.status == "ok"
    assert [(item.year, item.passengers) for item in result.enplaned_trend.result.annual_totals] == [
        (2024, 26_054_586),
        (2025, 27_250_806),
    ]
    traffic = result.t100_traffic.result
    assert [(item.year, item.passengers.value, item.seats.value) for item in traffic.annual] == [
        (2024, 25_288_609, 30_369_317),
        (2025, 26_477_602, 32_169_113),
    ]
    assert result.growth_gap_pp.value == pytest.approx(-1.2246691594)
    assert result.enplaned_trend.result.source.dataset_id == "rkru-6vcg"
    assert traffic.source.table == "FMG"
    assert "reporting-carrier scheduled departures" in result.operations.result.population
    assert [item.period for item in result.lineage] == ["2024-2025", "2024-2025", "2025"]


def _complete_rows(
    *,
    baseline_domestic: int = 100,
    baseline_international: int = 50,
    years: tuple[int, int] = (2023, 2024),
) -> list[tuple[str, str, str, int]]:
    rows: list[tuple[str, str, str, int]] = []
    for year in years:
        domestic = baseline_domestic if year == years[0] else 120
        international = baseline_international if year == years[0] else 60
        for month in range(1, 13):
            period = f"{year}{month:02d}"
            rows.append((period, "Domestic", "Enplaned", domestic))
            rows.append((period, "International", "Enplaned", international))
    return rows


def _traffic_fixture(
    *, complete_2024: bool = True, passengers_2023: int = 100, seats_2023: int = 200,
    years: tuple[int, int] = (2023, 2024),
):
    from app.calculations.traffic import (
        AnnualCoverage,
        AnnualTraffic,
        MetricResult,
        T100SnapshotSource,
        TrafficResult,
    )

    def annual(year: int, passengers: int, seats: int, complete: bool = True) -> AnnualTraffic:
        coverage = AnnualCoverage(year, tuple(range(1, 13)) if complete else tuple(range(1, 12)),
                                  () if complete else (12,), complete)
        return AnnualTraffic(
            year,
            coverage,
            MetricResult(passengers, "ok", None),
            MetricResult(seats, "ok", None),
            MetricResult(12, "ok", None),
            MetricResult(100.0 * passengers / seats if seats else None,
                         "ok" if seats else "unavailable", None if seats else "zero seats"),
        )

    source = T100SnapshotSource(
        "t100-accepted-1", "FMG", "BTS T-100 All Carriers", "https://transtats.bts.gov/t100", f"{years[0]}-{years[1]}",
        datetime(2026, 9, 26, tzinfo=timezone.utc),
    )
    base = annual(years[0], passengers_2023, seats_2023)
    current = annual(years[1], 110, 210, complete_2024)
    return TrafficResult("SFO", source, (base, current), SimpleNamespace())


def _publish_snapshot(
    data_root: Path,
    rows: list[tuple[str, str, str, int]],
    *,
    validation_status: str = "accepted",
    write_pointer: bool = True,
) -> Path:
    snapshot_id = "datasf-test-snapshot"
    snapshot_dir = data_root / "snapshots" / snapshot_id
    snapshot_dir.mkdir(parents=True)
    parquet_path = snapshot_dir / "data.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            "CREATE TABLE source(activity_period VARCHAR, geo_summary VARCHAR, "
            "activity_type_code VARCHAR, passenger_count BIGINT)"
        )
        connection.executemany("INSERT INTO source VALUES (?, ?, ?, ?)", rows)
        escaped_path = str(parquet_path).replace("'", "''")
        connection.execute(f"COPY source TO '{escaped_path}' (FORMAT PARQUET)")
    finally:
        connection.close()

    manifest = {
        "snapshot_id": snapshot_id,
        "source": {
            "name": "DataSF SFO passenger statistics",
            "dataset_id": "rkru-6vcg",
            "url": "https://data.sf.gov/resource/rkru-6vcg.csv",
        },
        "retrieved_at_utc": "2026-09-26T12:30:00Z",
        "content_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        "validation_status": validation_status,
        "parquet_file": "data.parquet",
        "scope": {
            "activity_period_start": min(row[0] for row in rows),
            "activity_period_end": max(row[0] for row in rows),
        },
    }
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if write_pointer:
        (data_root / "current.json").write_text(
            json.dumps(
                {
                    "snapshot_id": snapshot_id,
                    "manifest": f"snapshots/{snapshot_id}/manifest.json",
                }
            ),
            encoding="utf-8",
        )
    return parquet_path


def _bundle_for_datasf(
    data_root: Path,
    parquet_path: Path,
    *,
    years: tuple[int, int] = (2024, 2025),
) -> BundleContext:
    manifest_path = parquet_path.parent / "manifest.json"
    ref = SnapshotRef(
        source="datasf",
        snapshot_id="datasf-test-snapshot",
        manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        content_sha256=hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        manifest_path=manifest_path,
        data_path=parquet_path,
        years=years,
        vintage_id="test-vintage",
        publication_status="staged-candidate",
    )
    return BundleContext(
        bundle_id="test-bundle",
        manifest_sha256="0" * 64,
        baseline_year=years[0],
        comparison_year=years[1],
        cohort=("SFO",),
        sources={"datasf": ref},
        evidence_path=data_root / "evidence.json",
        evidence_sha256="0" * 64,
    )
