from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pytest
from app.calculations.long_haul import calculate_long_haul_share
from app.calculations.traffic import DEFAULT_DATA_ROOT, TrafficCalculationError
from app.sources.bundle import BundleContext, SnapshotRef, load_bundle


def test_weights_by_performed_departures_instead_of_averaging_route_shares(tmp_path: Path) -> None:
    rows = _complete_rows()
    rows.extend([(2024, 1, 999, 9, 1_000.0), (2024, 1, 1, 1, 3_000.0)])
    _publish_snapshot(tmp_path, rows)

    result = calculate_long_haul_share("ANC", 2024, 3_000, tmp_path)

    assert result.status == "ok"
    assert result.total_departures == 10
    assert result.long_haul_departures == 1
    assert result.share_percent == 10.0


def test_threshold_boundary_is_included_and_scheduled_departures_are_ignored(tmp_path: Path) -> None:
    rows = _complete_rows()
    rows.append((2024, 1, 100_000, 7, 2_500.0))
    _publish_snapshot(tmp_path, rows)

    result = calculate_long_haul_share("ANC", 2024, 2_500, tmp_path)

    assert result.total_departures == 7
    assert result.long_haul_departures == 7
    assert result.share_percent == 100.0


def test_missing_month_is_insufficient_data(tmp_path: Path) -> None:
    rows = [row for row in _complete_rows() if row[1] != 12]
    rows.append((2024, 1, 1, 1, 4_000.0))
    _publish_snapshot(tmp_path, rows)

    result = calculate_long_haul_share("ANC", 2024, 3_000, tmp_path)

    assert result.status == "insufficient_data"
    assert result.coverage.missing_months == (12,)
    assert result.share_percent is None


def test_missing_distance_produces_performed_departure_bounds(tmp_path: Path) -> None:
    rows = _complete_rows()
    rows.extend([(2024, 1, 0, 0, None), (2024, 2, 1, 2, None)])
    _publish_snapshot(tmp_path, rows)

    result = calculate_long_haul_share("ANC", 2024, 3_000, tmp_path)

    assert result.status == "ok"
    assert result.total_departures == 2
    assert result.unknown_distance_departures == 2
    assert result.share_percent is None
    assert (result.lower_percent, result.upper_percent) == (0.0, 100.0)


def test_zero_total_is_unavailable_not_zero_percent(tmp_path: Path) -> None:
    _publish_snapshot(tmp_path, _complete_rows())

    result = calculate_long_haul_share("ANC", 2024, 3_000, tmp_path)

    assert result.status == "unavailable"
    assert result.total_departures == 0
    assert result.share_percent is None
    assert result.unknown_distance_departures == 0
    assert result.lower_percent is None
    assert result.upper_percent is None


@pytest.mark.parametrize(
    ("performed", "distance"),
    [(-1, 3_000.0), (1.5, 3_000.0), (float("nan"), 3_000.0), (1, -1.0), (1, float("inf"))],
)
def test_invalid_counts_or_distances_are_insufficient_data(
    tmp_path: Path, performed: float, distance: float
) -> None:
    rows = _complete_rows()
    rows.append((2024, 1, 1, performed, distance))
    _publish_snapshot(tmp_path, rows)

    result = calculate_long_haul_share("ANC", 2024, 3_000, tmp_path)

    assert result.status == "insufficient_data"
    assert result.share_percent is None


def test_actual_accepted_snapshot_calculates_anc_bos_and_rejects_incomplete_pvc() -> None:
    if not (DEFAULT_DATA_ROOT / "current.json").is_file():
        pytest.skip("accepted local T-100 snapshot is not installed")

    anc = calculate_long_haul_share("ANC", 2024, 3_000)
    bos = calculate_long_haul_share("BOS", 2024, 3_000)
    pvc = calculate_long_haul_share("PVC", 2024, 3_000)

    assert (anc.long_haul_departures, anc.total_departures) == (950, 40_017)
    assert anc.share_percent == pytest.approx(2.373991, abs=1e-6)
    assert anc.unknown_distance_departures == 0
    assert anc.lower_percent == anc.upper_percent == anc.share_percent
    assert (bos.long_haul_departures, bos.total_departures) == (12_330, 187_549)
    assert bos.share_percent == pytest.approx(6.574282, abs=1e-6)
    assert pvc.status == "insufficient_data"
    assert pvc.coverage.missing_months == (12,)


def _complete_rows() -> list[tuple[object, ...]]:
    return [(2024, month, 0, 0, 1_000.0) for month in range(1, 13)]


