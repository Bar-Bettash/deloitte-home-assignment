from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.sources.bundle import load_bundle
from scripts import reconcile_recent

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = BACKEND_ROOT / "docs/evidence/recent-arithmetic-reference.json"


@pytest.fixture(scope="module")
def bundle():
    return load_bundle("annual-2025-r1", data_root=DATA_ROOT)


@pytest.fixture(scope="module")
def expected(bundle):
    return reconcile_recent.build_expected(bundle)


@pytest.fixture(scope="module")
def preserved_reference():
    return json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def final_receipt(bundle, preserved_reference):
    return reconcile_recent.build_final_receipt(
        bundle,
        preserved_reference,
        reference_path=REFERENCE_PATH,
        checked_at=datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
    )


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
        "84b35d0deee52004071b958f4d32b64625a29f5fdffed374b9eece73a67d2548"
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


def test_final_receipt_binds_scope_code_reference_and_workflows(
    bundle, final_receipt
) -> None:
    assert set(final_receipt) == reconcile_recent.FINAL_RECEIPT_KEYS
    assert final_receipt["status"] == "pass"
    assert final_receipt["bundle_manifest_sha256"] == bundle.manifest_sha256
    assert final_receipt["period"] == {"baseline_year": 2024, "comparison_year": 2025}
    assert final_receipt["cohort"] == list(bundle.cohort)
    assert final_receipt["sources"] == reconcile_recent._source_identity(bundle)
    assert final_receipt["reference"]["path"] == (
        "docs/evidence/recent-arithmetic-reference.json"
    )
    assert final_receipt["reference"]["sha256"] == reconcile_recent._sha256(REFERENCE_PATH)
    assert set(final_receipt["code_sha256"]) == set(reconcile_recent.CODE_PATHS)
    assert final_receipt["workflows"] == {
        "screen": {"status": "pass"},
        "operations": {"status": "pass", "comparison_status": "pass"},
        "long_haul": {"status": "pass"},
        "sfo": {"status": "pass"},
    }


def test_final_receipt_validator_recomputes_bound_artifacts(
    tmp_path, bundle, final_receipt
) -> None:
    receipt_path = tmp_path / "recent-reconciliation.json"
    receipt_path.write_text(json.dumps(final_receipt), encoding="utf-8")

    validated = reconcile_recent.validate_reconciliation_file(bundle, receipt_path)

    assert validated == final_receipt


def test_final_cli_uses_preserved_reference_without_raw_input(tmp_path) -> None:
    output_path = tmp_path / "recent-reconciliation.json"

    exit_code = reconcile_recent.main([
        "--bundle", "annual-2025-r1",
        "--data-root", str(DATA_ROOT),
        "--reference", str(REFERENCE_PATH),
        "--output", str(output_path),
    ])
    result = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert result["receipt_type"] == "application_reconciliation"
    assert result["status"] == "pass"


def test_evidence_only_manifest_revision_is_allowed(bundle, preserved_reference) -> None:
    revised = replace(bundle, manifest_sha256="a" * 64)

    receipt = reconcile_recent.build_final_receipt(
        revised,
        preserved_reference,
        reference_path=REFERENCE_PATH,
        checked_at=datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
    )

    assert receipt["bundle_manifest_sha256"] == "a" * 64


@pytest.mark.parametrize("changed", ["source", "period", "cohort"])
def test_final_reference_rejects_changed_bundle_scope(
    bundle, preserved_reference, changed
) -> None:
    if changed == "source":
        t100 = replace(bundle.sources["t100"], content_sha256="b" * 64)
        revised = replace(bundle, sources={**bundle.sources, "t100": t100})
    elif changed == "period":
        revised = replace(bundle, baseline_year=2023)
    else:
        revised = replace(bundle, cohort=(*bundle.cohort[:-1], "ZZZ"))

    with pytest.raises(reconcile_recent.ReconciliationError, match="incompatible"):
        reconcile_recent.validate_reference(revised, preserved_reference)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_reference_sha256", None),
        ("raw_reference_sha256", "not-a-sha256"),
        ("raw_source_reference_checked", False),
    ],
)
def test_final_reference_requires_preliminary_raw_source_proof(
    bundle, preserved_reference, field, value
) -> None:
    changed = deepcopy(preserved_reference)
    if field == "raw_source_reference_checked":
        changed["independence"][field] = value
    else:
        changed["identity"][field] = value

    with pytest.raises(reconcile_recent.ReconciliationError, match="incompatible"):
        reconcile_recent.validate_reference(bundle, changed)


