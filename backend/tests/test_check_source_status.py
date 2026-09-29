from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.sources.bundle import load_bundle
from app.sources.source_check import validate_source_check_payload
from scripts import check_source_status

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
QUALIFICATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/evidence/recent-source-qualification-20260927.json"
)
NOW = datetime(2026, 9, 27, 18, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def bundle():
    return load_bundle("annual-2025-r1", data_root=DATA_ROOT)


@pytest.fixture(scope="module")
def qualification():
    return json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def observations(bundle, qualification):
    sources = qualification["sources"]
    return {
        "datasf": {
            "proof_mode": "selected_content_hashes",
            "selected_partition_sha256": [item["sha256"] for item in sources["datasf"]["partitions"]],
            "newer_period_proof": "official_index_or_content_query",
            "newer_year": 2026, "newer_months": list(range(1, 9)),
            "newer_complete_years": [],
        },
        "t100": {
            "proof_mode": "selected_extracted_content_hashes",
            "selected_csv_sha256": {
                f"{item['year']}-{item['state']}": item["csv_sha256"]
                for item in sources["t100"]["partitions"]
            },
            "release_log_sha256": sources["t100"]["release_info_observation"]["response_sha256"],
            "newer_period_proof": "official_index_or_content_query",
            "newer_year": 2026, "newer_months": list(range(1, 7)),
            "newer_complete_years": [],
        },
        "ontime": {
            "proof_mode": "selected_extracted_content_hashes",
            "selected_csv_sha256": {
                str(item["month"]): item["csv_sha256"] for item in sources["ontime"]["partitions"]
            },
            "newer_period_proof": "official_index_or_content_query",
            "newer_year": 2026, "newer_months": list(range(1, 8)),
            "newer_complete_years": [],
        },
        "faa": {
            "proof_mode": "selected_content_hash",
            "selected_content_sha256": bundle.sources["faa"].content_sha256,
            "newer_period_proof": "official_index_or_content_query",
            "newer_year": None, "newer_months": [], "newer_complete_years": [],
        },
        "aip": {
            "proof_mode": "selected_content_hash",
            "selected_content_sha256": bundle.sources["aip"].content_sha256,
            "newer_period_proof": "official_index_or_content_query",
            "newer_year": None, "newer_months": [], "newer_complete_years": [],
        },
    }


def test_conclusive_selected_period_checks_admit_and_disclose_partial_years(
    bundle, qualification, observations
) -> None:
    receipt = check_source_status.build_receipt(
        bundle, qualification, observations, checked_at=NOW
    )

    assert receipt["admission_status"] == "admitted"
    assert receipt["bundle_id"] == bundle.bundle_id
    assert receipt["manifest_sha256"] == bundle.manifest_sha256
    assert [item["source"] for item in receipt["sources"]] == list(bundle.sources)
    partial = {
        item["source"]: item["newer_partial_year"] for item in receipt["sources"]
    }
    assert partial == {"aip": None, "datasf": 2026, "faa": None, "ontime": 2026, "t100": 2026}
    assert all(item["outcome"] == "current_for_scope" for item in receipt["sources"])
    readiness = validate_source_check_payload(bundle, receipt, now=NOW)
    assert readiness.bundle_id == bundle.bundle_id


@pytest.mark.parametrize("source", ["datasf", "t100", "ontime", "faa", "aip"])
def test_http_or_latest_period_without_selected_period_proof_is_inconclusive(
    bundle, qualification, observations, source
) -> None:
    observations[source] = {
        "http_status": 200,
        "latest_year": 2026,
        "newer_period_proof": "official_index_or_content_query",
        "newer_year": 2026,
        "newer_months": [1],
    }

    receipt = check_source_status.build_receipt(bundle, qualification, observations, checked_at=NOW)
    item = next(value for value in receipt["sources"] if value["source"] == source)

    assert receipt["admission_status"] == "blocked"
    assert item["outcome"] == "inconclusive"
    assert item["selected_period_revised"] is False


@pytest.mark.parametrize("source", ["datasf", "t100", "ontime", "faa", "aip"])
def test_changed_selected_period_content_blocks_as_revision(
    bundle, qualification, observations, source
) -> None:
    field = {
        "datasf": "selected_partition_sha256", "t100": "selected_csv_sha256",
        "ontime": "selected_csv_sha256",
        "faa": "selected_content_sha256", "aip": "selected_content_sha256",
    }[source]
    observations[source][field] = [] if source == "datasf" else (
        {} if source in {"t100", "ontime"} else "0" * 64
    )

    receipt = check_source_status.build_receipt(bundle, qualification, observations, checked_at=NOW)
    item = next(value for value in receipt["sources"] if value["source"] == source)

    assert receipt["admission_status"] == "blocked"
    assert item["outcome"] == "selected_period_revised"
    assert item["selected_period_revised"] is True


def test_unchanged_t100_release_page_does_not_hide_revised_selected_csv(
    bundle, qualification, observations
) -> None:
    observations["t100"]["selected_csv_sha256"]["2024-AK"] = "0" * 64

    receipt = check_source_status.build_receipt(
        bundle, qualification, observations, checked_at=NOW
    )
    item = next(value for value in receipt["sources"] if value["source"] == "t100")

    assert receipt["admission_status"] == "blocked"
    assert item["outcome"] == "selected_period_revised"
    assert item["selected_period_revised"] is True


@pytest.mark.parametrize(
    ("source", "update"),
    [
        ("t100", {"newer_year": 2026, "newer_months": list(range(1, 13))}),
        ("faa", {"newer_complete_years": [2026]}),
    ],
)
def test_newer_complete_period_blocks(bundle, qualification, observations, source, update) -> None:
    observations[source].update(update)

    receipt = check_source_status.build_receipt(
        bundle, qualification, observations, checked_at=NOW
    )
    item = next(value for value in receipt["sources"] if value["source"] == source)

    assert receipt["admission_status"] == "blocked"
    assert item["outcome"] == "newer_complete_available"
    assert item["newer_complete_available"] is True
    assert item["newer_partial_year"] is None


def test_intervening_complete_year_blocks_when_latest_year_is_partial(
    bundle, qualification, observations
) -> None:
    observations["t100"].update({
        "newer_year": 2027,
        "newer_months": [1],
        "newer_complete_years": [2026],
    })

    receipt = check_source_status.build_receipt(
        bundle, qualification, observations, checked_at=NOW
    )
    item = next(value for value in receipt["sources"] if value["source"] == "t100")

    assert receipt["admission_status"] == "blocked"
    assert item["outcome"] == "newer_complete_available"


def test_receipt_matches_strict_source_check_identity_schema(
    bundle, qualification, observations
) -> None:
    receipt = check_source_status.build_receipt(
        bundle, qualification, observations, checked_at=NOW
    )

    assert set(receipt) == {
        "schema_version", "receipt_type", "admission_status", "bundle_id",
        "manifest_sha256", "checked_at_utc", "sources",
    }
    assert receipt["checked_at_utc"] == "2026-09-27T18:00:00Z"
    assert all(set(item) == {
        "source", "snapshot_id", "manifest_sha256", "content_sha256", "years",
        "outcome", "newer_complete_available", "newer_partial_year",
        "selected_period_revised",
    } for item in receipt["sources"])


def test_observation_set_must_match_bundle_exactly(bundle, qualification, observations) -> None:
    observations.pop("faa")

    with pytest.raises(check_source_status.SourceStatusError, match="every bundle source"):
        check_source_status.build_receipt(bundle, qualification, observations, checked_at=NOW)


@pytest.mark.parametrize(
    "update",
    [
        {"newer_year": 2025},
        {"newer_year": None, "newer_months": [1]},
        {"newer_year": 2026, "newer_months": [2, 1]},
        {"newer_complete_years": [2025]},
    ],
)
def test_invalid_newer_period_shape_fails_closed(
    bundle, qualification, observations, update
) -> None:
    observations["t100"].update(update)

    with pytest.raises(check_source_status.SourceStatusError):
        check_source_status.build_receipt(bundle, qualification, observations, checked_at=NOW)


class FakeProbe:
    def __init__(self, observations) -> None:
        self.observations = observations

    def collect(self, bundle, qualification):
        return self.observations


def _cli_args(tmp_path: Path) -> list[str]:
    return [
        "--bundle", "annual-2025-r1", "--output", str(tmp_path / "receipt.json"),
        "--data-root", str(DATA_ROOT), "--qualification", str(QUALIFICATION_PATH),
    ]


def test_cli_writes_only_conclusively_admitted_receipt(tmp_path, observations) -> None:
    output = tmp_path / "receipt.json"

    result = check_source_status.main(_cli_args(tmp_path), probe=FakeProbe(observations))

    assert result == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["admission_status"] == "admitted"


def test_cli_block_preserves_existing_output(tmp_path, observations) -> None:
    output = tmp_path / "receipt.json"
    output.write_text("previous\n", encoding="utf-8")
    observations["faa"]["selected_content_sha256"] = "0" * 64

    with pytest.raises(SystemExit) as exc:
        check_source_status.main(_cli_args(tmp_path), probe=FakeProbe(observations))

    assert exc.value.code == 1
    assert output.read_text(encoding="utf-8") == "previous\n"


def test_official_index_helpers_parse_periods() -> None:
    html = "<td>T-100 Segment (All Carriers)</td><td>6/2026</td>"

    assert check_source_status._latest_period(html, "T-100 Segment (All Carriers)") == (2026, 6)
    assert check_source_status._newer_months(["202512", "202601", "202603"], 2025) == (
        2026, [1, 3], []
    )
    periods = [f"2026{month:02d}" for month in range(1, 13)] + ["202701"]
    assert check_source_status._newer_months(periods, 2025) == (2027, [1], [2026])
    assert check_source_status._complete_years_through(2027, 1, 2025) == [2026]


def test_t100_combined_scope_uses_older_domestic_or_international_release(
    qualification, monkeypatch
) -> None:
    probe = check_source_status.HttpProbeClient(client=object())
    monkeypatch.setattr(
        probe, "_download_t100_partition",
        lambda year, geography, fields: f"{year:04d}".ljust(64, "0"),
    )
    release = (
        b"T-100 Domestic Segment (All Carriers) 7/2026 "
        b"T-100 International Segment (All Carriers) 6/2026"
    )

    observed = probe._t100(qualification, release)

    assert observed["newer_year"] == 2026
    assert observed["newer_months"] == [1, 2, 3, 4, 5, 6]
    assert observed["newer_complete_years"] == []


@pytest.mark.parametrize("source", ["faa", "aip"])
def test_document_probe_fails_when_official_index_parser_proves_no_selected_edition(
    qualification, monkeypatch, source
) -> None:
    probe = check_source_status.HttpProbeClient(client=object())
    monkeypatch.setattr(probe, "_get", lambda url, maximum: b"unrelated official page")

    with pytest.raises(check_source_status.SourceStatusError, match="selected edition"):
        probe._document_probe(source, qualification)


@pytest.mark.parametrize("source", ["faa", "aip"])
def test_unchanged_old_document_blocks_when_index_lists_another_same_year_edition(
    qualification, monkeypatch, source
) -> None:
    probe = check_source_status.HttpProbeClient(client=object())
    if source == "faa":
        selected = qualification["sources"]["faa"]["url"]
        alternative = (
            "https://www.faa.gov/example/arp-cy2025-commercial-service-enplanements-final.pdf"
        )
    else:
        selected = qualification["sources"]["faa_aip_fy2025_awards"]["source"]["url"]
        alternative = "https://www.faa.gov/example/FY_2025_AIP_Grants_Revised.xlsx"
    index = f'<a href="{selected}">old</a><a href="{alternative}">current</a>'.encode()
    if source == "aip":
        landing = b'<a href="/airports/aip/grant_histories/2025">FY2025</a>'

        def get(url, maximum):
            if url == selected:
                return b"unchanged selected bytes"
            if url.rstrip("/").endswith("grant_histories"):
                return landing
            return index
    else:
        def get(url, maximum):
            return b"unchanged selected bytes" if url == selected else index
    monkeypatch.setattr(probe, "_get", get)

    observed = probe._document_probe(source, qualification)

    assert observed["proof_mode"] == "selected_edition_ambiguous_or_superseded"


def test_faa_newer_preliminary_calendar_year_is_complete_annual_period(
    qualification, monkeypatch
) -> None:
    selected = qualification["sources"]["faa"]["url"]
    index = (
        f'<a href="{selected}">CY2025</a>'
        '<a href="/example/arp-cy2026-commercial-service-enplanements-preliminary.pdf">'
        "CY2026 preliminary</a>"
    ).encode()
    probe = check_source_status.HttpProbeClient(client=object())
    monkeypatch.setattr(
        probe, "_get", lambda url, maximum: b"selected bytes" if url == selected else index
    )

    observed = probe._document_probe("faa", qualification)

    assert observed["newer_complete_years"] == [2026]
    assert observed["newer_year"] == 2026


def test_aip_current_fiscal_year_announcements_are_disclosed_as_partial(
    qualification, monkeypatch
) -> None:
    selected = qualification["sources"]["faa_aip_fy2025_awards"]["source"]["url"]
    landing = (
        b'<a href="/airports/aip/grant_histories/2025">FY2025</a>'
        b'<a href="/airports/aip/2026_aip_grants">FY2026</a>'
    )
    selected_page = f'<a href="{selected}">FY2025 grants</a>'.encode()
    probe = check_source_status.HttpProbeClient(
        client=object(), now=datetime(2026, 9, 27, tzinfo=timezone.utc)
    )

    def get(url, maximum):
        if url == selected:
            return b"selected bytes"
        if url.rstrip("/").endswith("grant_histories"):
            return landing
        if url.endswith("/2025"):
            return selected_page
        return b"Complete Listing of Grants (Announcement #7 - 9/25/2026)"

    monkeypatch.setattr(probe, "_get", get)

    observed = probe._document_probe("aip", qualification)

    assert observed["newer_year"] == 2026
    assert observed["newer_complete_years"] == []
    assert observed["newer_period_proof"] == "official_index_or_content_query"


def test_aip_ambiguous_newer_fiscal_year_is_inconclusive(
    qualification, monkeypatch
) -> None:
    selected = qualification["sources"]["faa_aip_fy2025_awards"]["source"]["url"]
    landing = (
        b'<a href="/airports/aip/grant_histories/2025">FY2025</a>'
        b'<a href="/airports/aip/2026_aip_grants">FY2026</a>'
    )
    selected_page = f'<a href="{selected}">FY2025 grants</a>'.encode()
    probe = check_source_status.HttpProbeClient(
        client=object(), now=datetime(2026, 10, 2, tzinfo=timezone.utc)
    )

    def get(url, maximum):
        if url == selected:
            return b"selected bytes"
        if url.rstrip("/").endswith("grant_histories"):
            return landing
        if url.endswith("/2025"):
            return selected_page
        return b"status not stated"

    monkeypatch.setattr(probe, "_get", get)

    observed = probe._document_probe("aip", qualification)

    assert observed["newer_period_proof"] == "inconclusive"


def test_qualification_partitions_must_bind_to_selected_bundle(
    tmp_path, bundle, qualification
) -> None:
    changed = deepcopy(qualification)
    changed["sources"]["t100"]["partitions"][0]["csv_sha256"] = "0" * 64
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(changed), encoding="utf-8")

    with pytest.raises(check_source_status.SourceStatusError, match="selected bundle"):
        check_source_status.verify_qualification_binding(bundle, path)


