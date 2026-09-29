from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUNDLE_KEYS = {
    "schema_version",
    "bundle_id",
    "baseline_year",
    "comparison_year",
    "cohort",
    "sources",
    "evidence_path",
    "evidence_sha256",
}
_SOURCE_NAMES = {"datasf", "t100", "ontime", "faa", "aip"}
_REQUIRED_SOURCES = {"datasf", "t100", "ontime", "faa"}
_DATA_HASH_KEY = {
    "datasf": "content_sha256",
    "t100": "parquet_sha256",
    "ontime": "parquet_sha256",
    "faa": "content_sha256",
    "aip": "content_sha256",
}
_SOURCE_KEYS = {
    "source",
    "snapshot_id",
    "manifest_sha256",
    "content_sha256",
    "manifest_path",
    "data_path",
    "years",
    "vintage_id",
    "publication_status",
}


class BundleError(RuntimeError):
    """Raised when bundle metadata violates its contract."""


def _validate_years(years: object, *, label: str) -> None:
    valid = (
        isinstance(years, tuple)
        and bool(years)
        and all(type(year) is int and 1900 <= year <= 2100 for year in years)
        and years == tuple(sorted(set(years)))
    )
    if not valid:
        raise BundleError(f"{label} years are invalid")


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

    def __post_init__(self) -> None:
        _validate_years(self.years, label="snapshot")


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

    def __post_init__(self) -> None:
        _validate_years((self.baseline_year, self.comparison_year), label="bundle")
        if self.baseline_year >= self.comparison_year:
            raise BundleError("bundle years are invalid")
        if not isinstance(self.cohort, tuple):
            raise BundleError("bundle cohort must be a tuple")
        if not isinstance(self.sources, Mapping):
            raise BundleError("bundle sources must be a mapping")
        copied = dict(self.sources)
        if any(not isinstance(key, str) or not isinstance(value, SnapshotRef) for key, value in copied.items()):
            raise BundleError("bundle sources are invalid")
        object.__setattr__(self, "sources", MappingProxyType(copied))


def load_bundle(bundle_id: str, data_root: Path = DEFAULT_DATA_ROOT) -> BundleContext:
    if not isinstance(bundle_id, str) or not _ID.fullmatch(bundle_id):
        raise BundleError("bundle ID is invalid")
    root = Path(data_root).resolve()
    path = _confined(f"bundles/{bundle_id}/manifest.json", root)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundleError("bundle manifest is unavailable") from exc
    value = _json_object(raw, "bundle manifest")
    if set(value) != _BUNDLE_KEYS:
        raise BundleError("bundle manifest has invalid keys")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["bundle_id"] != bundle_id
    ):
        raise BundleError("bundle manifest identity is incompatible")
    cohort = value["cohort"]
    if (
        not isinstance(cohort, list)
        or not cohort
        or not all(isinstance(item, str) and re.fullmatch(r"[A-Z]{3}", item) for item in cohort)
        or len(cohort) != len(set(cohort))
    ):
        raise BundleError("bundle cohort is invalid")
    source_values = value["sources"]
    if not isinstance(source_values, list) or not source_values:
        raise BundleError("bundle sources are invalid")
    baseline, comparison = value["baseline_year"], value["comparison_year"]
    _validate_years((baseline, comparison), label="bundle")
    if baseline >= comparison:
        raise BundleError("bundle years are invalid")
    years = {baseline, comparison}
    refs = tuple(_parse_source(item, root, years) for item in source_values)
    if any(ref.source not in _SOURCE_NAMES for ref in refs):
        raise BundleError("bundle source name is incompatible")
    sources = {ref.source: ref for ref in refs}
    if len(sources) != len(refs):
        raise BundleError("bundle source names must be unique")
    context = BundleContext(
        bundle_id=bundle_id,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        baseline_year=baseline,
        comparison_year=comparison,
        cohort=tuple(cohort),
        sources=sources,
        evidence_path=_confined(value["evidence_path"], root),
        evidence_sha256=_hash(value["evidence_sha256"], "evidence"),
    )
    _verify_artifacts(context)
    return context


