from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import httpx
import pytest
from app.sources.faa import FAA_URL, FAAError, parse_faa_cohort, publish_faa_snapshot

EXPECTED_NEW_ENGLAND = {
    "BDL", "HVN", "PWM", "BGR", "PQI", "RKD", "BHB", "AUG", "BOS", "ACK", "ORH",
    "MVY", "HYA", "PVC", "MHT", "PSM", "LEB", "PVD", "WST", "BID", "BTV", "RUT",
}
FAKE_PDF = b"%PDF-1.6\nunit-test-content\n%%EOF\n"


def test_parser_reproduces_exact_22_airport_cohort_from_table_text() -> None:
    cohort = parse_faa_cohort(_faa_table_text())

    assert len(cohort) == 22
    assert {airport.locid for airport in cohort} == EXPECTED_NEW_ENGLAND
    assert {airport.state for airport in cohort} == {"CT", "ME", "MA", "NH", "RI", "VT"}
    assert all(airport.region_office == "NE" for airport in cohort)


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
