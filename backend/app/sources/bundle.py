from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUNDLE_KEYS = {"schema_version", "bundle_id", "baseline_year", "comparison_year", "cohort", "sources", "evidence_path", "evidence_sha256"}
_SOURCE_KEYS = {"source", "snapshot_id", "manifest_sha256", "content_sha256", "manifest_path", "data_path", "years", "vintage_id", "publication_status"}
_ARTIFACT_SCHEMA = {"datasf": ("parquet_file", "content_sha256"), "t100": ("parquet_file", "parquet_sha256"), "ontime": ("parquet_file", "parquet_sha256"), "faa": ("raw_pdf", "content_sha256"),
                    "aip": ("workbook_file", "content_sha256")}
_REGISTRY_KEYS = {"schema_version", "default_bundle_id", "bundles"}

class BundleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    source: str
    snapshot_id: str
    manifest_sha256: str
    content_sha256: str
    manifest_path: Path
    data_path: Path
    years: tuple[int, ...]
    vintage_id: str
    publication_status: str


@dataclass(frozen=True, slots=True)
class BundleContext:
    bundle_id: str
    manifest_sha256: str
    baseline_year: int
    comparison_year: int
    cohort: tuple[str, ...]
    sources: Mapping[str, SnapshotRef]
    evidence_path: Path
    evidence_sha256: str


def load_bundle(bundle_id: str, data_root: Path = DEFAULT_DATA_ROOT) -> BundleContext:
    return _load_bundle(bundle_id, data_root, require_evidence=True)


def resolve_bundle(year: int | None = None, bundle_id: str | None = None, *, data_root: Path = DEFAULT_DATA_ROOT) -> BundleContext:
    root, registry = Path(data_root).resolve(), _load_registry(Path(data_root).resolve())
    _ensure(year is None or type(year) is int, "requested year is unsupported")
    if bundle_id is not None:
        context = _registered_bundle(bundle_id, registry, root)
        _ensure(year is None or year in {context.baseline_year, context.comparison_year}, "requested year is incompatible with the bundle")
        return context
    if year is None:
        return _registered_bundle(registry["default_bundle_id"], registry, root)
    _ensure(type(year) is int and year in {2023, 2024, 2025}, "requested year is unsupported")
    target = 2024 if year in {2023, 2024} else 2025
    default = _registered_bundle(registry["default_bundle_id"], registry, root)
    if default.comparison_year == target:
        return default
    matches = [_registered_bundle(name, registry, root) for name in registry["bundles"] if name != default.bundle_id]
    matches = [context for context in matches if context.comparison_year == target]
    _ensure(len(matches) == 1, "accepted registry does not resolve the requested year uniquely")
    return matches[0]


def promote_bundle(bundle_id: str, data_root: Path = DEFAULT_DATA_ROOT) -> BundleContext:
    root = Path(data_root).resolve()
    context = load_bundle(bundle_id, root)
    _ensure(context.comparison_year != 2025 or "aip" in context.sources, "CY2025 promotion requires AIP context")
    path = root / "bundles" / "accepted.json"
    registry = _load_registry(root, missing_ok=True)
    existing = registry["bundles"].get(bundle_id)
    _ensure(existing in {None, context.manifest_sha256}, "accepted bundle ID cannot be overwritten")
    bundles = {**registry["bundles"], bundle_id: context.manifest_sha256}
    value = {"schema_version": 1, "default_bundle_id": bundle_id, "bundles": bundles}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".accepted-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return context


def _load_candidate_bundle(bundle_id: str, data_root: Path = DEFAULT_DATA_ROOT) -> BundleContext:
    return _load_bundle(bundle_id, data_root, require_evidence=False)


def _load_bundle(bundle_id: str, data_root: Path, *, require_evidence: bool,
                 expected_manifest_sha256: str | None = None) -> BundleContext:
    _ensure(isinstance(bundle_id, str) and bool(_ID.fullmatch(bundle_id)), "bundle ID is invalid")
    root = Path(data_root).resolve()
    path = (root / "bundles" / bundle_id / "manifest.json").resolve()
    _ensure(path.is_relative_to(root), "bundle manifest path escapes the data root")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundleError("bundle manifest is unavailable") from exc
    if expected_manifest_sha256 is not None:
        _ensure(hashlib.sha256(raw).hexdigest() == expected_manifest_sha256, "accepted registry manifest checksum does not match")
    context = parse_bundle_manifest(raw, bundle_id=bundle_id, data_root=root)
    _verify_artifacts(context, require_evidence=require_evidence)
    return context