class RaisingProbe:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def collect(self, bundle, qualification):
        raise self.error


@pytest.mark.parametrize("error", [KeyError("missing")])
def test_cli_unexpected_probe_error_fails_with_one_line(tmp_path, capsys, error) -> None:
    with pytest.raises(SystemExit) as exc:
        check_source_status.main(_cli_args(tmp_path), probe=RaisingProbe(error))

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("source status check failed: official probe failed:")
    assert err.count("\n") == 1
    assert not (tmp_path / "receipt.json").exists()


def test_cli_unwritable_output_fails_with_one_line(tmp_path, capsys, observations) -> None:
    args = _cli_args(tmp_path)
    (tmp_path / "not-a-dir").write_text("")
    args[args.index("--output") + 1] = str(tmp_path / "not-a-dir" / "receipt.json")

    with pytest.raises(SystemExit) as exc:
        check_source_status.main(args, probe=FakeProbe(observations))

    assert exc.value.code == 1
    assert capsys.readouterr().err.startswith("source status check failed:")


@pytest.mark.parametrize("error", [OSError("unreadable"), ValueError("bad"), check_source_status.duckdb.Error("db")])
def test_qualification_binding_wraps_verifier_faults(monkeypatch, bundle, error) -> None:
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(check_source_status, "verify_datasf_snapshot", fail)
    with pytest.raises(check_source_status.SourceStatusError, match="not bound"):
        check_source_status.verify_qualification_binding(bundle, QUALIFICATION_PATH)
