from __future__ import annotations

import ast
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from app.sources.bundle import load_bundle
from scripts import reconcile_recent

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="module")
def bundle():
    return load_bundle("annual-2025-r1", data_root=DATA_ROOT)


@pytest.fixture(scope="module")
def expected(bundle):
    return reconcile_recent.build_expected(bundle)


def test_expected_generator_has_no_application_calculator_imports() -> None:
    tree = ast.parse(Path(reconcile_recent.__file__).read_text(encoding="utf-8"))
    top_level_imports = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert not any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.calculations")
        for node in top_level_imports
    )
    generator = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "build_expected")
    imports = [node for node in ast.walk(generator) if isinstance(node, (ast.Import, ast.ImportFrom))]

    assert imports == []


def test_real_bundle_controls_cover_every_workflow_and_full_grain(expected) -> None:
    assert expected["identity"]["bundle_manifest_sha256"] == (
        "f951dd50d254c3376c6b2f739fa79d711dbaa14e1fc6d21834d836012aad1b4d"
    )
    assert expected["independence"]["expected_values_import_application_calculators"] is False
    assert len(expected["traffic"]["airports"]) == 27
    assert len(expected["traffic"]["airports"]["ANC"]["monthly"]["2025"]) == 12
    assert expected["long_haul"]["known_long_haul_performed_departures"] == 999
    assert expected["long_haul"]["total_performed_departures"] == 36_040
    assert len(expected["operations"]["airports"]) == 4
    assert all(len(item["monthly"]) == 12 for item in expected["operations"]["airports"].values())
    assert len(expected["sfo"]["datasf"]["cells"]) == 48
    assert expected["sfo"]["datasf"]["annual_enplaned"] == {
        "2024": 26_054_586, "2025": 27_250_806,
    }
    ranks = {row["airport"]: row["rank"] for row in expected["screen"]["rows"]}
    assert ranks["HVN"] == 1
    assert (ranks["BGR"], ranks["PWM"]) == (2, 2)
    assert expected["screen"]["exclusions"] == {
        "PVC": "incomplete eligible coverage: 2024,2025"
    }


def test_preserved_raw_reference_is_hash_bound_and_scope_aware(tmp_path, bundle, expected) -> None:
    raw_path = tmp_path / "reference.json"
    raw_path.write_text(json.dumps(_raw_reference_from(expected)), encoding="utf-8")

    checked = reconcile_recent.build_expected(bundle, raw_reference_path=raw_path)

    assert checked["identity"]["raw_reference_sha256"] == reconcile_recent._sha256(raw_path)
    assert checked["independence"]["raw_source_reference_checked"] is True


@pytest.mark.parametrize("scope", ["datasf", "t100", "ontime"])
def test_preserved_raw_reference_rejects_truncated_scope(
    tmp_path, bundle, expected, scope
) -> None:
    raw = _raw_reference_from(expected)
    if scope == "datasf":
        raw["datasf"]["monthly_enplaned"].pop("202501")
    elif scope == "t100":
        raw["t100"]["new_england"]["airports"].pop("EWB")
    else:
        raw["ontime"]["airports"].pop("SNA")
    path = tmp_path / f"{scope}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(reconcile_recent.ReconciliationError, match="exact"):
        reconcile_recent.build_expected(bundle, raw_reference_path=path)


def test_historical_reference_reproduces_frozen_controls() -> None:
    context = reconcile_recent.load_historical_context(DATA_ROOT)
    historical = reconcile_recent.build_expected(context)

    assert historical["period"] == {"baseline_year": 2023, "comparison_year": 2024}
    assert historical["long_haul"]["known_long_haul_performed_departures"] == 950
    assert historical["long_haul"]["total_performed_departures"] == 40_017
    assert historical["sfo"]["datasf"]["annual_enplaned"] == {
        "2023": 24_992_086, "2024": 26_054_586,
    }
    assert set(historical["operations"]["airports"]) == {"LAX", "SFO", "SNA"}


def test_changed_application_arithmetic_fails_reconciliation(monkeypatch, bundle, expected) -> None:
    actual = reconcile_recent._expected_application_controls(expected)
    changed = deepcopy(actual)
    changed["operations"]["airports"]["LAX"]["scheduled_count"] += 1
    monkeypatch.setattr(
        reconcile_recent, "collect_application_controls", lambda *_args, **_kwargs: changed
    )

    report = reconcile_recent.reconcile(bundle, expected)

    assert report["status"] == "fail"
    original = actual["operations"]["airports"]["LAX"]["scheduled_count"]
    assert report["mismatches"] == [
        f"$.operations.airports.LAX.scheduled_count: {original} != {original + 1}"
    ]


