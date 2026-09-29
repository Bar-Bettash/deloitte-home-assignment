"""Produce a bundle-bound source revision receipt from conclusive official probes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urljoin

import duckdb
import httpx
from app.sources.aip import AIPError, verify_aip_snapshot
from app.sources.bundle import BundleContext, BundleError, SnapshotRef, load_bundle
from app.sources.datasf import DataSFError, verify_datasf_snapshot
from app.sources.faa import FAAError, verify_faa_snapshot
from app.sources.ontime import OnTimeError, verify_ontime_snapshot
from app.sources.t100 import T100Error, verify_t100_snapshot

DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
DEFAULT_QUALIFICATION = (
    Path(__file__).resolve().parents[1]
    / "docs/evidence/recent-source-qualification-20260927.json"
)
RELEASE_URL = "https://www.transtats.bts.gov/releaseinfo.asp"
SOURCE_ORDER = ("datasf", "t100", "ontime", "faa", "aip")


class SourceStatusError(RuntimeError):
    """Official observations cannot support a source-admission receipt."""


class ProbeClient(Protocol):
    def collect(
        self, bundle: BundleContext, qualification: Mapping[str, object]
    ) -> Mapping[str, Mapping[str, object]]: ...


def verify_qualification_binding(bundle: BundleContext, qualification_path: Path) -> None:
    """Prove that qualified raw partitions are the inputs behind this bundle."""
    verifiers = {
        "datasf": verify_datasf_snapshot,
        "t100": verify_t100_snapshot,
        "ontime": verify_ontime_snapshot,
        "faa": verify_faa_snapshot,
        "aip": verify_aip_snapshot,
    }
    try:
        for name in SOURCE_ORDER:
            ref = bundle.sources[name]
            data_root = ref.manifest_path.parents[2]
            verifiers[name](ref.snapshot_id, qualification_path, data_root=data_root)
    except (
        DataSFError, T100Error, OnTimeError, FAAError, AIPError, OSError, ValueError, duckdb.Error
    ) as exc:
        raise SourceStatusError(
            "source qualification is not bound to the selected bundle"
        ) from exc


def build_receipt(
    bundle: BundleContext,
    qualification: Mapping[str, object],
    observations: Mapping[str, Mapping[str, object]],
    *,
    checked_at: datetime | None = None,
) -> dict[str, object]:
    """Evaluate source-specific proof; HTTP success or latest-year alone never passes."""
    expected_names = set(bundle.sources)
    if tuple(bundle.sources) != SOURCE_ORDER:
        raise SourceStatusError("bundle source set is unsupported")
    if set(observations) != expected_names:
        raise SourceStatusError("official observations must cover every bundle source exactly")
    clock = checked_at or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise SourceStatusError("source-check time must be timezone-aware")
    checks = [
        _evaluate_source(name, bundle.sources[name], qualification, observations[name])
        for name in SOURCE_ORDER
    ]
    admitted = all(item["outcome"] == "current_for_scope" for item in checks)
    return {
        "schema_version": 1,
        "receipt_type": "application_source_check",
        "admission_status": "admitted" if admitted else "blocked",
        "bundle_id": bundle.bundle_id,
        "manifest_sha256": bundle.manifest_sha256,
        "checked_at_utc": clock.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sources": checks,
    }


def _evaluate_source(
    name: str,
    ref: SnapshotRef,
    qualification: Mapping[str, object],
    observation: Mapping[str, object],
) -> dict[str, object]:
    selected = _selected_status(name, ref, qualification, observation)
    if observation.get("newer_period_proof") != "official_index_or_content_query":
        selected = "inconclusive"
    newer_year = observation.get("newer_year")
    newer_months = observation.get("newer_months", [])
    complete_years = observation.get("newer_complete_years", [])
    if newer_year is not None and (
        type(newer_year) is not int or newer_year <= max(ref.years)
    ):
        raise SourceStatusError(f"{name} newer period is invalid")
    if (
        not isinstance(newer_months, list)
        or any(type(month) is not int or month not in range(1, 13) for month in newer_months)
        or newer_months != sorted(set(newer_months))
    ):
        raise SourceStatusError(f"{name} newer-month coverage is invalid")
    if newer_months and newer_year is None:
        raise SourceStatusError(f"{name} newer months lack their year")
    if (
        not isinstance(complete_years, list)
        or any(type(year) is not int or year <= max(ref.years) for year in complete_years)
        or complete_years != sorted(set(complete_years))
    ):
        raise SourceStatusError(f"{name} newer complete periods are invalid")
    newer_complete = bool(complete_years) or len(newer_months) == 12
    latest_is_complete = (
        newer_year in complete_years
        or (newer_year is not None and len(newer_months) == 12)
    )
    newer_partial = newer_year if newer_year is not None and not latest_is_complete else None
    if selected == "revised":
        outcome = "selected_period_revised"
    elif selected != "unchanged":
        outcome = "inconclusive"
    elif newer_complete:
        outcome = "newer_complete_available"
    else:
        outcome = "current_for_scope"
    return {
        "source": name,
        "snapshot_id": ref.snapshot_id,
        "manifest_sha256": ref.manifest_sha256,
        "content_sha256": ref.content_sha256,
        "years": list(ref.years),
        "outcome": outcome,
        "newer_complete_available": newer_complete,
        "newer_partial_year": newer_partial,
        "selected_period_revised": selected == "revised",
    }


def _selected_status(
    name: str,
    ref: SnapshotRef,
    qualification: Mapping[str, object],
    observation: Mapping[str, object],
) -> str:
    if not isinstance(observation, Mapping):
        raise SourceStatusError(f"{name} observation is invalid")
    mode = observation.get("proof_mode")
    if name == "datasf":
        expected = _qualification_source(qualification, "datasf").get("partitions")
        hashes = (
            [item.get("sha256") for item in expected]
            if isinstance(expected, list) and all(isinstance(item, dict) for item in expected)
            else None
        )
        observed = observation.get("selected_partition_sha256")
        if mode != "selected_content_hashes" or not isinstance(observed, list):
            return "inconclusive"
        return "unchanged" if observed == hashes else "revised"
    if name == "t100":
        partitions = _qualification_source(qualification, "t100").get("partitions")
        expected = (
            {
                f"{item.get('year')}-{item.get('state')}": item.get("csv_sha256")
                for item in partitions
            }
            if isinstance(partitions, list)
            and all(isinstance(item, dict) for item in partitions)
            else None
        )
        observed = observation.get("selected_csv_sha256")
        if mode != "selected_extracted_content_hashes" or not isinstance(observed, dict):
            return "inconclusive"
        return "unchanged" if observed == expected else "revised"
    if name == "ontime":
        partitions = _qualification_source(qualification, "ontime").get("partitions")
        expected = (
            {str(item.get("month")): item.get("csv_sha256") for item in partitions}
            if isinstance(partitions, list) and all(isinstance(item, dict) for item in partitions)
            else None
        )
        observed = observation.get("selected_csv_sha256")
        if mode != "selected_extracted_content_hashes" or not isinstance(observed, dict):
            return "inconclusive"
        return "unchanged" if observed == expected else "revised"
    if name in {"faa", "aip"}:
        observed = observation.get("selected_content_sha256")
        if mode != "selected_content_hash" or not _digest(observed):
            return "inconclusive"
        return "unchanged" if observed == ref.content_sha256 else "revised"
    raise SourceStatusError(f"unsupported source: {name}")


def _qualification_source(
    qualification: Mapping[str, object], name: str
) -> Mapping[str, object]:
    sources = qualification.get("sources")
    key = "faa_aip_fy2025_awards" if name == "aip" else name
    value = sources.get(key) if isinstance(sources, Mapping) else None
    if not isinstance(value, Mapping):
        raise SourceStatusError(f"qualification lacks {name}")
    return value


def _digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class HttpProbeClient:
    """Bounded official HTTP collector. Selected content is compared semantically or by hash."""

    def __init__(
        self, client: httpx.Client | None = None, *, now: datetime | None = None
    ) -> None:
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(60.0), follow_redirects=True,
            headers={"User-Agent": "airport-analyst-source-check/1.0"},
        )
        self._owns_client = client is None
        self.now = now or datetime.now(timezone.utc)

    def collect(
        self, bundle: BundleContext, qualification: Mapping[str, object]
    ) -> Mapping[str, Mapping[str, object]]:
        try:
            release = self._get(RELEASE_URL, 2 * 1024 * 1024)
            return {
                "datasf": self._datasf(qualification),
                "t100": self._t100(qualification, release),
                "ontime": self._ontime(qualification, release),
                "faa": self._document_probe("faa", qualification),
                "aip": self._document_probe("aip", qualification),
            }
        finally:
            if self._owns_client:
                self.client.close()

    def _datasf(self, qualification: Mapping[str, object]) -> dict[str, object]:
        source = _qualification_source(qualification, "datasf")
        partitions = source.get("partitions")
        if not isinstance(partitions, list):
            raise SourceStatusError("DataSF qualification partitions are invalid")
        hashes = [
            hashlib.sha256(self._get(item["url"], 2 * 1024 * 1024)).hexdigest()
            for item in partitions
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]
        if len(hashes) != len(partitions):
            raise SourceStatusError("DataSF qualification URLs are invalid")
        url = "https://data.sf.gov/resource/rkru-6vcg.csv?" + urlencode(
            {
                "$select": "activity_period",
                "$where": "activity_period > '202512'",
                "$group": "activity_period",
                "$order": "activity_period",
                "$limit": "5000",
            }
        )
        periods = [row["activity_period"] for row in csv.DictReader(
            io.StringIO(self._get(url, 1024 * 1024).decode("utf-8-sig"))
        )]
        year, months, complete = _newer_months(periods, 2025)
        return {
            "proof_mode": "selected_content_hashes", "selected_partition_sha256": hashes,
            "newer_period_proof": "official_index_or_content_query",
            "newer_year": year, "newer_months": months,
            "newer_complete_years": complete,
        }

    def _t100(
        self, qualification: Mapping[str, object], release: bytes
    ) -> dict[str, object]:
        source = _qualification_source(qualification, "t100")
        partitions = source.get("partitions")
        fields = source.get("request_fields")
        if (
            not isinstance(partitions, list)
            or len(partitions) != 16
            or not isinstance(fields, list)
            or not all(isinstance(field, str) for field in fields)
        ):
            raise SourceStatusError("T-100 qualification partitions are invalid")
        hashes: dict[str, str] = {}
        for item in partitions:
            if (
                not isinstance(item, dict)
                or type(item.get("year")) is not int
                or not isinstance(item.get("state"), str)
                or not isinstance(item.get("geography"), str)
            ):
                raise SourceStatusError("T-100 qualification partition is invalid")
            key = f"{item['year']}-{item['state']}"
            hashes[key] = self._download_t100_partition(
                item["year"], item["geography"], fields
            )
        release_text = release.decode("utf-8", errors="replace")
        year, month = min(
            _latest_period(release_text, label)
            for label in (
                "T-100 Domestic Segment (All Carriers)",
                "T-100 International Segment (All Carriers)",
            )
        )
        return {
            "proof_mode": "selected_extracted_content_hashes",
            "selected_csv_sha256": hashes,
            "release_log_sha256": hashlib.sha256(release).hexdigest(),
            "newer_period_proof": "official_index_or_content_query",
            "newer_year": year if year > 2025 else None,
            "newer_months": list(range(1, month + 1)) if year > 2025 else [],
            "newer_complete_years": _complete_years_through(year, month, 2025),
        }

    def _ontime(
        self, qualification: Mapping[str, object], release: bytes
    ) -> dict[str, object]:
        source = _qualification_source(qualification, "ontime")
        partitions = source.get("partitions")
        if not isinstance(partitions, list) or len(partitions) != 12:
            raise SourceStatusError("on-time qualification partitions are invalid")
        hashes: dict[str, str] = {}
        for item in partitions:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                raise SourceStatusError("on-time qualification URL is invalid")
            hashes[str(item.get("month"))] = self._zip_csv_sha256(item["url"])
        year, month = _latest_period(
            release.decode("utf-8", errors="replace"),
            "Reporting Carrier On-Time Performance (1987-present)",
        )
        return {
            "proof_mode": "selected_extracted_content_hashes",
            "selected_csv_sha256": hashes,
            "newer_period_proof": "official_index_or_content_query",
            "newer_year": year if year > 2025 else None,
            "newer_months": list(range(1, month + 1)) if year > 2025 else [],
            "newer_complete_years": _complete_years_through(year, month, 2025),
        }

    def _download_t100_partition(
        self, year: int, geography: str, fields: Sequence[str]
    ) -> str:
        url = "https://www.transtats.bts.gov/DL_SelectFields.aspx"
        params = {"QO_fu146_anzr": "", "gnoyr_VQ": "FMG"}
        try:
            form = self.client.get(url, params=params)
            form.raise_for_status()
            if len(form.content) > 2 * 1024 * 1024:
                raise SourceStatusError("T-100 download form exceeds its size limit")
            page = form.text
            data = {
                "__EVENTTARGET": "",
                "__EVENTARGUMENT": "",
                "__LASTFOCUS": "",
                "__VIEWSTATE": _hidden_value(page, "__VIEWSTATE"),
                "__VIEWSTATEGENERATOR": _hidden_value(page, "__VIEWSTATEGENERATOR"),
                "__EVENTVALIDATION": _hidden_value(page, "__EVENTVALIDATION"),
                "txtSearch": "",
                "cboGeography": geography,
                "cboYear": str(year),
                "cboPeriod": "All",
                "btnDownload": "Download",
            }
            data.update({field: "on" for field in fields})
            with self.client.stream(
                "POST", url, params=params, data=data, timeout=180.0,
                headers={"Referer": str(form.url)},
            ) as response:
                response.raise_for_status()
                with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as handle:
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 32 * 1024 * 1024:
                            raise SourceStatusError("T-100 ZIP exceeds 32 MiB")
                        handle.write(chunk)
                    handle.seek(0)
                    return _archive_csv_sha256(handle, "T-100", 64 * 1024 * 1024)
        except httpx.HTTPError as exc:
            raise SourceStatusError("T-100 selected-period content check failed") from exc

    def _document_probe(
        self, name: str, qualification: Mapping[str, object]
    ) -> dict[str, object]:
        source = _qualification_source(qualification, name)
        if name == "faa":
            url = source.get("url")
            index_url = (
                "https://www.faa.gov/airports/planning_capacity/"
                "passenger_allcargo_stats/passenger"
            )
            pattern = r"cy(20\d{2})[^\"']*commercial-service-enplanements[^\"']*\.pdf"
        else:
            detail = source.get("source")
            url = detail.get("url") if isinstance(detail, Mapping) else None
            index_url = "https://www.faa.gov/airports/aip/grant_histories"
            pattern = r"FY[_ -]?(20\d{2})[_ -]?AIP[_ -]?Grants[^\"']*\.xlsx"
        if not isinstance(url, str):
            raise SourceStatusError(f"{name} qualification URL is invalid")
        selected = self._get(url, 6 * 1024 * 1024)
        index = self._get(index_url, 3 * 1024 * 1024).decode("utf-8", errors="replace")
        raw_links = [
            html.unescape(link)
            for link in re.findall(r'href=["\']([^"\']+)', index, flags=re.IGNORECASE)
        ]
        if name == "aip":
            year_pages: dict[int, str] = {}
            for link in raw_links:
                match = re.search(
                    r"/airports/aip/(?:grant_histories/(20\d{2})|(20\d{2})_aip_grants)/?$",
                    link,
                    flags=re.IGNORECASE,
                )
                if match is not None:
                    year_pages[int(match.group(1) or match.group(2))] = urljoin(
                        index_url, link
                    )
            selected_page = year_pages.get(2025)
            if selected_page is None:
                raise SourceStatusError("aip official index did not prove the selected edition")
            detail_index = self._get(selected_page, 3 * 1024 * 1024).decode(
                "utf-8", errors="replace"
            )
            raw_links = [
                html.unescape(link)
                for link in re.findall(
                    r'href=["\']([^"\']+)', detail_index, flags=re.IGNORECASE
                )
            ]
            links = {
                urljoin(selected_page, link)
                for link in raw_links
                if re.search(pattern, link, flags=re.IGNORECASE)
            }
            years = sorted(year_pages)
            newer = [year for year in years if year > 2025]
            newer_proof = "official_index_or_content_query"
            complete_years = newer[:-1]
            if newer:
                newest = newer[-1]
                newest_page = self._get(year_pages[newest], 3 * 1024 * 1024).decode(
                    "utf-8", errors="replace"
                )
                full_year_workbook = re.search(
                    rf"FY[_ -]?{newest}[_ -]?AIP[_ -]?Grants[^\"']*\.xlsx",
                    newest_page,
                    flags=re.IGNORECASE,
                )
                explicit_completion = re.search(
                    rf"(?:final|full[- ]year).{{0,80}}(?:FY|fiscal year)?\s*{newest}",
                    newest_page,
                    flags=re.IGNORECASE,
                )
                ongoing = (
                    self.now.year == newest
                    and (self.now.month, self.now.day) <= (9, 30)
                    and re.search(r"Announcement\s*#\d+", newest_page, re.IGNORECASE)
                )
                if full_year_workbook or explicit_completion:
                    complete_years.append(newest)
                elif not ongoing:
                    newer_proof = "inconclusive"
        else:
            links = {
                urljoin(index_url, link)
                for link in raw_links
                if re.search(pattern, link, flags=re.IGNORECASE)
            }
            years = sorted({
                int(match.group(1))
                for link in links
                if (match := re.search(pattern, link, flags=re.IGNORECASE)) is not None
            })
            newer = [year for year in years if year > 2025]
            newer_proof = "official_index_or_content_query"
            complete_years = newer
        if 2025 not in years:
            raise SourceStatusError(f"{name} official index did not prove the selected edition")
        selected_links = {
            link for link in links
            if re.search(pattern, link, flags=re.IGNORECASE).group(1) == "2025"
        }
        exact_selected_edition = selected_links == {url}
        newest = max(newer) if newer else None
        return {
            "proof_mode": (
                "selected_content_hash" if exact_selected_edition
                else "selected_edition_ambiguous_or_superseded"
            ),
            "selected_content_sha256": hashlib.sha256(selected).hexdigest(),
            "newer_period_proof": newer_proof,
            "newer_year": newest,
            "newer_months": [],
            "newer_complete_years": complete_years,
        }

    def _get(self, url: str, maximum: int) -> bytes:
        try:
            with self.client.stream("GET", url) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > maximum:
                        raise SourceStatusError("official response exceeds its size limit")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise SourceStatusError("official source request failed") from exc
        return b"".join(chunks)

    def _zip_csv_sha256(self, url: str) -> str:
        try:
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as handle:
                with self.client.stream("GET", url, timeout=120.0) as response:
                    response.raise_for_status()
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 64 * 1024 * 1024:
                            raise SourceStatusError("on-time ZIP exceeds 64 MiB")
                        handle.write(chunk)
                handle.seek(0)
                return _archive_csv_sha256(handle, "on-time", 400 * 1024 * 1024)
        except (httpx.HTTPError, OSError, zipfile.BadZipFile) as exc:
            raise SourceStatusError("on-time selected-period content check failed") from exc


def _hidden_value(page: str, name: str) -> str:
    match = re.search(
        rf'name=["\']{re.escape(name)}["\'][^>]*value=["\']([^"\']*)',
        page,
        flags=re.IGNORECASE,
    )
    if match is None:
        raise SourceStatusError(f"T-100 download form lacks {name}")
    return html.unescape(match.group(1))


def _archive_csv_sha256(handle: object, label: str, maximum: int) -> str:
    try:
        with zipfile.ZipFile(handle) as archive:
            members = [
                item for item in archive.infolist()
                if item.filename.lower().endswith(".csv") and not item.is_dir()
            ]
            if len(members) != 1 or members[0].file_size > maximum:
                raise SourceStatusError(f"{label} ZIP member shape is invalid")
            digest = hashlib.sha256()
            with archive.open(members[0]) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
    except (OSError, zipfile.BadZipFile) as exc:
        raise SourceStatusError(f"{label} ZIP is invalid") from exc


def _latest_period(html: str, label: str) -> tuple[int, int]:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    match = re.search(re.escape(label) + r".*?\b(1[0-2]|[1-9])/(20\d{2})\b", text)
    if match is None:
        raise SourceStatusError(f"official release index lacks {label}")
    return int(match.group(2)), int(match.group(1))


def _newer_months(
    periods: Sequence[str], comparison_year: int
) -> tuple[int | None, list[int], list[int]]:
    parsed: dict[int, set[int]] = {}
    for period in periods:
        if not isinstance(period, str) or re.fullmatch(r"20\d{4}", period) is None:
            raise SourceStatusError("official newer-period response is invalid")
        year, month = int(period[:4]), int(period[4:])
        if month not in range(1, 13):
            raise SourceStatusError("official newer-period month is invalid")
        if year > comparison_year:
            parsed.setdefault(year, set()).add(month)
    if not parsed:
        return None, [], []
    year = max(parsed)
    complete = sorted(value for value, months in parsed.items() if months == set(range(1, 13)))
    return year, sorted(parsed[year]), complete


def _complete_years_through(year: int, month: int, comparison_year: int) -> list[int]:
    if year <= comparison_year:
        return []
    last_complete = year if month == 12 else year - 1
    return list(range(comparison_year + 1, last_complete + 1))


def _read_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceStatusError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise SourceStatusError(f"{label} must be an object")
    return value


def _write_atomic(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None, *, probe: ProbeClient | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check official selected-period source status")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--qualification", type=Path, default=DEFAULT_QUALIFICATION)
    args = parser.parse_args(argv)
    try:
        bundle = load_bundle(args.bundle, data_root=args.data_root)
        qualification = _read_object(args.qualification, "source qualification")
        verify_qualification_binding(bundle, args.qualification)
        collector = probe or HttpProbeClient()
        try:
            observations = collector.collect(bundle, qualification)
        except SourceStatusError:
            raise
        except Exception as exc:  # any probe fault must fail closed with one line
            raise SourceStatusError(f"official probe failed: {type(exc).__name__}: {exc}") from exc
        receipt = build_receipt(bundle, qualification, observations)
        if receipt["admission_status"] != "admitted":
            blocked = [item["source"] for item in receipt["sources"] if item["outcome"] != "current_for_scope"]
            raise SourceStatusError("official source check blocked admission: " + ", ".join(blocked))
        _write_atomic(args.output, receipt)
    except (SourceStatusError, BundleError, OSError) as exc:
        parser.exit(1, f"source status check failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
