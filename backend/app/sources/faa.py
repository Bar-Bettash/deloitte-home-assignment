"""Acquire and parse the FAA CY2024 commercial-service airport cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

FAA_URL = (
    "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/"
    "passenger/arp-cy2024-commercial-service-enplanements.pdf"
)
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "faa"
MAX_PDF_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 60.0
EXPECTED_TABLE_ROWS = 513
NEW_ENGLAND_STATES = {"CT", "ME", "MA", "NH", "RI", "VT"}

_ROW_PATTERN = re.compile(
    r"^\s*(?P<rank>\d+)\s+(?P<region>[A-Z]{2})\s+(?P<state>[A-Z]{2})\s+"
    r"(?P<locid>[A-Z0-9]{3,4})\s+.*?\s+(?P<service_level>P|CS)\s+"
    r"(?P<hub>L|M|S|N|None)\s+(?P<cy24>[\d,]+)\s+(?P<cy23>[\d,]+)\s+"
    r"(?P<change>-?\d+\.\d+)%\s*$"
)


class FAAError(RuntimeError):
    """The official PDF could not produce a validated FAA cohort."""


@dataclass(frozen=True)
class FAAAirport:
    rank: int
    region_office: str
    state: str
    locid: str
    service_level: str
    hub: str
    cy2024_enplanements: int
    cy2023_enplanements: int
    percent_change: float


def fetch_faa_pdf(client: httpx.Client) -> bytes:
    """Download the exact bounded official PDF without retries."""
    try:
        with client.stream("GET", FAA_URL, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type != "application/pdf":
                raise FAAError("FAA response is not a PDF")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise FAAError("FAA PDF exceeds the 5 MiB limit")
                chunks.append(chunk)
    except httpx.TimeoutException as exc:
        raise FAAError("FAA PDF request timed out") from exc
    except httpx.HTTPError as exc:
        raise FAAError("FAA PDF request failed") from exc
    content = b"".join(chunks)
    if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
        raise FAAError("FAA response is not a complete PDF")
    return content


def parse_faa_cohort(text: str) -> list[FAAAirport]:
    """Parse and validate the official fixed-layout table, then select RO=NE."""
    required_markers = (
        "Source: CY2024 ACAIS",
        "Cy2024 Enplanements at All Commercial Service Airports (by Rank)",
        "Page 11 of 11",
    )
    if any(marker not in text for marker in required_markers):
        raise FAAError("FAA PDF text is missing required document markers")
    if re.search(
        r"Nonprimary Commercial\s+119 Service\s+394 Total Primary Airports", text
    ) is None:
        raise FAAError("FAA PDF text is missing required airport-count totals")

    rows: list[FAAAirport] = []
    for line in text.splitlines():
        match = _ROW_PATTERN.match(line)
        if match is None:
            continue
        rows.append(
            FAAAirport(
                rank=int(match.group("rank")),
                region_office=match.group("region"),
                state=match.group("state"),
                locid=match.group("locid"),
                service_level=match.group("service_level"),
                hub=match.group("hub"),
                cy2024_enplanements=int(match.group("cy24").replace(",", "")),
                cy2023_enplanements=int(match.group("cy23").replace(",", "")),
                percent_change=float(match.group("change")),
            )
        )
    if len(rows) != EXPECTED_TABLE_ROWS:
        raise FAAError("FAA table does not contain the expected 513 ranked airports")
    if len({row.rank for row in rows}) != len(rows) or len({row.locid for row in rows}) != len(rows):
        raise FAAError("FAA table contains duplicate ranks or airport IDs")
    if any(row.cy2024_enplanements < 0 or row.cy2023_enplanements < 0 for row in rows):
        raise FAAError("FAA table contains an invalid enplanement count")

    cohort = [row for row in rows if row.region_office == "NE"]
    if len(cohort) != 22 or {row.state for row in cohort} != NEW_ENGLAND_STATES:
        raise FAAError("FAA New England cohort is incomplete or has unexpected states")
    return cohort


def extract_pdf_text(pdf_path: Path) -> str:
    """Use Poppler's layout-preserving extraction for the ruled FAA table."""
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except FileNotFoundError as exc:
        raise FAAError("pdftotext is required to extract the FAA table") from exc
    except subprocess.TimeoutExpired as exc:
        raise FAAError("FAA PDF text extraction timed out") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise FAAError("FAA PDF text extraction failed")
    return result.stdout