def _publish_snapshot(
    data_root: Path, rows: list[tuple[object, ...]], *, write_pointer: bool = True,
) -> None:
    snapshot_id = "t100-test-snapshot"
    snapshot_dir = data_root / "snapshots" / snapshot_id
    snapshot_dir.mkdir(parents=True)
    parquet_path = snapshot_dir / "data.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            "CREATE TABLE source(year INTEGER, month INTEGER, departures_scheduled DOUBLE, "
            "departures_performed DOUBLE, distance DOUBLE, origin VARCHAR)"
        )
        connection.executemany("INSERT INTO source VALUES (?, ?, ?, ?, ?, 'ANC')", rows)
        escaped = str(parquet_path).replace("'", "''")
        connection.execute(f"COPY source TO '{escaped}' (FORMAT PARQUET)")
    finally:
        connection.close()
    manifest = {
        "snapshot_id": snapshot_id,
        "source": {"name": "BTS T-100 Segment All Carriers", "table": "FMG", "url": "https://example.test"},
        "imported_at_utc": "2026-09-26T21:03:39Z",
        "population": {"origins": ["ANC"]},
        "eligible_rows": {
            str(year): sum(1 for row in rows if row[0] == year)
            for year in sorted({row[0] for row in rows})
        },
        "parquet_file": "data.parquet",
        "parquet_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        "validation_status": "accepted",
    }
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if write_pointer:
        (data_root / "current.json").write_text(
            json.dumps(
                {"snapshot_id": snapshot_id, "manifest": f"snapshots/{snapshot_id}/manifest.json"}
            ),
            encoding="utf-8",
        )


def _bundle_for_snapshot(data_root: Path) -> BundleContext:
    snapshot_dir = data_root / "snapshots/t100-test-snapshot"
    manifest_path = snapshot_dir / "manifest.json"
    data_path = snapshot_dir / "data.parquet"
    return BundleContext(
        bundle_id="test-bundle",
        manifest_sha256="0" * 64,
        baseline_year=2024,
        comparison_year=2025,
        cohort=("ANC",),
        sources={
            "t100": SnapshotRef(
                source="t100",
                snapshot_id="t100-test-snapshot",
                manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                content_sha256=hashlib.sha256(data_path.read_bytes()).hexdigest(),
                manifest_path=manifest_path,
                data_path=data_path,
                years=(2024, 2025),
                vintage_id="test-vintage",
                publication_status="staged-candidate",
            )
        },
        evidence_path=data_root / "evidence.json",
        evidence_sha256="0" * 64,
    )


def test_bundle_selects_recent_year_and_ignores_corrupt_legacy_pointer(tmp_path: Path) -> None:
    rows = [(year, month, 0, 0, 1_000.0) for year in (2024, 2025) for month in range(1, 13)]
    rows.extend([(2025, 1, 999, 7, 3_000.0), (2025, 2, 999, 3, None)])
    _publish_snapshot(tmp_path, rows, write_pointer=False)
    bundle = _bundle_for_snapshot(tmp_path)
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    (legacy_root / "current.json").write_text("{corrupt", encoding="utf-8")

    result = calculate_long_haul_share(
        "ANC", 2025, 3_000, legacy_root, bundle=bundle
    )

    assert result.source.period == "2024-2025"
    assert (result.long_haul_departures, result.total_departures) == (7, 10)
    assert result.unknown_distance_departures == 3
    assert result.share_percent is None
    assert (result.lower_percent, result.upper_percent) == (70.0, 100.0)
    with pytest.raises(TrafficCalculationError, match="2024 or 2025"):
        calculate_long_haul_share("ANC", 2023, 3_000, bundle=bundle)


def test_packaged_recent_anc_counts_and_historical_parity() -> None:
    data_root = DEFAULT_DATA_ROOT.parents[1]
    if not (data_root / "bundles/annual-2025-r1/manifest.json").is_file():
        pytest.skip("packaged recent bundle candidate is not installed")
    bundle = load_bundle("annual-2025-r1", data_root=data_root)

    recent = calculate_long_haul_share("ANC", 2025, 3_000, bundle=bundle)
    historical = calculate_long_haul_share("ANC", 2024, 3_000)

    assert (recent.long_haul_departures, recent.total_departures) == (999, 36_040)
    assert recent.unknown_distance_departures == 0
    assert recent.lower_percent == recent.upper_percent == recent.share_percent
    assert recent.share_percent == pytest.approx(2.771920, abs=1e-6)
    assert (historical.long_haul_departures, historical.total_departures) == (950, 40_017)
    assert historical.share_percent == pytest.approx(2.373991, abs=1e-6)
