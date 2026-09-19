from __future__ import annotations

"""PhiKernel failure-scar memory.

Failure scars are decaying routing evidence. They may influence future routing
costs, but they are explicitly non-authoritative: they cannot mutate warrants,
citation law, constitutional state, or participant capabilities.

This is navigation memory, not punishment and not legislation.
"""

from dataclasses import dataclass, field
from math import pow
from typing import Any
import time
import uuid

from phikernel.carriage import ADMIT, QUARANTINE as CARRIAGE_QUARANTINE, REFUSE as CARRIAGE_REFUSE, CarriageCandidate, MembraneVerdict
from phikernel.contradiction import ContradictionObject
from phikernel.transition import LICENSE, QUARANTINE as TRANSITION_QUARANTINE, REFUSE as TRANSITION_REFUSE, TransitionEvaluation


SCAR_VERSION = "0.2.0"

REFUSED = "REFUSED"
QUARANTINED = "QUARANTINED"
CONTRADICTION = "CONTRADICTION"
TEST_FAILURE = "TEST_FAILURE"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
PROVENANCE_BREAK = "PROVENANCE_BREAK"
VALID_FAILURE_KINDS = {
    REFUSED,
    QUARANTINED,
    CONTRADICTION,
    TEST_FAILURE,
    BUDGET_EXHAUSTED,
    PROVENANCE_BREAK,
}


class ScarError(Exception):
    """Base exception for failure-scar errors."""


