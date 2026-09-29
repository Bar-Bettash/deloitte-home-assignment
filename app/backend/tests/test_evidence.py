"""Contract tests for the curated official-evidence notes."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from app.evidence import (
    EvidenceBundle,
    EvidenceIntegrityError,
    build_explanation,
    load_evidence,
)
from app.sources.bundle import BundleContext, SnapshotRef
from pydantic import ValidationError


def test_actual_curated_file_has_reviewed_airports_sources_and_dates():
    bundle = load_evidence()

    assert [airport.airport for airport in bundle.airports] == ["PVD", "PWM", "BOS", "SFO"]
    assert all(airport.status == "unknown" for airport in bundle.airports)
    assert all(source.checked_at == bundle.checked_at for source in bundle.sources)
    assert all(airport.observations and airport.counterevidence for airport in bundle.airports)
    assert all(airport.next_diligence and airport.limitations for airport in bundle.airports)
    assert all(observation.locator and observation.fact for airport in bundle.airports for observation in airport.observations)


def _payload():
    return load_evidence().model_dump(mode="python")


def test_empty_or_incomplete_airport_review_is_rejected():
    payload = _payload()
    payload["airports"] = []
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


def test_placeholder_or_unreviewed_airport_set_is_rejected():
    payload = _payload()
    payload["airports"][0]["airport"] = "XXX"
    with pytest.raises(ValidationError):
        EvidenceBundle.model_validate(payload)


def test_unknown_note_cannot_recommend_terminal_intervention():
    payload = _payload()
    payload["airports"][0]["proposed_intervention"] = "Build more gates"
    with pytest.raises(ValidationError, match="unknown evidence"):
        EvidenceBundle.model_validate(payload)


def test_source_references_must_resolve():
    payload = _payload()
    payload["airports"][0]["observations"][0]["source_id"] = "missing_source"
    with pytest.raises(ValidationError, match="unresolved source"):
        EvidenceBundle.model_validate(payload)


def test_terminal_explanation_preserves_unknown_and_counterevidence():
    bundle = load_evidence()
    result = build_explanation("PVD", bundle)

    assert "score 87.0" in result.summary
    assert "not proof of terminal congestion" in result.summary
    assert result.intervention.startswith("No terminal intervention")
    assert result.counterevidence
    assert result.next_diligence
    assert result.source_ids == ["pvd_master_plan_2021_s5"]


def test_sfo_explanation_keeps_business_outcomes_not_identifiable():
    result = build_explanation("SFO", load_evidence())

    assert {outcome.outcome for outcome in result.business_outcomes} == {
        "profitability",
        "quantitative_unmet_demand",
    }
    assert all(outcome.status == "not_identifiable" for outcome in result.business_outcomes)
    assert result.counterevidence
    assert "not_identifiable" in result.limitations[-1]


def test_explanation_cannot_be_built_for_unreviewed_airport():
    with pytest.raises(ValueError, match="no reviewed evidence note"):
        build_explanation("PVC", load_evidence())


def test_every_source_has_a_human_readable_date_context():
    bundle = load_evidence()
    assert all(source.date_note.strip() for source in bundle.sources)
    assert any(source.historical for source in bundle.sources)
    assert any(not source.historical and source.source_date is None for source in bundle.sources)


def _recent_payload() -> dict:
    payload = load_evidence().model_dump(mode="json")
    by_airport = {item["airport"]: item for item in payload["airports"]}
    airports = [deepcopy(by_airport[key]) for key in ("PVD", "BOS", "PWM", "SFO")]
    airports[0]["airport"], airports[1]["airport"] = "HVN", "BGR"
    for airport in airports:
        source_id = airport["observations"][0]["source_id"]
        airport["project_status_reviews"] = [{
            "source_ids": [source_id],
            "checked_at": "2026-09-27",
            "outcome": "unresolved",
            "summary": "Reviewed project status does not establish a current terminal deficit.",
        }]
        airport["current_constraint_evidence"] = None
    refs = []
    for index, source in enumerate(("datasf", "t100", "ontime", "faa", "aip")):
        refs.append({
            "source": source,
            "snapshot_id": f"{source}-test",
            "manifest_sha256": f"{index + 1:x}" * 64,
            "content_sha256": f"{index + 6:x}" * 64,
            "years": [2024, 2025] if source in {"datasf", "t100"} else [2025],
        })
    payload.update({
        "scope": {
            "bundle_id": "annual-2025-test",
            "baseline_year": 2024,
            "comparison_year": 2025,
            "cohort": ["HVN", "BGR", "PWM"],
            "source_refs": refs,
        },
        "aip_funding_context": {
            "snapshot_id": "aip-test",
            "fiscal_year": 2025,
            "total_grant_count": 3707,
            "new_england_award_count": 66,
            "new_england_total_usd": "259112386.00",
            "interpretation": "funding_context_only",
        },
        "airports": airports,
    })
    return payload


def _write_recent(tmp_path: Path, payload: dict) -> tuple[Path, BundleContext]:
    evidence_path = tmp_path / "evidence_2025.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    sources = {}
    for item in payload["scope"]["source_refs"]:
        manifest = tmp_path / f"{item['source']}-manifest.json"
        data = tmp_path / f"{item['source']}-data.bin"
        if item["source"] == "aip":
            manifest.write_text(json.dumps({
                "snapshot_id": "aip-test", "fiscal_year": 2025, "row_count": 3707,
                "new_england_awards": 66, "new_england_total_usd": "259112386.00",
            }), encoding="utf-8")
            item["manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
            evidence_path.write_text(json.dumps(payload), encoding="utf-8")
        ref = SnapshotRef(
            source=item["source"], snapshot_id=item["snapshot_id"],
            manifest_sha256=item["manifest_sha256"], content_sha256=item["content_sha256"],
            manifest_path=manifest, data_path=data, years=tuple(item["years"]),
            vintage_id="test", publication_status="staged-candidate",
        )
        sources[item["source"]] = ref
    bundle = BundleContext(
        bundle_id=payload["scope"]["bundle_id"], manifest_sha256="f" * 64,
        baseline_year=2024, comparison_year=2025,
        cohort=tuple(payload["scope"]["cohort"]), sources=sources,
        evidence_path=evidence_path,
        evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
    )
    return evidence_path, bundle


def _validate_recent(payload: dict) -> EvidenceBundle:
    return EvidenceBundle.model_validate_json(json.dumps(payload))


def test_bundle_directed_recent_evidence_binds_scope_sources_and_aip(tmp_path: Path):
    path, bundle = _write_recent(tmp_path, _recent_payload())

    evidence = load_evidence(path, bundle=bundle)

    assert [item.airport for item in evidence.airports] == ["HVN", "BGR", "PWM", "SFO"]
    assert all(item.status == "unknown" for item in evidence.airports)
    assert evidence.scope.bundle_id == bundle.bundle_id
    assert tuple(item.source for item in evidence.scope.source_refs) == tuple(bundle.sources)
    assert evidence.aip_funding_context.new_england_award_count == 66
    assert evidence.aip_funding_context.new_england_total_usd == "259112386.00"
    assert evidence.aip_funding_context.interpretation == "funding_context_only"


@pytest.mark.parametrize("field", ["bundle_id", "comparison_year", "cohort", "source_ref"])
def test_bundle_directed_evidence_rejects_scope_mismatch(tmp_path: Path, field: str):
    payload = _recent_payload()
    path, bundle = _write_recent(tmp_path, payload)
    if field == "bundle_id":
        payload["scope"]["bundle_id"] = "other-bundle"
    elif field == "comparison_year":
        payload["scope"]["comparison_year"] = 2026
        payload["aip_funding_context"]["fiscal_year"] = 2026
        for ref in payload["scope"]["source_refs"]:
            ref["years"] = [2024, 2026] if ref["source"] in {"datasf", "t100"} else [2026]
    elif field == "cohort":
        payload["scope"]["cohort"] = ["HVN"]
    else:
        payload["scope"]["source_refs"][0]["content_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    bundle = bundle.__class__(
        bundle.bundle_id, bundle.manifest_sha256, bundle.baseline_year,
        bundle.comparison_year, bundle.cohort, bundle.sources, path,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    with pytest.raises(EvidenceIntegrityError, match="scope"):
        load_evidence(bundle=bundle)


def test_bundle_directed_evidence_rejects_wrong_path_or_corruption(tmp_path: Path):
    path, bundle = _write_recent(tmp_path, _recent_payload())
    other = tmp_path / "other.json"
    other.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError, match="path"):
        load_evidence(other, bundle=bundle)

    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(EvidenceIntegrityError, match="checksum"):
        load_evidence(bundle=bundle)


def test_bundle_directed_evidence_rejects_aip_value_mismatch(tmp_path: Path):
    payload = _recent_payload()
    path, bundle = _write_recent(tmp_path, payload)
    payload["aip_funding_context"]["new_england_award_count"] = 65
    path.write_text(json.dumps(payload), encoding="utf-8")
    bundle = bundle.__class__(
        bundle.bundle_id, bundle.manifest_sha256, bundle.baseline_year,
        bundle.comparison_year, bundle.cohort, bundle.sources, path,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    with pytest.raises(EvidenceIntegrityError, match="AIP funding"):
        load_evidence(bundle=bundle)


def test_project_reviews_must_resolve_sources():
    payload = _recent_payload()
    payload["airports"][0]["project_status_reviews"][0]["source_ids"] = ["missing"]

    with pytest.raises(ValidationError, match="project-review source"):
        _validate_recent(payload)


def test_supported_claim_requires_observed_measure_project_review_and_intervention():
    payload = _recent_payload()
    note = payload["airports"][0]
    note["status"] = "supported"
    note["proposed_intervention"] = "Evaluate a bounded terminal intervention."
    note["project_status_reviews"][0]["outcome"] = "planned"

    with pytest.raises(ValidationError, match="observed terminal measure"):
        _validate_recent(payload)

    note["current_constraint_evidence"] = {
        "source_ids": [note["observations"][0]["source_id"]],
        "observed_at": "2026-09-27",
        "measurement_kind": "observed_terminal_throughput",
        "finding": "Observed peak-period processing reached the measured terminal limit.",
        "throughput_link": "The same observation links that limit to processed passenger throughput.",
    }
    assert _validate_recent(payload).airports[0].status == "supported"


def test_recent_unknown_note_still_requires_current_project_status_review():
    payload = _recent_payload()
    payload["airports"][0]["project_status_reviews"] = []

    with pytest.raises(ValidationError, match="project-status review"):
        _validate_recent(payload)


def test_unknown_recent_notes_need_no_numeric_deficit_or_intervention():
    evidence = _validate_recent(_recent_payload())

    assert all(item.current_constraint_evidence is None for item in evidence.airports)
    assert all(item.proposed_intervention is None for item in evidence.airports)
    assert all(item.project_status_reviews for item in evidence.airports)
