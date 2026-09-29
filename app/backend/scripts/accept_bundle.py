"""Atomically promote one fully admitted immutable data bundle."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from app.evidence import EvidenceIntegrityError, load_evidence
from app.sources.bundle import BundleContext, BundleError, load_bundle
from app.sources.source_check import SourceCheckError, validate_source_check_file

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
_REGISTRY_KEYS = {"schema_version", "default_bundle_id", "bundles"}
_ENTRY_KEYS = {"bundle_id", "manifest_sha256"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AcceptanceError(RuntimeError):
    """Promotion inputs or the existing accepted registry are invalid."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise AcceptanceError("accepted registry contains duplicate keys")
        value[key] = item
    return value


def _read_registry(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AcceptanceError("accepted registry is unavailable") from exc
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AcceptanceError("accepted registry is invalid JSON") from exc
    if type(value) is not dict or set(value) != _REGISTRY_KEYS:
        raise AcceptanceError("accepted registry has invalid keys")
    entries = value["bundles"]
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or not isinstance(entries, list)
        or not entries
    ):
        raise AcceptanceError("accepted registry is incompatible")
    seen: set[str] = set()
    for entry in entries:
        if type(entry) is not dict or set(entry) != _ENTRY_KEYS:
            raise AcceptanceError("accepted registry entry has invalid keys")
        bundle_id, checksum = entry["bundle_id"], entry["manifest_sha256"]
        if (
            not isinstance(bundle_id, str)
            or _ID.fullmatch(bundle_id) is None
            or not isinstance(checksum, str)
            or _SHA256.fullmatch(checksum) is None
            or bundle_id in seen
        ):
            raise AcceptanceError("accepted registry entry is invalid")
        seen.add(bundle_id)
    if not isinstance(value["default_bundle_id"], str) or value["default_bundle_id"] not in seen:
        raise AcceptanceError("accepted registry default is invalid")
    return value


def _updated_registry(
    current: Mapping[str, object] | None, bundle: BundleContext
) -> dict[str, object] | None:
    if current is None:
        return {
            "schema_version": 1,
            "default_bundle_id": bundle.bundle_id,
            "bundles": [{
                "bundle_id": bundle.bundle_id,
                "manifest_sha256": bundle.manifest_sha256,
            }],
        }
    entries = current["bundles"]
    assert isinstance(entries, list)
    existing = next((item for item in entries if item["bundle_id"] == bundle.bundle_id), None)
    if existing is not None and existing["manifest_sha256"] != bundle.manifest_sha256:
        raise AcceptanceError("accepted bundle ID cannot be rebound to different bytes")
    if existing is not None and current["default_bundle_id"] == bundle.bundle_id:
        return None
    updated = [dict(item) for item in entries]
    if existing is None:
        updated.append({
            "bundle_id": bundle.bundle_id,
            "manifest_sha256": bundle.manifest_sha256,
        })
    return {
        "schema_version": 1,
        "default_bundle_id": bundle.bundle_id,
        "bundles": updated,
    }


def _write_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


ReceiptValidator = Callable[[BundleContext, Path], object]
EvidenceValidator = Callable[[BundleContext], object]


def _validate_evidence(bundle: BundleContext) -> object:
    return load_evidence(bundle=bundle)


def _validate_freshness(bundle: BundleContext, path: Path) -> object:
    return validate_source_check_file(bundle, path)


def _validate_reconciliation(bundle: BundleContext, path: Path) -> object:
    from scripts.reconcile_recent import (
        ReconciliationError,
        validate_reconciliation_file,
    )

    try:
        return validate_reconciliation_file(
            bundle, path, root=Path(__file__).resolve().parents[1]
        )
    except ReconciliationError as exc:
        raise AcceptanceError("reconciliation receipt is not valid for promotion") from exc


def promote_bundle(
    bundle_id: str,
    freshness_path: Path,
    reconciliation_path: Path,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    evidence_validator: EvidenceValidator = _validate_evidence,
    freshness_validator: ReceiptValidator = _validate_freshness,
    reconciliation_validator: ReceiptValidator = _validate_reconciliation,
) -> bool:
    """Validate both independent gates, then update the sole accepted registry."""
    bundle = load_bundle(bundle_id, data_root=data_root)
    evidence_validator(bundle)
    freshness_validator(bundle, Path(freshness_path))
    reconciliation_validator(bundle, Path(reconciliation_path))
    registry_path = Path(data_root) / "bundles" / "accepted.json"
    current = _read_registry(registry_path)
    updated = _updated_registry(current, bundle)
    if updated is None:
        return False
    _write_atomic(registry_path, updated)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Accept a fully admitted data bundle")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--freshness", required=True, type=Path)
    parser.add_argument("--reconciliation", required=True, type=Path)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        promote_bundle(
            args.bundle,
            args.freshness,
            args.reconciliation,
            data_root=args.data_root,
        )
    except (
        AcceptanceError,
        BundleError,
        EvidenceIntegrityError,
        SourceCheckError,
        OSError,
        ValueError,
    ) as exc:
        parser.exit(1, f"bundle acceptance failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