@dataclass(frozen=True)
class FailureScar:
    scar_id: str
    subject_id: str
    route_key: str
    failure_kind: str
    severity: float
    source_ref: str
    observed_at: float = field(default_factory=time.time)
    half_life_seconds: float = 3600.0
    metadata: dict[str, Any] = field(default_factory=dict)
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = SCAR_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("scar_id", self.scar_id),
            ("subject_id", self.subject_id),
            ("route_key", self.route_key),
            ("failure_kind", self.failure_kind),
            ("source_ref", self.source_ref),
        ):
            if not value.strip():
                raise ScarError(f"{name} must be non-empty")

        if self.failure_kind not in VALID_FAILURE_KINDS:
            raise ScarError(
                f"failure_kind must be one of {sorted(VALID_FAILURE_KINDS)}"
            )
        if not (0.0 <= self.severity <= 1.0):
            raise ScarError("severity must be in [0.0, 1.0]")
        if self.half_life_seconds <= 0:
            raise ScarError("half_life_seconds must be > 0")

        if self.authority_change != "NONE":
            raise ScarError("failure scars may not grant or revoke authority")
        if self.warrant_change != "NONE":
            raise ScarError("failure scars may not mutate warrants")
        if self.constitutional_change != "NONE":
            raise ScarError("failure scars may not mutate constitutional state")

    @classmethod
    def create(
        cls,
        *,
        subject_id: str,
        route_key: str,
        failure_kind: str,
        severity: float,
        source_ref: str,
        observed_at: float | None = None,
        half_life_seconds: float = 3600.0,
        metadata: dict[str, Any] | None = None,
    ) -> "FailureScar":
        return cls(
            scar_id=str(uuid.uuid4()),
            subject_id=subject_id,
            route_key=route_key,
            failure_kind=failure_kind,
            severity=float(severity),
            source_ref=source_ref,
            observed_at=time.time() if observed_at is None else float(observed_at),
            half_life_seconds=float(half_life_seconds),
            metadata=dict(metadata or {}),
        )

    def weight_at(self, *, now: float | None = None) -> float:
        current = time.time() if now is None else float(now)
        age = max(0.0, current - self.observed_at)
        return self.severity * pow(0.5, age / self.half_life_seconds)

    def to_record(self, *, now: float | None = None) -> dict[str, Any]:
        return {
            "version": self.version,
            "scar_id": self.scar_id,
            "subject_id": self.subject_id,
            "route_key": self.route_key,
            "failure_kind": self.failure_kind,
            "severity": self.severity,
            "source_ref": self.source_ref,
            "observed_at": self.observed_at,
            "half_life_seconds": self.half_life_seconds,
            "current_weight": self.weight_at(now=now),
            "authority_change": self.authority_change,
            "warrant_change": self.warrant_change,
            "constitutional_change": self.constitutional_change,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ScarProfile:
    subject_id: str
    route_key: str
    scar_count: int
    active_weight: float
    failure_kinds: tuple[str, ...]
    source_refs: tuple[str, ...]
    evaluated_at: float
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    constitutional_change: str = "NONE"

    def to_record(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "route_key": self.route_key,
            "scar_count": self.scar_count,
            "active_weight": self.active_weight,
            "failure_kinds": list(self.failure_kinds),
            "source_refs": list(self.source_refs),
            "evaluated_at": self.evaluated_at,
            "authority_change": self.authority_change,
            "warrant_change": self.warrant_change,
            "constitutional_change": self.constitutional_change,
        }


def build_scar_profile(
    scars: tuple[FailureScar, ...] | list[FailureScar],
    *,
    subject_id: str,
    route_key: str,
    now: float | None = None,
) -> ScarProfile:
    evaluated_at = time.time() if now is None else float(now)
    matching = tuple(
        scar
        for scar in scars
        if scar.subject_id == subject_id and scar.route_key == route_key
    )
    active_weight = sum(scar.weight_at(now=evaluated_at) for scar in matching)

    return ScarProfile(
        subject_id=subject_id,
        route_key=route_key,
        scar_count=len(matching),
        active_weight=active_weight,
        failure_kinds=tuple(sorted({scar.failure_kind for scar in matching})),
        source_refs=tuple(scar.source_ref for scar in matching),
        evaluated_at=evaluated_at,
    )


def scar_from_transition(
    transition: TransitionEvaluation,
    *,
    route_key: str,
    severity: float,
    observed_at: float | None = None,
    half_life_seconds: float = 3600.0,
) -> FailureScar:
    """Derive a scar from a non-successful governed transition receipt."""
    decision = transition.verdict.decision
    if decision == LICENSE and transition.verdict.allowed:
        raise ScarError("licensed successful transition does not create a failure scar")

    if decision == TRANSITION_QUARANTINE:
        failure_kind = QUARANTINED
    elif decision == TRANSITION_REFUSE or not transition.verdict.allowed:
        failure_kind = REFUSED
    else:
        failure_kind = REFUSED

    return FailureScar.create(
        subject_id=transition.proposal.actor_id,
        route_key=route_key,
        failure_kind=failure_kind,
        severity=severity,
        source_ref=f"transition:{transition.verdict.verdict_id}",
        observed_at=observed_at,
        half_life_seconds=half_life_seconds,
        metadata={
            "proposal_id": transition.proposal.proposal_id,
            "warrant_id": transition.proposal.warrant_id,
            "decision": transition.verdict.decision,
        },
    )


def scar_from_membrane(
    candidate: CarriageCandidate,
    verdict: MembraneVerdict,
    *,
    subject_id: str,
    route_key: str,
    severity: float,
    observed_at: float | None = None,
    half_life_seconds: float = 3600.0,
) -> FailureScar:
    """Derive a scar from a failed/quarantined carriage membrane outcome."""
    if verdict.carriage_id != candidate.carriage_id:
        raise ScarError("membrane verdict belongs to a different carriage")
    if verdict.decision == ADMIT:
        raise ScarError("admitted carriage does not create a failure scar")

    if verdict.missing_provenance_refs:
        failure_kind = PROVENANCE_BREAK
    elif verdict.decision == CARRIAGE_QUARANTINE:
        failure_kind = QUARANTINED
    elif verdict.decision == CARRIAGE_REFUSE:
        failure_kind = REFUSED
    else:
        raise ScarError(f"unsupported membrane decision: {verdict.decision}")

    return FailureScar.create(
        subject_id=subject_id,
        route_key=route_key,
        failure_kind=failure_kind,
        severity=severity,
        source_ref=f"membrane:{verdict.membrane_verdict_id}",
        observed_at=observed_at,
        half_life_seconds=half_life_seconds,
        metadata={
            "carriage_id": candidate.carriage_id,
            "claim_key": candidate.claim_key,
            "membrane_decision": verdict.decision,
        },
    )


def scar_from_contradiction(
    contradiction: ContradictionObject,
    *,
    subject_id: str,
    route_key: str,
    severity: float,
    observed_at: float | None = None,
    half_life_seconds: float = 3600.0,
) -> FailureScar:
    """Derive routing memory from an open contradiction without choosing a winner."""
    if not contradiction.is_open:
        raise ScarError("resolved contradiction does not create a new failure scar")

    return FailureScar.create(
        subject_id=subject_id,
        route_key=route_key,
        failure_kind=CONTRADICTION,
        severity=severity,
        source_ref=f"contradiction:{contradiction.contradiction_id}",
        observed_at=observed_at,
        half_life_seconds=half_life_seconds,
        metadata={
            "claim_key": contradiction.claim_key,
            "exhibit_carriage_ids": list(contradiction.exhibit_carriage_ids),
        },
    )
