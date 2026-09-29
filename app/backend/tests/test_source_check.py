"""Strict source-receipt validation and explicit offline-adapter tests."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest
from app.sources.bundle import BundleContext, SnapshotRef
from app.sources.source_check import (
    SourceCheckError,
    validate_source_check_file,
    validate_source_check_payload,
)

NOW = datetime(2026, 9, 27, 18, tzinfo=timezone.utc)
SOURCE_ORDER = ("datasf", "t100", "ontime", "faa", "aip")


def _bundle(tmp_path) -> BundleContext:
    sources = {}
    for index, name in enumerate(SOURCE_ORDER):
        years = (2024, 2025) if name in {"datasf", "t100"} else (2025,)
        sources[name] = SnapshotRef(
            name,
            f"{name}-snapshot",
            f"{index + 1:x}" * 64,
            f"{index + 6:x}" * 64,
            tmp_path / f"{name}.json",
            tmp_path / f"{name}.bin",
            years,
            "v1",
            "staged-candidate",
        )
    return BundleContext(
        "annual-2025-r1",
        "f" * 64,
        2024,
        2025,
        ("HVN", "BGR", "PWM"),
        sources,
        tmp_path / "evidence.json",
        "e" * 64,
    )


def _receipt(bundle: BundleContext, *, checked_at: datetime = NOW) -> dict:
    return {
        "schema_version": 1,
        "receipt_type": "application_source_check",
        "admission_status": "admitted",
        "bundle_id": bundle.bundle_id,
        "manifest_sha256": bundle.manifest_sha256,
        "checked_at_utc": checked_at.isoformat().replace("+00:00", "Z"),
        "sources": [
            {
                "source": name,
                "snapshot_id": ref.snapshot_id,
                "manifest_sha256": ref.manifest_sha256,
                "content_sha256": ref.content_sha256,
                "years": list(ref.years),
                "outcome": "current_for_scope",
                "newer_complete_available": False,
                "newer_partial_year": None,
                "selected_period_revised": False,
            }
            for name, ref in bundle.sources.items()
        ],
    }


def test_db_payload_and_offline_file_adapter_are_equivalent(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    payload = _receipt(bundle)
    payload["sources"][0]["newer_partial_year"] = 2026
    payload["sources"][2]["newer_partial_year"] = 2026
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    direct = validate_source_check_payload(bundle, payload, now=NOW)
    offline = validate_source_check_file(bundle, path, now=NOW)

    assert direct == offline
    assert direct.sources == SOURCE_ORDER
    assert direct.newer_partial_years == (("datasf", 2026), ("ontime", 2026))
    with pytest.raises(FrozenInstanceError):
        direct.bundle_id = "changed"


def test_pure_payload_validator_never_reads_files(tmp_path, monkeypatch) -> None:
    bundle, payload = _bundle(tmp_path), None
    payload = _receipt(bundle)

    def unexpected_read(*_args, **_kwargs):
        pytest.fail("pure DB-payload validation must not touch the filesystem")

    monkeypatch.setattr("pathlib.Path.read_bytes", unexpected_read)
    assert validate_source_check_payload(bundle, payload, now=NOW).bundle_id == bundle.bundle_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bundle_id", "other"),
        ("manifest_sha256", "0" * 64),
    ],
)
def test_bundle_identity_mismatch_is_rejected(tmp_path, field, value) -> None:
    bundle, payload = _bundle(tmp_path), None
    payload = _receipt(bundle)
    payload[field] = value

    with pytest.raises(SourceCheckError, match="bundle identity"):
        validate_source_check_payload(bundle, payload, now=NOW)


@pytest.mark.parametrize("field", ["snapshot_id", "manifest_sha256", "content_sha256", "years"])
def test_source_identity_mismatch_is_rejected(tmp_path, field) -> None:
    bundle = _bundle(tmp_path)
    payload = _receipt(bundle)
    payload["sources"][0][field] = [2023, 2024] if field == "years" else "0" * 64

    with pytest.raises(SourceCheckError, match="source identity"):
        validate_source_check_payload(bundle, payload, now=NOW)


def test_source_set_order_and_record_keys_are_exact(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    reordered = _receipt(bundle)
    reordered["sources"].reverse()
    missing = _receipt(bundle)
    missing["sources"].pop()
    extra_key = _receipt(bundle)
    extra_key["sources"][0]["proof"] = "http-200"
    malformed_name = _receipt(bundle)
    malformed_name["sources"][0]["source"] = []

    for payload in (reordered, missing, malformed_name):
        with pytest.raises(SourceCheckError, match="sources do not match"):
            validate_source_check_payload(bundle, payload, now=NOW)
    with pytest.raises(SourceCheckError, match="record has invalid keys"):
        validate_source_check_payload(bundle, extra_key, now=NOW)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("receipt_type", "official_source_release_check"),
        ("admission_status", "blocked"),
    ],
)
def test_only_admitted_application_receipt_is_accepted(tmp_path, field, value) -> None:
    bundle = _bundle(tmp_path)
    payload = _receipt(bundle)
    payload[field] = value

    with pytest.raises(SourceCheckError, match="not admitted"):
        validate_source_check_payload(bundle, payload, now=NOW)


@pytest.mark.parametrize(
    ("outcome", "complete", "revised"),
    [
        ("inconclusive", False, False),
        ("selected_period_revised", False, True),
        ("newer_complete_available", True, False),
        ("current_for_scope", 0, False),
        ("current_for_scope", False, 0),
    ],
)
def test_inconclusive_revision_or_new_complete_period_blocks(
    tmp_path, outcome, complete, revised
) -> None:
    bundle = _bundle(tmp_path)
    payload = _receipt(bundle)
    payload["sources"][1].update({
        "outcome": outcome,
        "newer_complete_available": complete,
        "selected_period_revised": revised,
    })

    with pytest.raises(SourceCheckError, match="outcome blocks"):
        validate_source_check_payload(bundle, payload, now=NOW)


@pytest.mark.parametrize("partial", [True, "2026", 2025, 2101])
def test_invalid_newer_partial_period_is_rejected(tmp_path, partial) -> None:
    bundle = _bundle(tmp_path)
    payload = _receipt(bundle)
    payload["sources"][1]["newer_partial_year"] = partial

    with pytest.raises(SourceCheckError, match="partial period"):
        validate_source_check_payload(bundle, payload, now=NOW)


def test_freshness_includes_exact_seven_day_boundary(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    boundary = _receipt(bundle, checked_at=NOW - timedelta(days=7))
    assert validate_source_check_payload(bundle, boundary, now=NOW).checked_at_utc == (
        NOW - timedelta(days=7)
    )

    for checked_at in (NOW - timedelta(days=7, microseconds=1), NOW + timedelta(microseconds=1)):
        with pytest.raises(SourceCheckError, match="future-dated or stale"):
            validate_source_check_payload(bundle, _receipt(bundle, checked_at=checked_at), now=NOW)


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        "not-a-timeZ",
        "2026-09-27Z",
        "2026-09-27T18:00:00+00:00",
        "2026-09-27T18:00:00+02:00Z",
    ],
)
def test_timestamp_must_be_valid_utc_z(tmp_path, timestamp) -> None:
    bundle = _bundle(tmp_path)
    payload = _receipt(bundle)
    payload["checked_at_utc"] = timestamp

    with pytest.raises(SourceCheckError, match="timestamp"):
        validate_source_check_payload(bundle, payload, now=NOW)


def test_invalid_clock_and_malformed_payloads_fail_closed(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    with pytest.raises(SourceCheckError, match="clock"):
        validate_source_check_payload(bundle, _receipt(bundle), now=NOW.replace(tzinfo=None))
    for payload in (None, "{}", [], {"schema_version": 1}):
        with pytest.raises(SourceCheckError, match="invalid keys"):
            validate_source_check_payload(bundle, payload, now=NOW)


def test_offline_adapter_rejects_missing_corrupt_or_non_object_file(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    path = tmp_path / "receipt.json"
    with pytest.raises(SourceCheckError, match="unavailable or invalid"):
        validate_source_check_file(bundle, path, now=NOW)
    path.write_bytes(b"{not-json")
    with pytest.raises(SourceCheckError, match="unavailable or invalid"):
        validate_source_check_file(bundle, path, now=NOW)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(SourceCheckError, match="invalid keys"):
        validate_source_check_file(bundle, path, now=NOW)
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(SourceCheckError, match="duplicate keys"):
        validate_source_check_file(bundle, path, now=NOW)


def test_bundle_must_have_exact_supported_source_scope(tmp_path) -> None:
    bundle = _bundle(tmp_path)
    shortened = replace(bundle, sources=dict(list(bundle.sources.items())[:-1]))

    with pytest.raises(SourceCheckError, match="scope is unsupported"):
        validate_source_check_payload(shortened, _receipt(bundle), now=NOW)
