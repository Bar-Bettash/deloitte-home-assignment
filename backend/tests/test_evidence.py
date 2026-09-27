"""Contract tests for the curated official-evidence notes."""

import pytest
from app.evidence import EvidenceBundle, build_explanation, load_evidence
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
