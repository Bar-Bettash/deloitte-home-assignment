from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import app.sources.faa as faa_source
import httpx
import pytest
from app.sources.faa import (
    FAA_PRELIMINARY_2025_URL,
    FAA_URL,
    FAAError,
    parse_faa_cohort,
    parse_faa_preliminary_2025_cohort,
    publish_faa_snapshot,
    stage_faa_preliminary_2025,
    verify_faa_snapshot,
)

EXPECTED_NEW_ENGLAND = {
    "BDL", "HVN", "PWM", "BGR", "PQI", "RKD", "BHB", "AUG", "BOS", "ACK", "ORH",
    "MVY", "HYA", "PVC", "MHT", "PSM", "LEB", "PVD", "WST", "BID", "BTV", "RUT",
}
EXPECTED_PRELIMINARY_2025_NEW_ENGLAND = EXPECTED_NEW_ENGLAND | {"EWB"}
FAKE_PDF = b"%PDF-1.6\nunit-test-content\n%%EOF\n"


def test_parser_reproduces_exact_22_airport_cohort_from_table_text() -> None:
    cohort = parse_faa_cohort(_faa_table_text())

    assert len(cohort) == 22
    assert {airport.locid for airport in cohort} == EXPECTED_NEW_ENGLAND
    assert {airport.state for airport in cohort} == {"CT", "ME", "MA", "NH", "RI", "VT"}
    assert all(airport.region_office == "NE" for airport in cohort)


def test_preliminary_2025_parser_uses_year_correct_fields_for_exact_23_airports() -> None:
    cohort = parse_faa_preliminary_2025_cohort(_faa_preliminary_2025_text())

    assert {airport.locid for airport in cohort} == EXPECTED_PRELIMINARY_2025_NEW_ENGLAND
    bos = next(airport for airport in cohort if airport.locid == "BOS")
    ewb = next(airport for airport in cohort if airport.locid == "EWB")
    assert (bos.cy2025_enplanements, bos.cy2024_enplanements) == (21_021_153, 21_090_721)
    assert (ewb.cy2025_enplanements, ewb.cy2024_enplanements) == (3_145, 2_060)


@pytest.mark.parametrize("missing", ["EWB", "WST"])
def test_preliminary_2025_parser_rejects_incomplete_cohort(missing: str) -> None:
    with pytest.raises(FAAError, match="cohort is incomplete"):
        parse_faa_preliminary_2025_cohort(_faa_preliminary_2025_text(drop_locid=missing))


def test_publishes_raw_pdf_and_acquisition_manifest(tmp_path) -> None:
    data_root = tmp_path / "raw" / "faa"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=FAKE_PDF,
            request=request,
        )

    def extractor(path) -> str:
        assert path.read_bytes() == FAKE_PDF
        return _faa_table_text()

    metadata = publish_faa_snapshot(
        httpx.Client(transport=httpx.MockTransport(handler)),
        data_root=data_root,
        retrieved_at=datetime(2026, 9, 26, 20, 30, tzinfo=timezone.utc),
        text_extractor=extractor,
    )

    assert [str(request.url) for request in requests] == [FAA_URL]
    pointer = json.loads((data_root / "current.json").read_text())
    snapshot_dir = data_root / "snapshots" / metadata["snapshot_id"]
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    assert (snapshot_dir / "source.pdf").read_bytes() == FAKE_PDF
    assert pointer["manifest"] == f"snapshots/{metadata['snapshot_id']}/manifest.json"
    assert manifest == metadata
    assert metadata["source"]["url"] == FAA_URL
    assert metadata["retrieved_at_utc"] == "2026-09-26T20:30:00Z"
    assert metadata["content_sha256"] == hashlib.sha256(FAKE_PDF).hexdigest()
    assert metadata["byte_count"] == len(FAKE_PDF)
    assert metadata["cohort_count"] == 22
    assert {row["locid"] for row in metadata["cohort"]} == EXPECTED_NEW_ENGLAND
    assert metadata["validation_status"] == "accepted"