def parse_bundle_manifest(raw: bytes, *, bundle_id: str, data_root: Path) -> BundleContext:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BundleError("bundle manifest is invalid JSON") from exc
    _ensure(isinstance(value, dict) and set(value) == _BUNDLE_KEYS, "bundle manifest has invalid keys")
    baseline, comparison = value["baseline_year"], value["comparison_year"]
    _ensure(type(value["schema_version"]) is int and value["schema_version"] == 1 and value["bundle_id"] == bundle_id, "bundle manifest identity is incompatible")
    _ensure(type(baseline) is int and type(comparison) is int and 1900 <= baseline < comparison <= 2100, "bundle periods are invalid")
    cohort = value["cohort"]
    _ensure(isinstance(cohort, list) and bool(cohort) and all(isinstance(item, str) and re.fullmatch(r"[A-Z]{3}", item) for item in cohort)
            and len(cohort) == len(set(cohort)), "bundle cohort is invalid")
    source_values = value["sources"]
    _ensure(isinstance(source_values, list) and bool(source_values), "bundle sources are invalid")
    refs = tuple(_parse_source(item, Path(data_root).resolve(), {baseline, comparison}) for item in source_values)
    sources = MappingProxyType({item.source: item for item in refs})
    _ensure(len(sources) == len(refs), "bundle source names must be unique")
    evidence_sha = _hash(value["evidence_sha256"], "evidence")
    return BundleContext(
        bundle_id, hashlib.sha256(raw).hexdigest(), baseline, comparison, tuple(cohort), sources,
        _confined(value["evidence_path"], Path(data_root).resolve()), evidence_sha,
    )


def _parse_source(value: object, root: Path, bundle_years: set[int]) -> SnapshotRef:
    _ensure(isinstance(value, dict) and set(value) == _SOURCE_KEYS, "bundle source has invalid keys")
    years = value["years"]
    _ensure(all(isinstance(value[key], str) and value[key] for key in ("source", "snapshot_id", "vintage_id", "publication_status")), "bundle source identity is invalid")
    _ensure(isinstance(years, list) and bool(years) and all(type(year) is int for year in years) and years == sorted(set(years)) and set(years).issubset(bundle_years), "bundle source periods are invalid")
    return SnapshotRef(
        value["source"], value["snapshot_id"], _hash(value["manifest_sha256"], "source manifest"),
        _hash(value["content_sha256"], "source content"), _confined(value["manifest_path"], root),
        _confined(value["data_path"], root), tuple(years), value["vintage_id"], value["publication_status"],
    )


def _hash(value: object, label: str) -> str:
    _ensure(isinstance(value, str) and bool(_SHA256.fullmatch(value)), f"{label} checksum is invalid")
    return value


def _confined(value: object, root: Path) -> Path:
    _ensure(isinstance(value, str) and bool(value) and not Path(value).is_absolute(), "bundle artifact path is invalid")
    resolved = (root / value).resolve()
    _ensure(resolved.is_relative_to(root), "bundle artifact path escapes the data root")
    return resolved


