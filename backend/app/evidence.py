"""Validated, curated official evidence and deterministic explanations.

These records are human-reviewed inputs. A traffic score is only a screening
signal; it does not itself establish a terminal constraint or justify a project.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "evidence.json"
EXPECTED_AIRPORTS = ("PVD", "PWM", "BOS", "SFO")


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceSource(EvidenceModel):
    source_id: str = Field(min_length=2, max_length=60)
    publisher: str = Field(min_length=2, max_length=160)
    title: str = Field(min_length=3, max_length=240)
    url: AnyHttpUrl
    source_date: date | None
    checked_at: date
    historical: bool
    date_note: str = Field(min_length=8, max_length=300)


class EvidenceObservation(EvidenceModel):
    source_id: str = Field(min_length=2, max_length=60)
    locator: str = Field(min_length=5, max_length=300)
    fact: str = Field(min_length=12, max_length=600)
    bearing: Literal["context", "counterevidence", "current_project"]


class AirportEvidence(EvidenceModel):
    airport: str = Field(pattern=r"^[A-Z]{3}$")
    status: Literal["supported", "contradicted", "unknown"]
    traffic_signal: str = Field(min_length=8, max_length=300)
    unresolved_constraint: str = Field(min_length=12, max_length=500)
    proposed_intervention: str | None = Field(default=None, max_length=300)
    observations: list[EvidenceObservation] = Field(min_length=1, max_length=12)
    counterevidence: list[str] = Field(min_length=1, max_length=8)
    next_diligence: list[str] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(min_length=1, max_length=8)


class BusinessOutcome(EvidenceModel):
    outcome: Literal["profitability", "quantitative_unmet_demand"]
    status: Literal["not_identifiable"]
    reason: str = Field(min_length=20, max_length=500)


class EvidenceBundle(EvidenceModel):
    checked_at: date
    traffic_snapshot: str = Field(min_length=8, max_length=240)
    sources: list[EvidenceSource] = Field(min_length=4, max_length=20)
    airports: list[AirportEvidence] = Field(min_length=4, max_length=4)
    sfo_business_outcomes: list[BusinessOutcome] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_complete_curated_record(self) -> EvidenceBundle:
        airport_ids = tuple(item.airport for item in self.airports)
        if airport_ids != EXPECTED_AIRPORTS:
            raise ValueError(f"expected curated airport order {EXPECTED_AIRPORTS}")
        if any(item.status != "unknown" for item in self.airports):
            raise ValueError("curated notes must remain unknown without a current observed constraint")
        source_ids = [item.source_id for item in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        known_sources = set(source_ids)
        for airport in self.airports:
            if any(note.source_id not in known_sources for note in airport.observations):
                raise ValueError(f"unresolved source reference for {airport.airport}")
            if not airport.counterevidence or not airport.next_diligence:
                raise ValueError(f"counterevidence and next diligence required for {airport.airport}")
            if airport.proposed_intervention is not None:
                raise ValueError("unknown evidence cannot recommend an intervention")
        outcome_ids = {item.outcome for item in self.sfo_business_outcomes}
        if outcome_ids != {"profitability", "quantitative_unmet_demand"}:
            raise ValueError("SFO business outcomes must be explicitly not_identifiable")
        return self


class AirportExplanation(EvidenceModel):
    airport: str
    summary: str
    signal: str
    constraint: str
    intervention: str
    counterevidence: list[str]
    next_diligence: list[str]
    source_ids: list[str]
    limitations: list[str]
    business_outcomes: list[BusinessOutcome] = Field(default_factory=list)


def load_evidence(path: Path = DATA_PATH) -> EvidenceBundle:
    """Load and validate the actual curated JSON evidence record."""
    return EvidenceBundle.model_validate_json(path.read_text(encoding="utf-8"))


def build_explanation(airport: str, evidence: EvidenceBundle) -> AirportExplanation:
    """Build a fixed, source-linked summary without inferring a terminal project."""
    note = next((item for item in evidence.airports if item.airport == airport), None)
    if note is None:
        raise ValueError("airport has no reviewed evidence note")
    source_ids = list(dict.fromkeys(item.source_id for item in note.observations))
    outcomes = evidence.sfo_business_outcomes if airport == "SFO" else []
    return AirportExplanation(
        airport=airport,
        summary=(f"{note.traffic_signal} Current terminal constraint status is {note.status}; "
                 "the traffic screen is a reason to investigate, not proof of terminal congestion."),
        signal=note.traffic_signal,
        constraint=note.unresolved_constraint,
        intervention=note.proposed_intervention or "No terminal intervention is recommended from this evidence.",
        counterevidence=note.counterevidence,
        next_diligence=note.next_diligence,
        source_ids=source_ids,
        limitations=note.limitations,
        business_outcomes=outcomes,
    )