def publish_faa_snapshot(
    client: httpx.Client,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    retrieved_at: datetime | None = None,
    text_extractor: Callable[[Path], str] = extract_pdf_text,
) -> dict[str, object]:
    """Publish the raw official PDF and parsed cohort, then replace the pointer."""
    content = fetch_faa_pdf(client)
    retrieval_time = retrieved_at or datetime.now(timezone.utc)
    if retrieval_time.tzinfo is None:
        raise ValueError("retrieved_at must include a timezone")
    retrieval_time = retrieval_time.astimezone(timezone.utc)

    snapshot_root = data_root / "snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=".staging-", dir=snapshot_root))
    pointer_temp = data_root / f".current-{uuid4().hex}.json"
    try:
        pdf_path = staging_dir / "source.pdf"
        _write_bytes(pdf_path, content)
        cohort = parse_faa_cohort(text_extractor(pdf_path))
        content_sha256 = hashlib.sha256(content).hexdigest()
        identity = hashlib.sha256(f"faa:cy2024:{content_sha256}".encode("ascii")).hexdigest()
        snapshot_id = f"faa-{identity}"
        metadata: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "source": {
                "name": "FAA CY2024 commercial-service enplanements by rank",
                "url": FAA_URL,
            },
            "retrieved_at_utc": retrieval_time.isoformat().replace("+00:00", "Z"),
            "byte_count": len(content),
            "content_sha256": content_sha256,
            "raw_pdf": "source.pdf",
            "validation_status": "accepted",
            "table_row_count": EXPECTED_TABLE_ROWS,
            "cohort_rule": {"region_office": "NE", "states": sorted(NEW_ENGLAND_STATES)},
            "cohort_count": len(cohort),
            "cohort": [asdict(airport) for airport in cohort],
        }
        _write_json(staging_dir / "manifest.json", metadata)

        final_dir = snapshot_root / snapshot_id
        if final_dir.exists():
            existing_pdf = final_dir / "source.pdf"
            existing_manifest = final_dir / "manifest.json"
            if (
                not existing_pdf.is_file()
                or not existing_manifest.is_file()
                or hashlib.sha256(existing_pdf.read_bytes()).hexdigest() != content_sha256
            ):
                raise FAAError("existing FAA snapshot does not match its content identity")
            try:
                published_metadata = json.loads(existing_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FAAError("existing FAA snapshot manifest is invalid") from exc
            if (
                published_metadata.get("snapshot_id") != snapshot_id
                or published_metadata.get("content_sha256") != content_sha256
                or published_metadata.get("validation_status") != "accepted"
            ):
                raise FAAError("existing FAA snapshot manifest is invalid")
        else:
            os.replace(staging_dir, final_dir)
            published_metadata = metadata

        pointer = {
            "snapshot_id": snapshot_id,
            "manifest": f"snapshots/{snapshot_id}/manifest.json",
        }
        _write_json(pointer_temp, pointer)
        os.replace(pointer_temp, data_root / "current.json")
        return published_metadata
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        pointer_temp.unlink(missing_ok=True)


def _write_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the FAA CY2024 cohort snapshot")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    if not args.refresh:
        parser.error("--refresh is required")
    try:
        with httpx.Client() as client:
            metadata = publish_faa_snapshot(client)
    except (FAAError, OSError) as exc:
        print(f"FAA refresh failed: {exc}", file=sys.stderr)
        return 1
    print(metadata["snapshot_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
