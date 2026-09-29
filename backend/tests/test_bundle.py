import hashlib
import json

import pytest
from app.sources.bundle import (
    BundleError,
    _load_candidate_bundle,
    load_bundle,
    promote_bundle,
    resolve_bundle,
)


def _write(path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _tree(root, *, ontime_year=2024):
    digest = lambda value: hashlib.sha256(value).hexdigest()
    cohort, refs, paths = ["BOS", "PVD"], [], {}
    evidence = b'{"reviewed":true}'
    evidence_path = root / "evidence.json"
    _write(evidence_path, evidence)
    manifests = {
        "datasf": ({"scope": {"activity_period_start": "202301", "activity_period_end": "202412"}}, "parquet_file", "content_sha256", (2023, 2024)),
        "t100": ({"eligible_rows": {"2023": 2, "2024": 2}}, "parquet_file", "parquet_sha256", (2023, 2024)),
        "ontime": ({"request": {"year": ontime_year}}, "parquet_file", "parquet_sha256", (2024,)),
        "faa": ({"cohort": [{"locid": airport, "cy2024_enplanements": 1} for airport in cohort]}, "raw_pdf", "content_sha256", (2024,)),
    }
    for name, (manifest, file_key, hash_key, years) in manifests.items():
        snapshot_id, content = f"{name}-1", f"{name}-content".encode()
        folder = root / "raw" / name / "snapshots" / snapshot_id
        data_path, manifest_path = folder / ("source.pdf" if name == "faa" else "data.parquet"), folder / "manifest.json"
        manifest.update({"snapshot_id": snapshot_id, "validation_status": "accepted", file_key: data_path.name, hash_key: digest(content)})
        manifest_raw = json.dumps(manifest, sort_keys=True).encode()
        _write(data_path, content)
        _write(manifest_path, manifest_raw)
        refs.append({"source": name, "snapshot_id": snapshot_id, "manifest_sha256": digest(manifest_raw),
                     "content_sha256": digest(content), "manifest_path": str(manifest_path.relative_to(root)),
                     "data_path": str(data_path.relative_to(root)), "years": list(years),
                     "vintage_id": "v1", "publication_status": "published"})
        paths[f"{name}_manifest"], paths[f"{name}_data"] = manifest_path, data_path
    bundle = {"schema_version": 1, "bundle_id": "historical-2024", "baseline_year": 2023,
              "comparison_year": 2024, "cohort": cohort, "sources": refs,
              "evidence_path": "evidence.json", "evidence_sha256": digest(evidence)}
    bundle_path = root / "bundles" / "historical-2024" / "manifest.json"
    _write(bundle_path, json.dumps(bundle).encode())
    paths.update(bundle=bundle_path, evidence=evidence_path)
    return paths


def _registry(root, bundles, default):
    path = root / "bundles" / "accepted.json"
    _write(path, json.dumps({"schema_version": 1, "default_bundle_id": default, "bundles": bundles}).encode())
    return path


def test_load_bundle_verifies_four_source_artifacts_and_evidence(tmp_path) -> None:
    paths = _tree(tmp_path)
    context = load_bundle("historical-2024", data_root=tmp_path)
    assert tuple(context.sources) == ("datasf", "t100", "ontime", "faa")
    assert context.cohort == ("BOS", "PVD")
    paths["evidence"].unlink()
    assert _load_candidate_bundle("historical-2024", data_root=tmp_path).bundle_id == "historical-2024"


@pytest.mark.parametrize(("kind", "error"), [
    ("data", "data checksum"), ("manifest", "manifest checksum"),
    ("evidence", "evidence checksum"), ("period", "on-time period"),
    ("keys", "invalid keys"), ("path", "escapes the data root"),
])
def test_load_bundle_rejects_tampering_and_incompatible_scope(tmp_path, kind, error) -> None:
    paths = _tree(tmp_path, ontime_year=2023 if kind == "period" else 2024)
    if kind in {"data", "manifest", "evidence"}:
        target = {"data": "t100_data", "manifest": "t100_manifest", "evidence": "evidence"}[kind]
        paths[target].write_bytes(paths[target].read_bytes() + b"tampered")
    elif kind in {"keys", "path"}:
        bundle = json.loads(paths["bundle"].read_bytes())
        bundle["unexpected" if kind == "keys" else "evidence_path"] = True if kind == "keys" else "../evidence.json"
        paths["bundle"].write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(BundleError, match=error):
        load_bundle("historical-2024", data_root=tmp_path)


def test_resolver_rejects_unregistered_and_forged_bundle_identity(tmp_path) -> None:
    paths = _tree(tmp_path)
    with pytest.raises(BundleError, match="registry is unavailable"):
        resolve_bundle(bundle_id="historical-2024", data_root=tmp_path)
    _registry(tmp_path, {"historical-2024": "b" * 64}, "historical-2024")
    with pytest.raises(BundleError, match="registry manifest checksum"):
        resolve_bundle(bundle_id="historical-2024", data_root=tmp_path)
    checksum = hashlib.sha256(paths["bundle"].read_bytes()).hexdigest()
    _registry(tmp_path, {"historical-2024": checksum}, "historical-2024")
    with pytest.raises(BundleError, match="not registered"):
        resolve_bundle(bundle_id="unknown", data_root=tmp_path)
    for invalid_year in (True, "2024", []):
        with pytest.raises(BundleError, match="requested year is unsupported"):
            resolve_bundle(year=invalid_year, bundle_id="historical-2024", data_root=tmp_path)
    assert resolve_bundle(year=2023, data_root=tmp_path).bundle_id == "historical-2024"
    assert resolve_bundle(bundle_id="historical-2024", year=2024, data_root=tmp_path).comparison_year == 2024


def test_promotion_is_atomic_and_preserves_registry_on_validation_failure(tmp_path) -> None:
    paths = _tree(tmp_path)
    registry_path = _registry(tmp_path, {"older": "a" * 64}, "older")
    context = promote_bundle("historical-2024", data_root=tmp_path)
    registry = json.loads(registry_path.read_bytes())
    assert registry["bundles"] == {"older": "a" * 64, "historical-2024": context.manifest_sha256}
    assert registry["default_bundle_id"] == "historical-2024"
    prior = registry_path.read_bytes()
    paths["t100_data"].write_bytes(b"tampered")
    with pytest.raises(BundleError, match="data checksum"):
        promote_bundle("historical-2024", data_root=tmp_path)
    assert registry_path.read_bytes() == prior


@pytest.mark.parametrize("malformed", [
    {"schema_version": 1, "default_bundle_id": "historical-2024", "bundles": {"historical-2024": []}},
    {"schema_version": 1, "default_bundle_id": [], "bundles": {"historical-2024": "a" * 64}},
])
def test_malformed_registry_values_raise_bundle_error(tmp_path, malformed) -> None:
    _tree(tmp_path)
    _write(tmp_path / "bundles" / "accepted.json", json.dumps(malformed).encode())
    with pytest.raises(BundleError, match="accepted bundle registry is invalid"):
        resolve_bundle(data_root=tmp_path)
