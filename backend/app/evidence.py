"""Validated, curated official evidence and deterministic explanations.

These records are human-reviewed inputs. A traffic score is only a screening
signal; it does not itself establish a terminal constraint or justify a project.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Literal

from app.sources.bundle import BundleContext
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "evidence.json"
EXPECTED_AIRPORTS = ("PVD", "PWM", "BOS", "SFO")
RECENT_AIRPORTS = ("HVN", "BGR", "PWM", "SFO")


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


class EvidenceSourceIdentity(EvidenceModel):
    source: Literal["datasf", "t100", "ontime", "faa", "aip"]
    snapshot_id: str = Field(min_length=3, max_length=160)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    years: tuple[int, ...] = Field(min_length=1, max_length=2)


class EvidenceScope(EvidenceModel):
    bundle_id: str = Field(min_length=3, max_length=128)
    baseline_year: int = Field(ge=1900, le=2100)
    comparison_year: int = Field(ge=1900, le=2100)
    cohort: tuple[str, ...] = Field(min_length=1, max_length=30)
    source_refs: tuple[EvidenceSourceIdentity, ...] = Field(min_length=4, max_length=5)


class AIPFundingContext(EvidenceModel):
    snapshot_id: str = Field(min_length=3, max_length=160)
    fiscal_year: int = Field(ge=1900, le=2100)
    total_grant_count: int = Field(ge=0)
    new_england_award_count: int = Field(ge=0)
    new_england_total_usd: str = Field(pattern=r"^(0|[1-9][0-9]*)\.[0-9]{2}$")
    interpretation: Literal["funding_context_only"]


class ProjectStatusReview(EvidenceModel):
    source_ids: list[str] = Field(min_length=1, max_length=6)
    checked_at: date
    outcome: Literal["planned", "ongoing", "completed", "superseded", "unresolved"]
    summary: str = Field(min_length=12, max_length=600)


class CurrentConstraintEvidence(EvidenceModel):
    source_ids: list[str] = Field(min_length=1, max_length=6)
    observed_at: date
    measurement_kind: Literal[
        "observed_terminal_throughput",
        "observed_terminal_queue",
        "observed_gate_utilization",
    ]
    finding: str = Field(min_length=12, max_length=600)
    throughput_link: str = Field(min_length=12, max_length=600)


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
    project_status_reviews: list[ProjectStatusReview] = Field(default_factory=list, max_length=8)
    current_constraint_evidence: CurrentConstraintEvidence | None = None


class BusinessOutcome(EvidenceModel):
    outcome: Literal["profitability", "quantitative_unmet_demand"]
    status: Literal["not_identifiable"]
    reason: str = Field(min_length=20, max_length=500)


class EvidenceBundle(EvidenceModel):
    checked_at: date
    traffic_snapshot: str = Field(min_length=8, max_length=240)
    scope: EvidenceScope | None = None
    aip_funding_context: AIPFundingContext | None = None
    sources: list[EvidenceSource] = Field(min_length=4, max_length=20)
    airports: list[AirportEvidence] = Field(min_length=4, max_length=4)
    sfo_business_outcomes: list[BusinessOutcome] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_complete_curated_record(self) -> EvidenceBundle:
        airport_ids = tuple(item.airport for item in self.airports)
        expected_airports = EXPECTED_AIRPORTS if self.scope is None else RECENT_AIRPORTS
        if airport_ids != expected_airports:
            raise ValueError(f"expected curated airport order {expected_airports}")
        if self.scope is None and self.aip_funding_context is not None:
            raise ValueError("historical evidence cannot carry recent AIP context")
        if self.scope is not None and self.aip_funding_context is None:
            raise ValueError("recent evidence requires separate AIP funding context")
        if self.scope is not None:
            scope = self.scope
            ref_names = tuple(ref.source for ref in scope.source_refs)
            if ref_names != ("datasf", "t100", "ontime", "faa", "aip"):
                raise ValueError("recent evidence requires the five ordered source identities")
            if scope.baseline_year >= scope.comparison_year:
                raise ValueError("evidence year pair is invalid")
            expected_years = {
                "datasf": (scope.baseline_year, scope.comparison_year),
                "t100": (scope.baseline_year, scope.comparison_year),
                "ontime": (scope.comparison_year,),
                "faa": (scope.comparison_year,),
                "aip": (scope.comparison_year,),
            }
            if any(ref.years != expected_years[ref.source] for ref in scope.source_refs):
                raise ValueError("evidence source periods do not match the selected pair")
            if self.aip_funding_context.snapshot_id != scope.source_refs[-1].snapshot_id:
                raise ValueError("AIP funding context snapshot identity does not match")
            if self.aip_funding_context.fiscal_year != scope.comparison_year:
                raise ValueError("AIP funding context year does not match")
        source_ids = [item.source_id for item in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be unique")
        if any(source.checked_at != self.checked_at for source in self.sources):
            raise ValueError("source review dates must match the evidence review date")
        known_sources = set(source_ids)
        for airport in self.airports:
            if any(note.source_id not in known_sources for note in airport.observations):
                raise ValueError(f"unresolved source reference for {airport.airport}")
            review_source_ids = {
                source_id
                for review in airport.project_status_reviews
                for source_id in review.source_ids
            }
            if not review_source_ids.issubset(known_sources):
                raise ValueError(f"unresolved project-review source for {airport.airport}")
            if self.scope is not None and (
                not airport.project_status_reviews
                or any(review.checked_at != self.checked_at for review in airport.project_status_reviews)
            ):
                raise ValueError(f"current project-status review required for {airport.airport}")
            constraint = airport.current_constraint_evidence
            if constraint is not None and not set(constraint.source_ids).issubset(known_sources):
                raise ValueError(f"unresolved constraint source for {airport.airport}")
            if constraint is not None and constraint.observed_at > self.checked_at:
                raise ValueError(f"constraint observation is after review for {airport.airport}")
            if not airport.counterevidence or not airport.next_diligence:
                raise ValueError(f"counterevidence and next diligence required for {airport.airport}")
            if airport.status == "supported":
                if constraint is None or not airport.project_status_reviews:
                    raise ValueError(
                        "supported evidence requires an observed terminal measure and project review"
                    )
                if airport.proposed_intervention is None:
                    raise ValueError("supported evidence requires an explicit intervention")
            elif airport.proposed_intervention is not None:
                label = "unknown" if airport.status == "unknown" else "unsupported"
                raise ValueError(f"{label} evidence cannot recommend an intervention")
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


class EvidenceIntegrityError(RuntimeError):
    """The bundle-directed evidence artifact does not match its frozen context."""


def load_evidence(
    path: Path | None = None, *, bundle: BundleContext | None = None
) -> EvidenceBundle:
    """Load historical evidence or the exact artifact referenced by a bundle."""
    selected = DATA_PATH if path is None and bundle is None else Path(path or bundle.evidence_path)
    if bundle is not None and selected.resolve() != bundle.evidence_path.resolve():
        raise EvidenceIntegrityError("evidence path does not match the supplied bundle")
    try:
        raw = selected.read_bytes()
    except OSError as exc:
        raise EvidenceIntegrityError("evidence artifact is unavailable") from exc
    if bundle is not None and hashlib.sha256(raw).hexdigest() != bundle.evidence_sha256:
        raise EvidenceIntegrityError("evidence checksum does not match the supplied bundle")
    evidence = EvidenceBundle.model_validate_json(raw)
    if bundle is not None:
        _validate_bundle_evidence(evidence, bundle)
    return evidence


def _validate_bundle_evidence(evidence: EvidenceBundle, bundle: BundleContext) -> None:
    scope = evidence.scope
    if scope is None:
        raise EvidenceIntegrityError("bundle evidence is missing machine scope")
    expected_refs = tuple(
        EvidenceSourceIdentity(
            source=name,
            snapshot_id=ref.snapshot_id,
            manifest_sha256=ref.manifest_sha256,
            content_sha256=ref.content_sha256,
            years=ref.years,
        )
        for name, ref in bundle.sources.items()
    )
    if (
        scope.bundle_id != bundle.bundle_id
        or (scope.baseline_year, scope.comparison_year)
        != (bundle.baseline_year, bundle.comparison_year)
        or scope.cohort != bundle.cohort
        or scope.source_refs != expected_refs
    ):
        raise EvidenceIntegrityError("evidence scope does not match the supplied bundle")
    _validate_aip_context(evidence, bundle)


def _validate_aip_context(evidence: EvidenceBundle, bundle: BundleContext) -> None:
    context = evidence.aip_funding_context
    ref = bundle.sources.get("aip")
    if context is None or ref is None:
        raise EvidenceIntegrityError("bundle evidence is missing AIP funding context")
    try:
        raw = ref.manifest_path.read_bytes()
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EvidenceIntegrityError("AIP manifest is unavailable or invalid") from exc
    if not isinstance(manifest, dict):
        raise EvidenceIntegrityError("AIP manifest is unavailable or invalid")
    expected = (
        ref.manifest_sha256,
        ref.snapshot_id,
        manifest.get("fiscal_year"),
        manifest.get("row_count"),
        manifest.get("new_england_awards"),
        manifest.get("new_england_total_usd"),
    )
    actual = (
        hashlib.sha256(raw).hexdigest(),
        context.snapshot_id,
        context.fiscal_year,
        context.total_grant_count,
        context.new_england_award_count,
        context.new_england_total_usd,
    )
    if (
        actual != expected
        or manifest.get("snapshot_id") != ref.snapshot_id
        or manifest.get("fiscal_year") != bundle.comparison_year
        or context.interpretation != "funding_context_only"
    ):
        raise EvidenceIntegrityError("AIP funding context does not match the selected snapshot")


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
