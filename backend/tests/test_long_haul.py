from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import pytest
from app.calculations.long_haul import calculate_long_haul_share
from app.calculations.traffic import DEFAULT_DATA_ROOT


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


def test_missing_distance_only_invalidates_rows_with_positive_performed_departures(tmp_path: Path) -> None:
    rows = _complete_rows()
    rows.extend([(2024, 1, 0, 0, None), (2024, 2, 1, 2, None)])
    _publish_snapshot(tmp_path, rows)

    result = calculate_long_haul_share("ANC", 2024, 3_000, tmp_path)

    assert result.status == "insufficient_data"
    assert "distance" in (result.reason or "")


def test_zero_total_is_unavailable_not_zero_percent(tmp_path: Path) -> None:
    _publish_snapshot(tmp_path, _complete_rows())

    result = calculate_long_haul_share("ANC", 2024, 3_000, tmp_path)

    assert result.status == "unavailable"
    assert result.total_departures == 0
    assert result.share_percent is None


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
    assert (bos.long_haul_departures, bos.total_departures) == (12_330, 187_549)
    assert bos.share_percent == pytest.approx(6.574282, abs=1e-6)
    assert pvc.status == "insufficient_data"
    assert pvc.coverage.missing_months == (12,)


def _complete_rows() -> list[tuple[object, ...]]:
    return [(2024, month, 0, 0, 1_000.0) for month in range(1, 13)]


def _publish_snapshot(data_root: Path, rows: list[tuple[object, ...]]) -> None:
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
        "parquet_file": "data.parquet",
        "parquet_sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        "validation_status": "accepted",
    }
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (data_root / "current.json").write_text(
        json.dumps({"snapshot_id": snapshot_id, "manifest": f"snapshots/{snapshot_id}/manifest.json"}),
        encoding="utf-8",
    )