def test_final_validator_rejects_reference_or_code_hash_drift(
    monkeypatch, bundle, preserved_reference, final_receipt
) -> None:
    changed_reference = deepcopy(final_receipt)
    changed_reference["reference"]["sha256"] = "c" * 64
    with pytest.raises(reconcile_recent.ReconciliationError, match="receipt mismatch"):
        reconcile_recent.validate_reconciliation_payload(
            bundle, changed_reference, preserved_reference
        )

    original = reconcile_recent._code_hashes

    def changed_hashes(root):
        hashes = original(root)
        hashes["app/dispatch.py"] = "d" * 64
        return hashes

    monkeypatch.setattr(reconcile_recent, "_code_hashes", changed_hashes)
    with pytest.raises(reconcile_recent.ReconciliationError, match="receipt mismatch"):
        reconcile_recent.validate_reconciliation_payload(
            bundle, final_receipt, preserved_reference
        )


def test_final_file_validator_rejects_duplicate_keys(tmp_path, bundle) -> None:
    receipt_path = tmp_path / "duplicate.json"
    receipt_path.write_text('{"status":"pass","status":"pass"}', encoding="utf-8")

    with pytest.raises(reconcile_recent.ReconciliationError, match="duplicate JSON key"):
        reconcile_recent.validate_reconciliation_file(bundle, receipt_path)


def test_final_cli_never_overwrites_preliminary_reference(bundle, capsys) -> None:
    original_hash = reconcile_recent._sha256(REFERENCE_PATH)

    with pytest.raises(SystemExit) as raised:
        reconcile_recent.main([
            "--bundle", bundle.bundle_id,
            "--data-root", str(DATA_ROOT),
            "--reference", str(REFERENCE_PATH),
            "--output", str(REFERENCE_PATH),
        ])

    assert raised.value.code == 1
    assert "must not overwrite" in capsys.readouterr().err
    assert reconcile_recent._sha256(REFERENCE_PATH) == original_hash


@pytest.mark.parametrize("defect", ["malformed", "wrong_scope", "aip_context"])
def test_final_receipt_rejects_invalid_bundled_evidence(
    tmp_path, bundle, preserved_reference, defect
) -> None:
    evidence_path = tmp_path / "evidence.json"
    payload = json.loads(bundle.evidence_path.read_text(encoding="utf-8"))
    if defect == "malformed":
        evidence_path.write_text("{", encoding="utf-8")
    else:
        if defect == "wrong_scope":
            payload["scope"]["bundle_id"] = "other-bundle"
        else:
            payload["aip_funding_context"]["new_england_award_count"] -= 1
        evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    invalid_bundle = replace(
        bundle,
        evidence_path=evidence_path,
        evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
    )

    with pytest.raises(reconcile_recent.ReconciliationError, match="evidence"):
        reconcile_recent.build_final_receipt(
            invalid_bundle,
            preserved_reference,
            reference_path=REFERENCE_PATH,
            checked_at=datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
        )


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



@pytest.mark.parametrize("mode", [["--expected-only"], ["--reference", "unused.json"]])
def test_cli_unknown_bundle_fails_with_one_line(tmp_path, capsys, mode) -> None:
    with pytest.raises(SystemExit) as raised:
        reconcile_recent.main([
            "--bundle", "no-such-bundle", "--data-root", str(tmp_path), *mode,
            "--output", str(tmp_path / "must-not-exist.json"),
        ])

    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("recent reconciliation failed:") and err.count("\n") == 1
    assert not (tmp_path / "must-not-exist.json").exists()
