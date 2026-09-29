from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from app.sources import aip
from app.sources.aip import (
    EXPECTED_RECORDS,
    MAX_MEMBER_BYTES,
    AIPError,
    parse_aip_workbook,
    publish_aip_snapshot,
)

NEW_ENGLAND_COHORT = {
    "ACK", "AUG", "BDL", "BGR", "BHB", "BID", "BOS", "BTV", "EWB", "HYA", "HVN",
    "LEB", "MHT", "MVY", "ORH", "PQI", "PSM", "PVC", "PVD", "PWM", "RKD", "RUT", "WST",
}


def test_parses_and_validates_fy2025_grants(tmp_path: Path) -> None:
    workbook = _workbook(tmp_path / "aip.xlsx")

    records = parse_aip_workbook(workbook)

    assert len(records) == EXPECTED_RECORDS
    assert records[0].grant_number == "3-00-0000-001-2025"
    assert records[0].total_amount == Decimal(1)
    assert sum(records[0].funding_components) == records[0].total_amount


@pytest.mark.parametrize("problem", ["wrong-year", "duplicate", "bad-components"])
def test_rejects_invalid_grant_scope_identity_or_amounts(tmp_path: Path, problem: str) -> None:
    with pytest.raises(AIPError):
        parse_aip_workbook(_workbook(tmp_path / "bad.xlsx", problem=problem))


def test_rejects_wrong_requested_fiscal_year(tmp_path: Path) -> None:
    with pytest.raises(AIPError, match="fiscal scope"):
        parse_aip_workbook(_workbook(tmp_path / "aip.xlsx"), fiscal_year=2024)


@pytest.mark.parametrize("problem", ["unsafe", "oversized"])
def test_rejects_unsafe_or_oversized_zip_members(tmp_path: Path, problem: str) -> None:
    workbook = tmp_path / "bad.xlsx"
    with zipfile.ZipFile(workbook, "w", zipfile.ZIP_DEFLATED) as archive:
        name = "../escape.xml" if problem == "unsafe" else "xl/worksheets/sheet1.xml"
        archive.writestr(name, b"x" if problem == "unsafe" else b"x" * (MAX_MEMBER_BYTES + 1))
    with pytest.raises(AIPError, match="unsafe|expanded-size"):
        parse_aip_workbook(workbook)


def test_qualified_workbook_reconciles_new_england_awards() -> None:
    source = Path("/private/tmp/deloitte-aip-qualification/FY_2025_AIP_Grants.xlsx")
    if not source.is_file():
        pytest.skip("qualification input is external to the repository")
    records = parse_aip_workbook(source)
    cohort = [record for record in records if record.locid in NEW_ENGLAND_COHORT]
    assert len(cohort) == 66
    assert sum((record.total_amount for record in cohort), Decimal()) == Decimal(259112386)
    assert sum((record.total_amount for record in records), Decimal()) == Decimal("9044165010.08")


def test_stages_immutable_snapshot_without_accepted_pointer(tmp_path: Path) -> None:
    workbook = _workbook(tmp_path / "aip.xlsx")
    data_root = tmp_path / "raw" / "aip"
    metadata = publish_aip_snapshot(
        workbook,
        data_root=data_root,
        imported_at=datetime(2026, 9, 27, 12, tzinfo=timezone.utc),
    )

    final = data_root / "snapshots" / str(metadata["snapshot_id"])
    manifest = json.loads((final / "manifest.json").read_text())
    assert (final / "source.xlsx").read_bytes() == workbook.read_bytes()
    assert manifest == metadata
    assert metadata["content_sha256"] == hashlib.sha256(workbook.read_bytes()).hexdigest()
    assert metadata["fiscal_year"] == 2025
    assert metadata["validation_status"] == "staged"
    assert not (data_root / "current.json").exists()

    (final / "source.xlsx").write_bytes(b"corrupt")
    with pytest.raises(AIPError, match="content identity"):
        publish_aip_snapshot(workbook, data_root=data_root)


