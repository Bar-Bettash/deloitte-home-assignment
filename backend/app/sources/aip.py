"""Bounded parser for the official FY2025 FAA AIP grants workbook."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

import httpx

MAX_XLSX_BYTES = 1024 * 1024
MAX_MEMBER_BYTES = 3 * 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024
EXPECTED_RECORDS = 3707
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "raw" / "aip"
AIP_URL = "https://www.faa.gov/sites/faa.gov/files/2025-11/FY_2025_AIP_Grants.xlsx"
QUALIFIED_SHA256 = "1c16930f818879bc144d5d531c85f4dc1c3e992b08ceb937dadddc4a7484adcb"
QUALIFIED_BYTES = 430906
NEW_ENGLAND_COHORT = {
    "ACK", "AUG", "BDL", "BGR", "BHB", "BID", "BOS", "BTV", "EWB", "HYA", "HVN",
    "LEB", "MHT", "MVY", "ORH", "PQI", "PSM", "PVC", "PVD", "PWM", "RKD", "RUT", "WST",
}
HEADERS = (
    "State", "City", "Worksite", "LocID", "Sponsor", "Grant Number", "Award Date",
    "Entitlement", "Discretionary", "Discretionary Noise", "Discretionary MAP",
    "Supp Discretionary", "CARES", "Econ Recover", "AIG", "FCT", "ATRM",
    "Total Amount", "Project Summary",
)
COMPONENT_COLUMNS = tuple(range(7, 17))
_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_OFFICE_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_CELL_COLUMN = re.compile(r"([A-Z]+)\d+")
_CENT = Decimal("0.01")


class AIPError(RuntimeError):
    """The workbook could not produce validated FY2025 grant records."""


@dataclass(frozen=True, slots=True)
class AIPGrant:
    state: str
    city: str
    worksite: str
    locid: str
    sponsor: str
    grant_number: str
    award_date: date
    funding_components: tuple[Decimal, ...]
    total_amount: Decimal
    project_summary: str


def parse_aip_workbook(path: Path, *, fiscal_year: int = 2025) -> list[AIPGrant]:
    """Return validated grants from the exact fiscal-year worksheet."""
    if fiscal_year != 2025:
        raise AIPError("AIP workbook fiscal scope must be FY2025")
    try:
        if path.stat().st_size > MAX_XLSX_BYTES:
            raise AIPError("AIP workbook exceeds the compressed-size limit")
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            _validate_members(members)
            workbook = _read_xml(archive, "xl/workbook.xml")
            relationships = _read_xml(archive, "xl/_rels/workbook.xml.rels")
            sheet_path = _data_sheet_path(workbook, relationships)
            shared = _shared_strings(archive)
            sheet = _read_xml(archive, sheet_path)
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError) as exc:
        raise AIPError("AIP workbook package is invalid") from exc

    rows = sheet.findall(".//x:sheetData/x:row", _NS)
    header = next((row for row in rows if row.get("r") == "3"), None)
    if header is None or tuple(_row_values(header, shared)) != HEADERS:
        raise AIPError("AIP workbook has an unexpected header")
    try:
        records = [_grant(_row_values(row, shared), fiscal_year) for row in rows if int(row.get("r", "0")) > 3]
    except (IndexError, ValueError) as exc:
        raise AIPError("AIP worksheet contains an invalid encoded value") from exc
    if len(records) != EXPECTED_RECORDS:
        raise AIPError("AIP workbook does not contain 3,707 grant records")
    identities = [record.grant_number for record in records]
    if len(set(identities)) != len(identities):
        raise AIPError("AIP workbook contains duplicate grant numbers")
    return records


def publish_aip_snapshot(
    workbook: Path,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    imported_at: datetime | None = None,
) -> dict[str, object]:
    """Stage an immutable FY2025 snapshot without changing an accepted pointer."""
    import_time = imported_at or datetime.now(timezone.utc)
    if import_time.tzinfo is None:
        raise ValueError("imported_at must include a timezone")
    try:
        content = workbook.read_bytes()
    except OSError as exc:
        raise AIPError("AIP workbook is unavailable") from exc
    if len(content) > MAX_XLSX_BYTES:
        raise AIPError("AIP workbook exceeds the compressed-size limit")
    content_sha256 = hashlib.sha256(content).hexdigest()
    snapshot_id = "aip-" + hashlib.sha256(
        f"faa-aip:fy2025:{content_sha256}".encode("ascii")
    ).hexdigest()
    snapshot_root = data_root / "snapshots"
    snapshot_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=snapshot_root))
    try:
        source = staging / "source.xlsx"
        _write_bytes(source, content)
        records = parse_aip_workbook(source)
        cohort = [record for record in records if record.locid in NEW_ENGLAND_COHORT]
        metadata: dict[str, object] = {
            "snapshot_id": snapshot_id,
            "source": {"name": "FY 2025 FAA Grant Detail Report", "url": AIP_URL},
            "imported_at_utc": import_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "fiscal_year": 2025,
            "source_file": "source.xlsx",
            "byte_count": len(content),
            "content_sha256": content_sha256,
            "row_count": len(records),
            "total_amount_usd": str(sum((record.total_amount for record in records), Decimal())),
            "grant_identity": "Grant Number",
            "amount_rule": "funding components equal Total Amount after cent normalization",
            "new_england_awards": len(cohort),
            "new_england_total_usd": str(sum((record.total_amount for record in cohort), Decimal())),
            "validation_status": "staged",
        }
        _write_json(staging / "manifest.json", metadata)
        final = snapshot_root / snapshot_id
        if final.exists():
            return _validate_existing_snapshot(final, snapshot_id, content_sha256)
        os.replace(staging, final)
        return metadata
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _validate_existing_snapshot(final: Path, snapshot_id: str, checksum: str) -> dict[str, object]:
    try:
        source = final / "source.xlsx"
        manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
    except (OSError, json.JSONDecodeError) as exc:
        raise AIPError("existing AIP snapshot is invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("snapshot_id") != snapshot_id
        or manifest.get("content_sha256") != checksum
        or manifest.get("fiscal_year") != 2025
        or manifest.get("validation_status") != "staged"
        or actual != checksum
    ):
        raise AIPError("existing AIP snapshot does not match its content identity")
    return manifest


def _write_bytes(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json(path: Path, value: dict[str, object]) -> None:
    _write_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def fetch_qualified_workbook() -> bytes:
    """Fetch the qualified official workbook once, without retries."""
    try:
        with httpx.Client().stream("GET", AIP_URL, timeout=60.0) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                raise AIPError("FAA AIP response is not an XLSX workbook")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_XLSX_BYTES:
                    raise AIPError("FAA AIP response exceeds the compressed-size limit")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise AIPError("FAA AIP workbook request failed") from exc
    content = b"".join(chunks)
    if len(content) != QUALIFIED_BYTES or hashlib.sha256(content).hexdigest() != QUALIFIED_SHA256:
        raise AIPError("FAA AIP workbook differs from the qualified source")
    return content


def verify_aip_snapshot(snapshot_id: str, qualification: Path, *, data_root: Path) -> dict[str, object]:
    """Re-parse one saved snapshot and reconcile it to durable qualification facts."""
    if re.fullmatch(r"aip-[0-9a-f]{64}", snapshot_id) is None:
        raise AIPError("AIP snapshot ID is invalid")
    directory = data_root / "snapshots" / snapshot_id
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        qualified = json.loads(qualification.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AIPError("AIP verification metadata is invalid") from exc
    try:
        expected = qualified["sources"]["faa_aip_fy2025_awards"]
        source_expected = expected["source"]
        workbook_expected = expected["workbook"]
        cohort_expected = expected["new_england_cohort"]
        source = directory / "source.xlsx"
    except (KeyError, TypeError) as exc:
        raise AIPError("AIP qualification contract is incomplete") from exc
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    derived_id = "aip-" + hashlib.sha256(
        f"faa-aip:fy2025:{checksum}".encode("ascii")
    ).hexdigest()
    records = parse_aip_workbook(source)
    cohort = [record for record in records if record.locid in NEW_ENGLAND_COHORT]
    cohort_total = sum((record.total_amount for record in cohort), Decimal())
    valid = (
        manifest.get("snapshot_id") == snapshot_id == derived_id
        and manifest.get("source_file") == "source.xlsx"
        and manifest.get("validation_status") == "staged"
        and manifest.get("fiscal_year") == 2025
        and manifest.get("content_sha256") == checksum == source_expected.get("sha256")
        and manifest.get("byte_count") == source.stat().st_size == source_expected.get("bytes")
        and len(records) == manifest.get("row_count") == workbook_expected.get("data_rows")
        and len({record.grant_number for record in records}) == workbook_expected.get("unique_grant_numbers")
        and len(cohort) == manifest.get("new_england_awards") == cohort_expected.get("listed_grants")
        and cohort_total == Decimal(str(cohort_expected.get("total_amount_usd")))
        and manifest.get("new_england_total_usd") == str(cohort_total)
    )
    if not valid:
        raise AIPError("AIP snapshot does not match its qualification")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage or verify the qualified FY2025 AIP workbook")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--refresh", action="store_true")
    modes.add_argument("--verify-only", action="store_true")
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--year", type=int)
    parser.add_argument("--snapshot-id")
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args(argv)
    if args.refresh:
        if not args.stage or args.year != 2025 or args.snapshot_id or args.qualification:
            parser.error("refresh requires only --stage --year 2025")
        content = fetch_qualified_workbook()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
            handle.write(content)
            temporary = Path(handle.name)
        try:
            result = publish_aip_snapshot(temporary, data_root=args.data_root)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        if args.stage or args.year or not args.snapshot_id or args.qualification is None:
            parser.error("verify-only requires --snapshot-id and --qualification")
        result = verify_aip_snapshot(args.snapshot_id, args.qualification, data_root=args.data_root)
    print(result["snapshot_id"])
    return 0


def _validate_members(members: list[zipfile.ZipInfo]) -> None:
    if len(members) > 64 or len({member.filename for member in members}) != len(members):
        raise AIPError("AIP workbook contains duplicate ZIP members")
    expanded = 0
    for member in members:
        parts = PurePosixPath(member.filename).parts
        if member.filename.startswith("/") or ".." in parts or member.flag_bits & 1:
            raise AIPError("AIP workbook contains an unsafe ZIP member")
        if member.file_size > MAX_MEMBER_BYTES:
            raise AIPError("AIP workbook member exceeds the expanded-size limit")
        expanded += member.file_size
    if expanded > MAX_EXPANDED_BYTES:
        raise AIPError("AIP workbook exceeds the total expanded-size limit")


def _read_xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    info = archive.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise AIPError("AIP XML member exceeds the expanded-size limit")
    return ET.fromstring(archive.read(info))


def _data_sheet_path(workbook: ET.Element, relationships: ET.Element) -> str:
    sheets = workbook.findall(".//x:sheets/x:sheet", _NS)
    if len(sheets) != 1 or sheets[0].get("name") != "Data":
        raise AIPError("AIP workbook must contain only the Data sheet")
    relation_id = sheets[0].get(_OFFICE_REL)
    target = next((item.get("Target") for item in relationships.findall("r:Relationship", _REL_NS) if item.get("Id") == relation_id), None)
    if target != "worksheets/sheet1.xml":
        raise AIPError("AIP Data sheet relationship is invalid")
    return "xl/worksheets/sheet1.xml"


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = _read_xml(archive, "xl/sharedStrings.xml")
    return ["".join(node.text or "" for node in item.findall(".//x:t", _NS)) for item in root.findall("x:si", _NS)]


def _row_values(row: ET.Element, shared: list[str]) -> list[str]:
    values = [""] * len(HEADERS)
    for cell in row.findall("x:c", _NS):
        match = _CELL_COLUMN.fullmatch(cell.get("r", ""))
        if match is None:
            raise AIPError("AIP worksheet contains an invalid cell reference")
        column = 0
        for char in match.group(1):
            column = column * 26 + ord(char) - 64
        if column > len(HEADERS):
            continue
        raw = cell.findtext("x:v", default="", namespaces=_NS)
        values[column - 1] = shared[int(raw)] if cell.get("t") == "s" else raw
    return values


def _grant(values: list[str], fiscal_year: int) -> AIPGrant:
    if len(values) != len(HEADERS) or re.fullmatch(rf"\d-\d{{2}}-[A-Z0-9]{{4}}-\d{{3}}-{fiscal_year}", values[5]) is None:
        raise AIPError("AIP grant is outside the requested fiscal scope")
    try:
        awarded = date(1899, 12, 30) + timedelta(days=int(Decimal(values[6])))
        components = tuple(Decimal(values[index] or "0").quantize(_CENT) for index in COMPONENT_COLUMNS)
        total = Decimal(values[17]).quantize(_CENT)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise AIPError("AIP grant contains an invalid date or amount") from exc
    if not date(2024, 10, 1) <= awarded <= date(2025, 9, 30):
        raise AIPError("AIP grant is outside the requested fiscal scope")
    if not all(amount.is_finite() for amount in (*components, total)) or sum(components) != total:
        raise AIPError("AIP grant funding components do not equal Total Amount")
    return AIPGrant(values[0], values[1], values[2], values[3].upper(), values[4], values[5], awarded, components, total, values[18])


if __name__ == "__main__":
    raise SystemExit(main())