def test_stages_and_verifies_preliminary_2025_without_promoting_pointer(
    tmp_path, monkeypatch, capsys
) -> None:
    data_root = tmp_path / "raw" / "faa"
    (data_root).mkdir(parents=True)
    (data_root / "current.json").write_text('{"snapshot_id":"legacy"}\n')

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == FAA_PRELIMINARY_2025_URL
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=FAKE_PDF)

    metadata = stage_faa_preliminary_2025(
        httpx.Client(transport=httpx.MockTransport(handler)), data_root=data_root,
        retrieved_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
        text_extractor=lambda _: _faa_preliminary_2025_text(),
    )
    qualification = tmp_path / "qualification.json"
    qualification.write_text(json.dumps({"sources": {"faa": {
        "publication_status": "preliminary", "bytes": len(FAKE_PDF),
        "url": FAA_PRELIMINARY_2025_URL,
        "sha256": hashlib.sha256(FAKE_PDF).hexdigest(),
        "counts": {"table_rows": 515, "unique_locids": 515, "unique_ranks": 515,
                   "new_england_airports": 23},
        "new_england_locids": sorted(EXPECTED_PRELIMINARY_2025_NEW_ENGLAND),
        "validation": {"qualified": True},
    }}}))

    verified = verify_faa_snapshot(
        metadata["snapshot_id"], qualification, data_root=data_root,
        text_extractor=lambda _: _faa_preliminary_2025_text(),
    )
    assert verified == metadata
    assert json.loads((data_root / "current.json").read_text()) == {"snapshot_id": "legacy"}
    monkeypatch.setattr(faa_source, "verify_faa_snapshot", lambda *_, **__: metadata)
    assert faa_source.main(["--verify-only", "--snapshot-id", metadata["snapshot_id"],
                            "--qualification", str(qualification)]) == 0
    assert metadata["snapshot_id"] in capsys.readouterr().out

    snapshot_dir = data_root / "snapshots" / metadata["snapshot_id"]
    manifest_path = snapshot_dir / "manifest.json"
    mutated = json.loads(manifest_path.read_text())
    mutated["cohort"][0]["cy2025_enplanements"] += 1
    manifest_path.write_text(json.dumps(mutated))
    with pytest.raises(FAAError, match="does not match"):
        verify_faa_snapshot(
            metadata["snapshot_id"], qualification, data_root=data_root,
            text_extractor=lambda _: _faa_preliminary_2025_text(),
        )
    manifest_path.write_text(json.dumps(metadata))
    (snapshot_dir / "source.pdf").write_bytes(b"tampered")
    with pytest.raises(FAAError, match="does not match"):
        verify_faa_snapshot(
            metadata["snapshot_id"], qualification, data_root=data_root,
            text_extractor=lambda _: _faa_preliminary_2025_text(),
        )


@pytest.mark.parametrize(
    "text",
    [
        "not the official table",
        lambda: _faa_table_text(drop_locid="RUT"),
        lambda: _faa_table_text(duplicate_locid="BOS"),
    ],
)
def test_rejects_missing_markers_incomplete_cohort_or_duplicate_rows(text) -> None:
    candidate = text() if callable(text) else text
    with pytest.raises(FAAError):
        parse_faa_cohort(candidate)


def _faa_table_text(*, drop_locid: str | None = None, duplicate_locid: str | None = None) -> str:
    expected = [
        ("CT", "BDL"), ("CT", "HVN"), ("ME", "PWM"), ("ME", "BGR"),
        ("ME", "PQI"), ("ME", "RKD"), ("ME", "BHB"), ("ME", "AUG"),
        ("MA", "BOS"), ("MA", "ACK"), ("MA", "ORH"), ("MA", "MVY"),
        ("MA", "HYA"), ("MA", "PVC"), ("NH", "MHT"), ("NH", "PSM"),
        ("NH", "LEB"), ("RI", "PVD"), ("RI", "WST"), ("RI", "BID"),
        ("VT", "BTV"), ("VT", "RUT"),
    ]
    if drop_locid is not None:
        expected = [item for item in expected if item[1] != drop_locid]
    rows = []
    for rank, (state, locid) in enumerate(expected, start=1):
        chosen_locid = duplicate_locid if duplicate_locid and rank == 1 else locid
        rows.append(_table_row(rank, "NE", state, chosen_locid))
    for rank in range(len(rows) + 1, 514):
        rows.append(_table_row(rank, "WP", "CA", f"{rank:03d}"))
    return "\n".join(
        [
            "Source: CY2024 ACAIS",
            "Cy2024 Enplanements at All Commercial Service Airports (by Rank)",
            *rows,
            "Nonprimary Commercial",
            "119 Service",
            "394 Total Primary Airports",
            "Page 11 of 11",
        ]
    )


