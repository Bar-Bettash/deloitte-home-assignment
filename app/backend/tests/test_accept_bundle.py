from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import accept_bundle


def _bundle(bundle_id: str = "annual-2025-r1", checksum: str = "a" * 64):
    return SimpleNamespace(bundle_id=bundle_id, manifest_sha256=checksum)


def _registry(*, default: str = "historical-2024-r1") -> dict[str, object]:
    return {
        "schema_version": 1,
        "default_bundle_id": default,
        "bundles": [
            {"bundle_id": "historical-2024-r1", "manifest_sha256": "b" * 64},
            {"bundle_id": "annual-2025-r1", "manifest_sha256": "a" * 64},
        ],
    }


def test_registry_update_preserves_verified_unknown_entries_and_selects_bundle() -> None:
    current = _registry()

    updated = accept_bundle._updated_registry(current, _bundle())

    assert updated == {**current, "default_bundle_id": "annual-2025-r1"}
    assert updated["bundles"][0] == current["bundles"][0]


def test_registry_update_is_idempotent_for_same_default_identity() -> None:
    assert accept_bundle._updated_registry(
        _registry(default="annual-2025-r1"), _bundle()
    ) is None


def test_registry_update_never_rebinds_existing_id() -> None:
    with pytest.raises(accept_bundle.AcceptanceError, match="rebound"):
        accept_bundle._updated_registry(_registry(), _bundle(checksum="c" * 64))


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "default_bundle_id": "missing", "bundles": []},
        {**_registry(), "extra": True},
        {
            **_registry(),
            "bundles": [
                {"bundle_id": "duplicate", "manifest_sha256": "a" * 64},
                {"bundle_id": "duplicate", "manifest_sha256": "b" * 64},
            ],
        },
        {
            **_registry(),
            "bundles": [{"bundle_id": "../escape", "manifest_sha256": "a" * 64}],
            "default_bundle_id": "../escape",
        },
    ],
)
def test_existing_registry_must_be_fully_valid_before_update(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(accept_bundle.AcceptanceError):
        accept_bundle._read_registry(path)

    assert path.read_bytes() == before


def test_registry_reader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "accepted.json"
    path.write_text(
        '{"schema_version":1,"schema_version":1,"default_bundle_id":"x",'
        '"bundles":[{"bundle_id":"x","manifest_sha256":"' + "a" * 64 + '"}]}',
        encoding="utf-8",
    )

    with pytest.raises(accept_bundle.AcceptanceError, match="duplicate"):
        accept_bundle._read_registry(path)


def _write_registry(root: Path, payload: dict[str, object] | None = None) -> Path:
    path = root / "bundles" / "accepted.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload or _registry()) + "\n", encoding="utf-8")
    return path


def _validator(calls: list[str], name: str, error: Exception | None = None):
    def validate(bundle, path):
        calls.append(name)
        if error is not None:
            raise error
        return object()

    return validate


def test_promotion_requires_both_receipts_before_registry_write(
    tmp_path: Path, monkeypatch
) -> None:
    registry = _write_registry(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(accept_bundle, "load_bundle", lambda bundle_id, data_root: _bundle())

    changed = accept_bundle.promote_bundle(
        "annual-2025-r1", Path("fresh.json"), Path("reconcile.json"),
        data_root=tmp_path,
        evidence_validator=lambda bundle: calls.append("evidence"),
        freshness_validator=_validator(calls, "freshness"),
        reconciliation_validator=_validator(calls, "reconciliation"),
    )

    assert changed is True
    assert calls == ["evidence", "freshness", "reconciliation"]
    assert json.loads(registry.read_text())["default_bundle_id"] == "annual-2025-r1"


@pytest.mark.parametrize("failed_gate", ["bundle", "freshness", "reconciliation"])
def test_every_prewrite_failure_preserves_registry_bytes(
    tmp_path: Path, monkeypatch, failed_gate: str
) -> None:
    registry = _write_registry(tmp_path)
    before = registry.read_bytes()
    if failed_gate == "bundle":
        monkeypatch.setattr(
            accept_bundle, "load_bundle",
            lambda bundle_id, data_root: (_ for _ in ()).throw(
                accept_bundle.BundleError("injected")
            ),
        )
    else:
        monkeypatch.setattr(accept_bundle, "load_bundle", lambda bundle_id, data_root: _bundle())
    calls: list[str] = []
    freshness_error = RuntimeError("injected") if failed_gate == "freshness" else None
    reconciliation_error = RuntimeError("injected") if failed_gate == "reconciliation" else None

    with pytest.raises(RuntimeError):
        accept_bundle.promote_bundle(
            "annual-2025-r1", Path("fresh.json"), Path("reconcile.json"),
            data_root=tmp_path,
            evidence_validator=lambda bundle: object(),
            freshness_validator=_validator(calls, "freshness", freshness_error),
            reconciliation_validator=_validator(calls, "reconciliation", reconciliation_error),
        )

    assert registry.read_bytes() == before


def test_atomic_replace_failure_preserves_registry_bytes(tmp_path: Path, monkeypatch) -> None:
    registry = _write_registry(tmp_path)
    before = registry.read_bytes()
    monkeypatch.setattr(accept_bundle, "load_bundle", lambda bundle_id, data_root: _bundle())
    monkeypatch.setattr(
        accept_bundle.os, "replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("injected")),
    )

    with pytest.raises(OSError, match="injected"):
        accept_bundle.promote_bundle(
            "annual-2025-r1", Path("fresh.json"), Path("reconcile.json"),
            data_root=tmp_path,
            evidence_validator=lambda bundle: object(),
            freshness_validator=lambda bundle, path: object(),
            reconciliation_validator=lambda bundle, path: object(),
        )

    assert registry.read_bytes() == before
    assert list(registry.parent.glob(".accepted.json.*")) == []


def test_atomic_fsync_failure_preserves_registry_bytes(tmp_path: Path, monkeypatch) -> None:
    registry = _write_registry(tmp_path)
    before = registry.read_bytes()
    monkeypatch.setattr(accept_bundle, "load_bundle", lambda bundle_id, data_root: _bundle())
    monkeypatch.setattr(
        accept_bundle.os, "fsync",
        lambda descriptor: (_ for _ in ()).throw(OSError("injected fsync")),
    )

    with pytest.raises(OSError, match="injected fsync"):
        accept_bundle.promote_bundle(
            "annual-2025-r1", Path("fresh.json"), Path("reconcile.json"),
            data_root=tmp_path,
            evidence_validator=lambda bundle: object(),
            freshness_validator=lambda bundle, path: object(),
            reconciliation_validator=lambda bundle, path: object(),
        )

    assert registry.read_bytes() == before
    assert list(registry.parent.glob(".accepted.json.*")) == []


def test_idempotent_acceptance_performs_no_write(tmp_path: Path, monkeypatch) -> None:
    registry = _write_registry(tmp_path, _registry(default="annual-2025-r1"))
    before = registry.read_bytes()
    monkeypatch.setattr(accept_bundle, "load_bundle", lambda bundle_id, data_root: _bundle())
    monkeypatch.setattr(
        accept_bundle, "_write_atomic",
        lambda path, payload: (_ for _ in ()).throw(AssertionError("must not write")),
    )

    changed = accept_bundle.promote_bundle(
        "annual-2025-r1", Path("fresh.json"), Path("reconcile.json"),
        data_root=tmp_path,
        evidence_validator=lambda bundle: object(),
        freshness_validator=lambda bundle, path: object(),
        reconciliation_validator=lambda bundle, path: object(),
    )

    assert changed is False
    assert registry.read_bytes() == before


def test_default_gate_adapters_are_both_called(tmp_path: Path, monkeypatch) -> None:
    from scripts import reconcile_recent

    calls: list[str] = []
    monkeypatch.setattr(accept_bundle, "load_bundle", lambda bundle_id, data_root: _bundle())
    monkeypatch.setattr(
        accept_bundle, "load_evidence", lambda bundle: calls.append("evidence")
    )
    monkeypatch.setattr(
        accept_bundle, "validate_source_check_file",
        lambda bundle, path: calls.append("freshness"),
    )
    monkeypatch.setattr(
        reconcile_recent, "validate_reconciliation_file",
        lambda bundle, path, root: calls.append("reconciliation"),
    )

    accept_bundle.promote_bundle(
        "annual-2025-r1", Path("fresh.json"), Path("reconcile.json"),
        data_root=tmp_path,
    )

    assert calls == ["evidence", "freshness", "reconciliation"]


@pytest.mark.parametrize("reason", ["malformed", "wrong scope", "invalid AIP context"])
def test_evidence_failure_prevents_promotion_and_preserves_registry(
    tmp_path: Path, monkeypatch, reason: str
) -> None:
    registry = _write_registry(tmp_path)
    before = registry.read_bytes()
    monkeypatch.setattr(accept_bundle, "load_bundle", lambda bundle_id, data_root: _bundle())

    with pytest.raises(accept_bundle.EvidenceIntegrityError, match=reason):
        accept_bundle.promote_bundle(
            "annual-2025-r1", Path("fresh.json"), Path("reconcile.json"),
            data_root=tmp_path,
            evidence_validator=lambda bundle: (_ for _ in ()).throw(
                accept_bundle.EvidenceIntegrityError(reason)
            ),
            freshness_validator=lambda bundle, path: object(),
            reconciliation_validator=lambda bundle, path: object(),
        )

    assert registry.read_bytes() == before
