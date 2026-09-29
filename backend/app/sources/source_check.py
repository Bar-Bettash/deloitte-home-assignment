import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.sources.bundle import BundleContext

DEFAULT_RECEIPT_PATH = Path(__file__).resolve().parents[2] / "data" / "source-check.json"
_RECEIPT_KEYS = {"schema_version", "receipt_type", "admission_status", "bundle_id", "manifest_sha256", "checked_at_utc", "sources"}
_SOURCE_KEYS = {"source", "snapshot_id", "manifest_sha256", "content_sha256", "years", "outcome", "newer_complete_available", "newer_partial_year", "selected_period_revised"}
_WORKFLOW_SOURCES = {"screen": {"faa", "t100"}, "operations": {"ontime"}, "long_haul": {"t100"}, "sfo": {"datasf", "t100", "ontime"}}


class SourceCheckError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceReadiness:
    bundle_id: str
    workflow: str
    checked_at_utc: datetime
    sources: tuple[str, ...]
    newer_partial_years: tuple[tuple[str, int], ...]


def validate_source_check(
    bundle: BundleContext,
    workflow: str,
    receipt_path: Path = DEFAULT_RECEIPT_PATH,
    now: datetime | None = None,
) -> SourceReadiness:
    required = _WORKFLOW_SOURCES.get(workflow)
    if required is None:
        raise SourceCheckError("source-check workflow is unsupported")
    bundle_sources = bundle.sources
    if "aip" in bundle_sources and workflow in {"screen", "sfo"}:
        required = required | {"aip"}
    if not required.issubset(bundle_sources):
        raise SourceCheckError("bundle lacks a required workflow source")
    try:
        value = json.loads(Path(receipt_path).read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceCheckError("source-check receipt is unavailable or invalid") from exc
    scope = value.get("scope", {}) if isinstance(value, dict) and isinstance(value.get("scope", {}), dict) else {}
    admission = value.get("admission_status") if isinstance(value, dict) else None
    if admission == "evidence_only_not_admitted" or scope.get("admission_status") == "evidence_only_not_admitted":
        raise SourceCheckError("source-check receipt is evidence only and not admitted")
    if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
        raise SourceCheckError("source-check receipt has invalid keys")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["receipt_type"] != "application_source_check" or admission != "admitted":
        raise SourceCheckError("source-check receipt is not an admitted application receipt")
    if value["bundle_id"] != bundle.bundle_id or value["manifest_sha256"] != bundle.manifest_sha256:
        raise SourceCheckError("source-check bundle identity does not match")
    checked_at = _parse_utc(value["checked_at_utc"])
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise SourceCheckError("source-check validation clock is invalid")
    age = clock.astimezone(timezone.utc) - checked_at
    if age < timedelta(0) or age > timedelta(days=7):
        raise SourceCheckError("source-check receipt is future-dated or stale")
    checks = value["sources"]
    if not isinstance(checks, list) or any(
        not isinstance(item, dict)
        or set(item) != _SOURCE_KEYS
        or not isinstance(item.get("source"), str)
        for item in checks
    ):
        raise SourceCheckError("source-check source list is invalid")
    by_source = {item.get("source"): item for item in checks}
    if len(by_source) != len(checks) or not required.issubset(by_source) or not set(by_source).issubset(bundle_sources):
        raise SourceCheckError("source-check sources do not match the workflow")
    partial: list[tuple[str, int]] = []
    for source in sorted(by_source):
        check, ref = by_source[source], bundle_sources[source]
        if [check[key] for key in ("snapshot_id", "manifest_sha256", "content_sha256")] != [ref.snapshot_id, ref.manifest_sha256, ref.content_sha256] or check["years"] != list(ref.years):
            raise SourceCheckError("source-check source identity does not match")
        if source not in required:
            continue
        if check["outcome"] != "current_for_scope" or check["newer_complete_available"] is not False or check["selected_period_revised"] is not False:
            raise SourceCheckError("source-check outcome blocks admission")
        partial_year = check["newer_partial_year"]
        if partial_year is not None:
            if type(partial_year) is not int or partial_year <= max(ref.years):
                raise SourceCheckError("source-check partial period is invalid")
            partial.append((source, partial_year))
    return SourceReadiness(bundle.bundle_id, workflow, checked_at, tuple(sorted(required)), tuple(partial))


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SourceCheckError("source-check timestamp is invalid")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SourceCheckError("source-check timestamp is invalid") from exc