def test_real_application_reconciles_recent_and_historical(bundle, expected) -> None:
    recent = reconcile_recent.reconcile(bundle, expected)
    historical_context = reconcile_recent.load_historical_context(DATA_ROOT)
    historical = reconcile_recent.reconcile(
        historical_context,
        reconcile_recent.build_expected(historical_context),
        bundled=False,
    )

    assert recent == {
        "status": "pass", "bundle_id": "annual-2025-r1",
        "bundle_manifest_sha256": bundle.manifest_sha256, "mismatches": [],
    }
    assert historical["status"] == "pass"
    assert historical["mismatches"] == []


def test_plan_cli_writes_numeric_reference_and_reconciliation(tmp_path, expected) -> None:
    raw_path = tmp_path / "raw-reference.json"
    output_path = tmp_path / "reference.json"
    raw_path.write_text(json.dumps(_raw_reference_from(expected)), encoding="utf-8")

    exit_code = reconcile_recent.main([
        "--bundle", "annual-2025-r1", "--data-root", str(DATA_ROOT),
        "--raw-reference", str(raw_path), "--output", str(output_path),
    ])
    result = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert result["long_haul"]["total_performed_departures"] == 36_040
    assert result["reconciliation"]["status"] == "pass"


def test_full_cli_rejects_omitted_raw_reference(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        reconcile_recent.main([
            "--bundle", "annual-2025-r1", "--data-root", str(DATA_ROOT),
            "--output", str(tmp_path / "must-not-exist.json"),
        ])

    assert raised.value.code == 1
    assert "requires --raw-reference" in capsys.readouterr().err
    assert not (tmp_path / "must-not-exist.json").exists()


def test_real_screen_output_mutation_is_detected(monkeypatch, bundle, expected) -> None:
    from app.calculations import screen

    original = screen.calculate_screen

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        first = replace(result.rows[0], passengers=result.rows[0].passengers + 1)
        return replace(result, rows=(first, *result.rows[1:]))

    monkeypatch.setattr(screen, "calculate_screen", changed)

    report = reconcile_recent.reconcile(bundle, expected)

    assert report["status"] == "fail"
    assert any(".screen.rows[0].passengers" in item for item in report["mismatches"])


def test_real_sfo_growth_gap_mutation_is_detected(monkeypatch, bundle, expected) -> None:
    from app.calculations import sfo

    original = sfo.calculate_sfo_pressure

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        metric = replace(result.growth_gap_pp, value=result.growth_gap_pp.value + 1.0)
        return replace(result, growth_gap_pp=metric)

    monkeypatch.setattr(sfo, "calculate_sfo_pressure", changed)

    report = reconcile_recent.reconcile(bundle, expected)

    assert report["status"] == "fail"
    assert any("growth_gap" in item for item in report["mismatches"])


def test_numeric_comparison_uses_tolerance_and_reports_paths() -> None:
    assert reconcile_recent.compare_values({"value": 1.0}, {"value": 1.0 + 1e-9}) == []
    assert reconcile_recent.compare_values({"value": 1.0}, {"value": 1.1}) == [
        "$.value: 1.0 != 1.1"
    ]


def _raw_reference_from(expected: dict) -> dict:
    datasf = {
        period: {"total_enplaned_passengers": value}
        for period, value in expected["sfo"]["datasf"]["monthly_enplaned"].items()
    }
    airports = {
        airport: {
            year: {key: item["annual"][year][key] for key in ("passengers", "seats")}
            for year in ("2024", "2025")
        }
        for airport, item in expected["traffic"]["airports"].items()
        if airport not in {"ANC", "LAX", "SFO", "SNA"}
    }
    operations = {
        airport: {
            "all_valid_rows_denominator": item["scheduled_count"],
            "cancelled_rows": item["cancelled"], "diverted_rows": item["diverted"],
            "dep_delay_minutes": {
                "non_null_denominator": item["departure_delay_minutes"]["denominator"]
            },
            "taxi_out_minutes": {"non_null_denominator": item["taxi_out_minutes"]["denominator"]},
        }
        for airport, item in expected["operations"]["airports"].items()
        if airport in {"LAX", "SFO", "SNA"}
    }
    long_haul = expected["long_haul"]
    return {
        "datasf": {"monthly_enplaned": datasf},
        "t100": {"new_england": {"airports": airports}, "anc_long_haul": {"2025": {"3000": {
            "known_long_haul_performed_departures": str(
                long_haul["known_long_haul_performed_departures"]
            ),
            "total_performed_departures": str(long_haul["total_performed_departures"]),
        }}}},
        "ontime": {"airports": operations},
    }