def _verify_artifacts(context: BundleContext, *, require_evidence: bool) -> None:
    names = set(context.sources)
    _ensure({"datasf", "t100", "ontime", "faa"}.issubset(names) and names <= set(_ARTIFACT_SCHEMA), "bundle source set is incompatible")
    manifests: dict[str, dict] = {}
    for name, ref in context.sources.items():
        raw = _read(ref.manifest_path, f"{name} manifest")
        _ensure(hashlib.sha256(raw).hexdigest() == ref.manifest_sha256, f"{name} manifest checksum does not match")
        manifest = _json_object(raw, f"{name} manifest")
        _ensure(manifest.get("snapshot_id") == ref.snapshot_id and manifest.get("validation_status") == "accepted", f"{name} snapshot identity or status is invalid")
        file_key, hash_key = _ARTIFACT_SCHEMA[name]
        filename, checksum = manifest.get(file_key), manifest.get(hash_key)
        _ensure(isinstance(filename, str) and Path(filename).name == filename, f"{name} data filename is invalid")
        _ensure(ref.data_path == ref.manifest_path.parent / filename, f"{name} data path does not match its manifest")
        _ensure(_hash(checksum, name) == ref.content_sha256, f"{name} content identity does not match")
        _ensure(hashlib.sha256(_read(ref.data_path, f"{name} data")).hexdigest() == checksum, f"{name} data checksum does not match")
        manifests[name] = manifest
    pair = {context.baseline_year, context.comparison_year}
    _ensure(_manifest_years(manifests["t100"].get("eligible_rows")) == pair == set(context.sources["t100"].years), "T-100 periods do not match the bundle")
    scope = manifests["datasf"].get("scope")
    try:
        datasf_years = {int(scope["activity_period_start"][:4]), int(scope["activity_period_end"][:4])}
    except (KeyError, TypeError, ValueError):
        datasf_years = set()
    _ensure(datasf_years == pair == set(context.sources["datasf"].years), "DataSF periods do not match the bundle")
    request = manifests["ontime"].get("request")
    _ensure(isinstance(request, dict) and request.get("year") == context.comparison_year and context.sources["ontime"].years == (context.comparison_year,), "on-time period does not match the bundle")
    cohort = manifests["faa"].get("cohort")
    year_key = f"cy{context.comparison_year}_enplanements"
    _ensure(isinstance(cohort, list) and all(isinstance(row, dict) and year_key in row for row in cohort) and context.comparison_year in context.sources["faa"].years
            and tuple(row.get("locid") for row in cohort) == context.cohort, "FAA cohort or period does not match the bundle")
    if "aip" in manifests:
        _ensure(manifests["aip"].get("fiscal_year") == context.comparison_year, "AIP period does not match the bundle")
    if require_evidence:
        evidence = _read(context.evidence_path, "bundle evidence")
        _ensure(hashlib.sha256(evidence).hexdigest() == context.evidence_sha256, "bundle evidence checksum does not match")


def _manifest_years(value: object) -> set[int]:
    if not isinstance(value, dict):
        return set()
    try:
        return {int(year) for year in value}
    except (TypeError, ValueError):
        return set()


def _read(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BundleError(f"{label} is unavailable") from exc


def _json_object(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BundleError(f"{label} is invalid JSON") from exc
    _ensure(isinstance(value, dict), f"{label} must be an object")
    return value


def _load_registry(root: Path, *, missing_ok: bool = False) -> dict:
    path = root / "bundles" / "accepted.json"
    if missing_ok and not path.exists():
        return {"schema_version": 1, "default_bundle_id": None, "bundles": {}}
    _ensure(not path.is_symlink(), "accepted bundle registry path is invalid")
    value = _json_object(_read(path, "accepted bundle registry"), "accepted bundle registry")
    bundles = value.get("bundles")
    default = value.get("default_bundle_id")
    valid = (set(value) == _REGISTRY_KEYS and type(value.get("schema_version")) is int
             and value["schema_version"] == 1 and isinstance(bundles, dict) and bool(bundles)
             and all(isinstance(name, str) and _ID.fullmatch(name) and isinstance(checksum, str)
                     and _SHA256.fullmatch(checksum) for name, checksum in bundles.items())
             and isinstance(default, str) and default in bundles)
    _ensure(valid, "accepted bundle registry is invalid")
    return value


def _registered_bundle(bundle_id: object, registry: dict, root: Path) -> BundleContext:
    _ensure(isinstance(bundle_id, str) and bundle_id in registry["bundles"], "bundle is not registered as accepted")
    return _load_bundle(bundle_id, root, require_evidence=True,
                        expected_manifest_sha256=registry["bundles"][bundle_id])


def _ensure(condition: bool, message: str) -> None:
    if not condition:
        raise BundleError(message)