def _table_row(rank: int, region: str, state: str, locid: str) -> str:
    return (
        f"{rank:4d} {region} {state} {locid} City Airport Name "
        f"P N {rank:,} {max(rank - 1, 0):,} 1.00%"
    )


def _faa_preliminary_2025_text(*, drop_locid: str | None = None) -> str:
    airports = [
        ("MA", "BOS", 21_021_153, 21_090_721),
        *[(state, locid, 10_000, 9_000) for state, locid in (
            ("CT", "BDL"), ("CT", "HVN"), ("ME", "PWM"), ("ME", "BGR"),
            ("ME", "PQI"), ("ME", "RKD"), ("ME", "BHB"), ("ME", "AUG"),
            ("MA", "ACK"), ("MA", "ORH"), ("MA", "MVY"), ("MA", "HYA"),
            ("MA", "PVC"), ("NH", "MHT"), ("NH", "PSM"), ("NH", "LEB"),
            ("RI", "PVD"), ("RI", "WST"), ("RI", "BID"), ("VT", "BTV"),
            ("VT", "RUT"),
        )],
        ("MA", "EWB", 3_145, 2_060),
    ]
    airports = [airport for airport in airports if airport[1] != drop_locid]
    rows = [
        _table_row_with_values(rank, "NE", state, locid, cy25, cy24)
        for rank, (state, locid, cy25, cy24) in enumerate(airports, start=1)
    ]
    for rank in range(len(rows) + 1, 516):
        rows.append(_table_row_with_values(rank, "WP", "CA", f"{rank:03d}", rank, rank - 1))
    return "\n".join([
        "Preliminary CY2025 Enplanements at All Commercial Service Airports (by Rank)",
        "July 8, 2026", *rows, "Nonprimary Commercial", "114", "Service", "401",
        "Total Primary Airports", "Page 23 of 23",
    ])


def _table_row_with_values(
    rank: int, region: str, state: str, locid: str, current: int, previous: int
) -> str:
    return (
        f"{rank:4d} {region} {state} {locid} City Airport Name "
        f"P N {current:,} {previous:,} 1.00%"
    )


def test_verify_only_failure_is_labelled_verification(tmp_path, capsys) -> None:
    qualification = tmp_path / "qualification.json"
    qualification.write_text("{}")
    result = faa_source.main(["--verify-only", "--snapshot-id", "faa-" + "0" * 64,
                              "--qualification", str(qualification), "--data-root", str(tmp_path)])
    assert result == 1
    assert capsys.readouterr().err.startswith("FAA verification failed:")


def _pdf_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_faa_pdf_returns_complete_pdf() -> None:
    client = _pdf_client(lambda request: httpx.Response(200, headers={"content-type": "application/pdf; qs=1"}, content=FAKE_PDF))
    assert faa_source.fetch_faa_pdf(client, "https://faa.test/cohort.pdf") == FAKE_PDF


@pytest.mark.parametrize(
    "status, content_type, body, message",
    [
        (404, "application/pdf", FAKE_PDF, "request failed"),
        (200, "text/html", b"<html>", "not a PDF"),
        (200, "application/pdf", b"%PDF-" + b"0" * faa_source.MAX_PDF_BYTES, "5 MiB limit"),
        (200, "application/pdf", b"%PDF-1.6\ntruncated", "not a complete PDF"),
        (200, "application/pdf", b"<html>%%EOF", "not a complete PDF"),
    ],
)
def test_fetch_faa_pdf_fails_closed(status, content_type, body, message) -> None:
    client = _pdf_client(lambda request: httpx.Response(status, headers={"content-type": content_type}, content=body))
    with pytest.raises(FAAError, match=message):
        faa_source.fetch_faa_pdf(client, "https://faa.test/cohort.pdf")


@pytest.mark.parametrize(
    "error, message",
    [(httpx.ReadTimeout, "timed out"), (httpx.ConnectError, "request failed")],
)
def test_fetch_faa_pdf_maps_transport_errors(error, message) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("offline", request=request)

    with pytest.raises(FAAError, match=message):
        faa_source.fetch_faa_pdf(_pdf_client(handler), "https://faa.test/cohort.pdf")