def test_cli_stages_then_verifies_saved_snapshot_read_only(tmp_path: Path, monkeypatch, capsys) -> None:
    workbook = _workbook(tmp_path / "input.xlsx")
    content = workbook.read_bytes()
    data_root = tmp_path / "raw" / "aip"
    monkeypatch.setattr(aip, "fetch_qualified_workbook", lambda: content)
    assert aip.main(["--refresh", "--stage", "--year", "2025", "--data-root", str(data_root)]) == 0
    snapshot_id = capsys.readouterr().out.strip()
    final = data_root / "snapshots" / snapshot_id
    manifest = json.loads((final / "manifest.json").read_text())
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({"sources": {"faa_aip_fy2025_awards": {
        "source": {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)},
        "workbook": {"data_rows": EXPECTED_RECORDS, "unique_grant_numbers": EXPECTED_RECORDS},
        "new_england_cohort": {"listed_grants": 0, "total_amount_usd": 0},
    }}}))
    before = ((final / "source.xlsx").read_bytes(), (final / "manifest.json").read_bytes())

    assert aip.main(["--verify-only", "--snapshot-id", snapshot_id, "--qualification", str(qualification), "--data-root", str(data_root)]) == 0
    assert capsys.readouterr().out.strip() == snapshot_id
    assert before == ((final / "source.xlsx").read_bytes(), (final / "manifest.json").read_bytes())
    assert manifest["validation_status"] == "staged"
    assert not (data_root / "current.json").exists()

    (final.parent / "source.xlsx").write_bytes(content)
    tampered = {**manifest, "source_file": "../source.xlsx"}
    (final / "manifest.json").write_text(json.dumps(tampered))
    with pytest.raises(AIPError, match="qualification"):
        aip.verify_aip_snapshot(snapshot_id, qualification, data_root=data_root)
    (final / "manifest.json").write_text(json.dumps(manifest))

    tampered = {**manifest, "byte_count": manifest["byte_count"] + 1}
    (final / "manifest.json").write_text(json.dumps(tampered))
    with pytest.raises(AIPError, match="qualification"):
        aip.verify_aip_snapshot(snapshot_id, qualification, data_root=data_root)
    (final / "manifest.json").write_text(json.dumps(manifest))

    wrong_id = "aip-" + "0" * 64
    wrong = data_root / "snapshots" / wrong_id
    wrong.mkdir()
    (wrong / "source.xlsx").write_bytes(content)
    (wrong / "manifest.json").write_text(json.dumps({**manifest, "snapshot_id": wrong_id}))
    with pytest.raises(AIPError, match="qualification"):
        aip.verify_aip_snapshot(wrong_id, qualification, data_root=data_root)


def test_cli_rejects_incomplete_refresh_contract() -> None:
    with pytest.raises(SystemExit):
        aip.main(["--refresh", "--year", "2025"])


def _workbook(path: Path, *, problem: str | None = None) -> Path:
    headers = (
        "State", "City", "Worksite", "LocID", "Sponsor", "Grant Number", "Award Date",
        "Entitlement", "Discretionary", "Discretionary Noise", "Discretionary MAP",
        "Supp Discretionary", "CARES", "Econ Recover", "AIG", "FCT", "ATRM",
        "Total Amount", "Project Summary",
    )
    strings = list(headers) + ["MA", "City", "Airport", "AAA", "Sponsor", "Project"]
    grants = [f"3-00-{number // 1000:04d}-{number % 1000:03d}-2025" for number in range(1, EXPECTED_RECORDS + 1)]
    if problem == "wrong-year":
        grants[-1] = grants[-1][:-4] + "2024"
    if problem == "duplicate":
        grants[-1] = grants[0]
    strings.extend(grants)
    cells = lambda row, values: "".join(
        f'<c r="{chr(65 + column)}{row}" t="s"><v>{value}</v></c>'
        for column, value in enumerate(values)
    )
    rows = [f'<row r="3">{cells(3, range(19))}</row>']
    for number in range(EXPECTED_RECORDS):
        amounts = ["1", *("0" for _ in range(9)), "2" if problem == "bad-components" and number == 0 else "1"]
        text_indexes = [19, 20, 21, 22, 23, 25 + number, None, *([None] * 11), 24]
        row = number + 4
        encoded = []
        for column, value in enumerate(text_indexes):
            kind = ' t="s"' if value is not None else ""
            stored = value if value is not None else [45600, *amounts][column - 6]
            encoded.append(f'<c r="{chr(65 + column)}{row}"{kind}><v>{stored}</v></c>')
        content = "".join(encoded)
        rows.append(f'<row r="{row}">{content}</row>')
    shared = "".join(f"<si><t>{value}</t></si>" for value in strings)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{shared}</sst>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(rows)}</sheetData></worksheet>')
    return path


XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PACKAGED_AIP = next((Path(__file__).resolve().parents[1] / "data" / "raw" / "aip" / "snapshots").glob("aip-*/source.xlsx"))


def _serve(monkeypatch, handler) -> None:
    real_client = aip.httpx.Client
    monkeypatch.setattr(aip.httpx, "Client", lambda: real_client(transport=aip.httpx.MockTransport(handler)))


def test_fetch_qualified_workbook_returns_the_qualified_bytes(monkeypatch) -> None:
    content = PACKAGED_AIP.read_bytes()
    _serve(monkeypatch, lambda request: aip.httpx.Response(200, headers={"content-type": XLSX_TYPE}, content=content))
    assert aip.fetch_qualified_workbook() == content


@pytest.mark.parametrize(
    "status, content_type, body, message",
    [
        (503, XLSX_TYPE, b"", "request failed"),
        (200, "text/html", b"<html>", "not an XLSX"),
        (200, XLSX_TYPE, b"x" * (aip.MAX_XLSX_BYTES + 1), "compressed-size limit"),
        (200, XLSX_TYPE, b"PK-not-the-qualified-workbook", "differs from the qualified source"),
    ],
)
def test_fetch_qualified_workbook_fails_closed(monkeypatch, status, content_type, body, message) -> None:
    _serve(monkeypatch, lambda request: aip.httpx.Response(status, headers={"content-type": content_type}, content=body))
    with pytest.raises(AIPError, match=message):
        aip.fetch_qualified_workbook()


def test_fetch_qualified_workbook_maps_transport_errors(monkeypatch) -> None:
    def handler(request):
        raise aip.httpx.ConnectError("offline", request=request)

    _serve(monkeypatch, handler)
    with pytest.raises(AIPError, match="request failed"):
        aip.fetch_qualified_workbook()


def test_cli_reports_one_line_failures_and_exits_nonzero(tmp_path: Path, monkeypatch, capsys) -> None:
    def fail() -> bytes:
        raise AIPError("FAA AIP workbook request failed")

    monkeypatch.setattr(aip, "fetch_qualified_workbook", fail)
    assert aip.main(["--refresh", "--stage", "--year", "2025", "--data-root", str(tmp_path)]) == 1
    assert capsys.readouterr().err == "AIP refresh failed: FAA AIP workbook request failed\n"

    snapshot_id = "aip-" + hashlib.sha256(b"faa-aip:fy2025:" + b"0" * 64).hexdigest()
    (tmp_path / "snapshots" / snapshot_id).mkdir(parents=True)
    (tmp_path / "snapshots" / snapshot_id / "manifest.json").write_text("{}")
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({"sources": {"faa_aip_fy2025_awards": {
        "source": {}, "workbook": {}, "new_england_cohort": {}}}}))
    args = ["--verify-only", "--snapshot-id", snapshot_id, "--qualification", str(qualification), "--data-root", str(tmp_path)]
    assert aip.main(args) == 1
    err = capsys.readouterr().err
    assert err.startswith("AIP verification failed:") and err.count("\n") == 1
    assert aip.main([*args[:4], str(tmp_path / "missing.json"), *args[5:]]) == 1
    assert capsys.readouterr().err.startswith("AIP verification failed:")