def _json_object(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BundleError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise BundleError(f"{label} must be an object")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise BundleError(f"{label} checksum is invalid")
    return value


def _confined(value: object, root: Path) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise BundleError("bundle artifact path is invalid")
    resolved_root = root.resolve()
    resolved = (resolved_root / value).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise BundleError("bundle artifact path escapes the data root")
    return resolved


def _parse_source(value: object, root: Path, bundle_years: set[int]) -> SnapshotRef:
    if not isinstance(value, dict) or set(value) != _SOURCE_KEYS:
        raise BundleError("bundle source has invalid keys")
    identity_keys = ("source", "snapshot_id", "vintage_id", "publication_status")
    if not all(isinstance(value[key], str) and value[key] for key in identity_keys):
        raise BundleError("bundle source identity is invalid")
    years = value["years"]
    valid_years = (
        isinstance(years, list)
        and bool(years)
        and all(type(year) is int for year in years)
        and years == sorted(set(years))
        and set(years).issubset(bundle_years)
    )
    if not valid_years:
        raise BundleError("bundle source periods are invalid")
    return SnapshotRef(
        source=value["source"],
        snapshot_id=value["snapshot_id"],
        manifest_sha256=_hash(value["manifest_sha256"], "source manifest"),
        content_sha256=_hash(value["content_sha256"], "source content"),
        manifest_path=_confined(value["manifest_path"], root),
        data_path=_confined(value["data_path"], root),
        years=tuple(years),
        vintage_id=value["vintage_id"],
        publication_status=value["publication_status"],
    )


def _validate_aip_scope(context: BundleContext, manifest: Mapping[str, object]) -> None:
    ref = context.sources.get("aip")
    if ref is None:
        return
    fiscal_year = manifest.get("fiscal_year") if isinstance(manifest, Mapping) else None
    valid = (
        ref.years == (context.comparison_year,)
        and type(fiscal_year) is int
        and fiscal_year == context.comparison_year
    )
    if not valid:
        raise BundleError("AIP period does not match the bundle")


def _read(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BundleError(f"{label} is unavailable") from exc


def _sha256_file(path: Path, label: str) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BundleError(f"{label} is unavailable") from exc
    return digest.hexdigest()


def _verify_artifacts(context: BundleContext) -> None:
    names = set(context.sources)
    if not _REQUIRED_SOURCES.issubset(names) or not names <= _SOURCE_NAMES:
        raise BundleError("bundle source set is incompatible")
    for name, ref in context.sources.items():
        if _sha256_file(ref.manifest_path, f"{name} manifest") != ref.manifest_sha256:
            raise BundleError(f"{name} manifest checksum does not match")
        manifest = _json_object(_read(ref.manifest_path, f"{name} manifest"), f"{name} manifest")
        if manifest.get("snapshot_id") != ref.snapshot_id:
            raise BundleError(f"{name} snapshot identity is invalid")
        if _hash(manifest.get(_DATA_HASH_KEY[name]), name) != ref.content_sha256:
            raise BundleError(f"{name} content identity does not match")
        if _sha256_file(ref.data_path, f"{name} data") != ref.content_sha256:
            raise BundleError(f"{name} data checksum does not match")
        if name == "aip":
            _validate_aip_scope(context, manifest)
    if _sha256_file(context.evidence_path, "bundle evidence") != context.evidence_sha256:
        raise BundleError("bundle evidence checksum does not match")


def main(argv: list[str] | None = None, *, data_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an immutable bundle candidate")
    parser.add_argument("--check-candidate", required=True, metavar="BUNDLE_ID")
    args = parser.parse_args(argv)
    try:
        context = load_bundle(
            args.check_candidate,
            data_root=DEFAULT_DATA_ROOT if data_root is None else data_root,
        )
    except BundleError as exc:
        parser.exit(1, f"bundle candidate check failed: {exc}\n")
    print(
        json.dumps(
            {
                "bundle_id": context.bundle_id,
                "manifest_sha256": context.manifest_sha256,
                "status": "valid",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
