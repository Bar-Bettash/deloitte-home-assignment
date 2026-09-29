import json
from datetime import datetime, timedelta, timezone

import pytest
from app.sources.bundle import BundleContext, SnapshotRef
from app.sources.source_check import SourceCheckError, validate_source_check

DIGEST = "a" * 64
NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def _bundle(tmp_path) -> BundleContext:
    sources = {
        name: SnapshotRef(name, f"{name}-1", DIGEST, DIGEST, tmp_path / f"{name}.json", tmp_path / f"{name}.parquet", (2024, 2025), "v1", "candidate")
        for name in ("datasf", "t100", "ontime", "faa", "aip")
    }
    return BundleContext("recent-2025", DIGEST, 2024, 2025, ("BOS",), sources, tmp_path / "evidence.json", DIGEST)


def _receipt(bundle: BundleContext, names=("t100",), checked_at=NOW) -> dict:
    return {
        "schema_version": 1, "receipt_type": "application_source_check", "admission_status": "admitted",
        "bundle_id": bundle.bundle_id, "manifest_sha256": bundle.manifest_sha256,
        "checked_at_utc": checked_at.isoformat().replace("+00:00", "Z"),
        "sources": [{
            "source": ref.source, "snapshot_id": ref.snapshot_id,
            "manifest_sha256": ref.manifest_sha256, "content_sha256": ref.content_sha256,
            "years": list(ref.years), "outcome": "current_for_scope",
            "newer_complete_available": False, "newer_partial_year": None,
            "selected_period_revised": False,
        } for ref in bundle.sources.values() if ref.source in names],
    }


def _write(path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_accepts_bundle_bound_sfo_receipt_and_discloses_new_partial(tmp_path) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    receipt = _receipt(bundle, ("datasf", "t100", "ontime", "faa", "aip"))
    receipt["sources"][1]["newer_partial_year"] = 2026
    _write(path, receipt)
    results = {workflow: validate_source_check(bundle, workflow, path, NOW) for workflow in ("screen", "operations", "long_haul", "sfo")}
    assert results["sfo"].sources == ("aip", "datasf", "ontime", "t100")
    assert results["operations"].newer_partial_years == ()
    assert results["long_haul"].newer_partial_years == (("t100", 2026),)
    with pytest.raises(AttributeError):
        results["sfo"].workflow = "operations"


def test_rejects_stale_or_future_receipt(tmp_path) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    for checked_at in (NOW - timedelta(days=7, seconds=1), NOW + timedelta(seconds=1)):
        _write(path, _receipt(bundle, checked_at=checked_at))
        with pytest.raises(SourceCheckError, match="future-dated or stale"):
            validate_source_check(bundle, "long_haul", path, NOW)
    _write(path, _receipt(bundle))
    with pytest.raises(SourceCheckError, match="clock is invalid"):
        validate_source_check(bundle, "long_haul", path, NOW.replace(tzinfo=None))


@pytest.mark.parametrize("field,value", [
    ("manifest_sha256", "b" * 64),
    ("content_sha256", "b" * 64),
    ("years", [2023, 2024]),
])
def test_rejects_mismatched_source_identity(tmp_path, field: str, value: object) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    receipt = _receipt(bundle)
    receipt["sources"][0][field] = value
    _write(path, receipt)
    with pytest.raises(SourceCheckError, match="source identity"):
        validate_source_check(bundle, "long_haul", path, NOW)


def test_rejects_evidence_only_receipt(tmp_path) -> None:
    path = tmp_path / "check.json"
    _write(path, {"receipt_type": "official_source_release_check", "scope": {"admission_status": "evidence_only_not_admitted"}})
    with pytest.raises(SourceCheckError, match="evidence only"):
        validate_source_check(_bundle(tmp_path), "long_haul", path, NOW)


def test_malformed_json_and_unexpected_keys_raise_typed_error(tmp_path) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    path.write_bytes(b"{not-json")
    with pytest.raises(SourceCheckError, match="unavailable or invalid"):
        validate_source_check(bundle, "long_haul", path, NOW)
    receipt = _receipt(bundle)
    receipt["unexpected"] = True
    _write(path, receipt)
    with pytest.raises(SourceCheckError, match="invalid keys"):
        validate_source_check(bundle, "long_haul", path, NOW)
    receipt = _receipt(bundle)
    receipt["schema_version"] = True
    _write(path, receipt)
    with pytest.raises(SourceCheckError, match="not an admitted"):
        validate_source_check(bundle, "long_haul", path, NOW)


@pytest.mark.parametrize("sources", ["bad", [{"source": []}], [{"source": "t100"}]])
def test_malformed_source_shapes_raise_typed_error(tmp_path, sources: object) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    receipt = _receipt(bundle)
    receipt["sources"] = sources
    _write(path, receipt)
    with pytest.raises(SourceCheckError, match="source list is invalid"):
        validate_source_check(bundle, "long_haul", path, NOW)


@pytest.mark.parametrize("checked_at", [None, "2026-09-27T12:00:00+00:00", "not-a-timeZ"])
def test_rejects_invalid_timestamp(tmp_path, checked_at: object) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    receipt = _receipt(bundle)
    receipt["checked_at_utc"] = checked_at
    _write(path, receipt)
    with pytest.raises(SourceCheckError, match="timestamp is invalid"):
        validate_source_check(bundle, "long_haul", path, NOW)


@pytest.mark.parametrize("field", ["bundle_id", "manifest_sha256"])
def test_rejects_bundle_identity_mismatch(tmp_path, field: str) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    receipt = _receipt(bundle)
    receipt[field] = "forged"
    _write(path, receipt)
    with pytest.raises(SourceCheckError, match="bundle identity"):
        validate_source_check(bundle, "long_haul", path, NOW)


def test_rejects_absent_duplicate_or_unknown_receipt_source(tmp_path) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    duplicate = _receipt(bundle)
    duplicate["sources"].append(dict(duplicate["sources"][0]))
    unknown = _receipt(bundle)
    unknown["sources"][0]["source"] = "unknown"
    for receipt in (_receipt(bundle, ()), duplicate, unknown):
        _write(path, receipt)
        with pytest.raises(SourceCheckError, match="sources do not match"):
            validate_source_check(bundle, "long_haul", path, NOW)


@pytest.mark.parametrize("field,value", [
    ("newer_complete_available", True),
    ("selected_period_revised", True),
    ("outcome", "check_failed"),
])
def test_rejects_blocking_outcome(tmp_path, field: str, value: object) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    receipt = _receipt(bundle)
    receipt["sources"][0][field] = value
    _write(path, receipt)
    with pytest.raises(SourceCheckError, match="outcome blocks"):
        validate_source_check(bundle, "long_haul", path, NOW)


@pytest.mark.parametrize("partial", [True, "2026", 2025])
def test_rejects_invalid_partial_metadata(tmp_path, partial: object) -> None:
    bundle, path = _bundle(tmp_path), tmp_path / "check.json"
    receipt = _receipt(bundle)
    receipt["sources"][0]["newer_partial_year"] = partial
    _write(path, receipt)
    with pytest.raises(SourceCheckError, match="partial period is invalid"):
        validate_source_check(bundle, "long_haul", path, NOW)
