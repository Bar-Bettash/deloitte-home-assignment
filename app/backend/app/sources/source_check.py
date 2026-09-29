"""Strict validation for bundle-bound official-source admission receipts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.sources.bundle import BundleContext

_RECEIPT_KEYS = {
    "schema_version",
    "receipt_type",
    "admission_status",
    "bundle_id",
    "manifest_sha256",
    "checked_at_utc",
    "sources",
}
_SOURCE_KEYS = {
    "source",
    "snapshot_id",
    "manifest_sha256",
    "content_sha256",
    "years",
    "outcome",
    "newer_complete_available",
    "newer_partial_year",
    "selected_period_revised",
}
_SOURCE_ORDER = ("datasf", "t100", "ontime", "faa", "aip")
_MAX_AGE = timedelta(days=7)


class SourceCheckError(RuntimeError):
    """A source admission receipt is absent, malformed, stale, or blocking."""


@dataclass(frozen=True, slots=True)
class SourceReadiness:
    bundle_id: str
    manifest_sha256: str
    checked_at_utc: datetime
    sources: tuple[str, ...]
    newer_partial_years: tuple[tuple[str, int], ...]


def validate_source_check_payload(
    bundle: BundleContext,
    payload: object,
    *,
    now: datetime | None = None,
) -> SourceReadiness:
    """Validate an already-loaded DB/JSON payload without filesystem access."""
    if type(payload) is not dict or set(payload) != _RECEIPT_KEYS:
        raise SourceCheckError("source-check receipt has invalid keys")
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != 1
        or payload["receipt_type"] != "application_source_check"
        or payload["admission_status"] != "admitted"
    ):
        raise SourceCheckError("source-check receipt is not admitted")
    if (
        payload["bundle_id"] != bundle.bundle_id
        or payload["manifest_sha256"] != bundle.manifest_sha256
    ):
        raise SourceCheckError("source-check bundle identity does not match")
    checked_at = _parse_utc(payload["checked_at_utc"])
    clock = datetime.now(timezone.utc) if now is None else now
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise SourceCheckError("source-check validation clock is invalid")
    age = clock.astimezone(timezone.utc) - checked_at
    if age < timedelta(0) or age > _MAX_AGE:
        raise SourceCheckError("source-check receipt is future-dated or stale")

    if tuple(bundle.sources) != _SOURCE_ORDER:
        raise SourceCheckError("source-check bundle source scope is unsupported")
    checks = payload["sources"]
    if not isinstance(checks, list) or any(type(item) is not dict for item in checks):
        raise SourceCheckError("source-check source list is invalid")
    names = tuple(item.get("source") for item in checks)
    if (
        any(not isinstance(name, str) for name in names)
        or names != _SOURCE_ORDER
        or len(set(names)) != len(names)
    ):
        raise SourceCheckError("source-check sources do not match the bundle")

    partial: list[tuple[str, int]] = []
    for check, (name, ref) in zip(checks, bundle.sources.items(), strict=True):
        if set(check) != _SOURCE_KEYS:
            raise SourceCheckError("source-check source record has invalid keys")
        identity = (
            check["source"],
            check["snapshot_id"],
            check["manifest_sha256"],
            check["content_sha256"],
            check["years"],
        )
        expected = (
            name,
            ref.snapshot_id,
            ref.manifest_sha256,
            ref.content_sha256,
            list(ref.years),
        )
        if identity != expected:
            raise SourceCheckError("source-check source identity does not match")
        if (
            check["outcome"] != "current_for_scope"
            or check["newer_complete_available"] is not False
            or check["selected_period_revised"] is not False
        ):
            raise SourceCheckError("source-check outcome blocks admission")
        partial_year = check["newer_partial_year"]
        if partial_year is not None:
            if (
                type(partial_year) is not int
                or partial_year <= max(ref.years)
                or partial_year > 2100
            ):
                raise SourceCheckError("source-check partial period is invalid")
            partial.append((name, partial_year))
    return SourceReadiness(
        bundle.bundle_id,
        bundle.manifest_sha256,
        checked_at,
        _SOURCE_ORDER,
        tuple(partial),
    )


def validate_source_check_file(
    bundle: BundleContext,
    path: Path,
    *,
    now: datetime | None = None,
) -> SourceReadiness:
    """Offline adapter for one explicit receipt path; hosted code passes DB payloads."""
    try:
        payload = json.loads(Path(path).read_bytes(), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceCheckError("source-check receipt is unavailable or invalid") from exc
    return validate_source_check_payload(bundle, payload, now=now)


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or "T" not in value or not value.endswith("Z"):
        raise SourceCheckError("source-check timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SourceCheckError("source-check timestamp is invalid") from exc
    if parsed.utcoffset() != timedelta(0):
        raise SourceCheckError("source-check timestamp is invalid")
    return parsed.astimezone(timezone.utc)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise SourceCheckError("source-check receipt contains duplicate keys")
        value[key] = item
    return value
