from __future__ import annotations

"""PhiKernel premise objects.

Premises make hidden assumptions explicit, traceable, and governable.

A premise may accumulate challenge evidence from failure scars, contradictions,
tests, or human review. That evidence may change premise state and L2 routing,
and may justify proposals to L1 policy or petitions to L0 constitution.

Premise state never grants authority and never directly mutates L1 or L0.
"""

from dataclasses import dataclass, field, replace
from typing import Any
import time
import uuid

from phikernel.contradiction import (
    HUMAN_SEAL,
    SCOPED_EXPERIMENT,
    TEST_WARRANT,
    VALID_RESOLUTION_KINDS,
    ContradictionObject,
)
from phikernel.mutability import (
    ConstitutionPetition,
    ConstitutionSnapshot,
    PolicyChangeProposal,
    PolicyParameter,
    RoutingWeatherReceipt,
    RoutingWeatherState,
    apply_routing_weather_update,
    petition_constitution_change,
    propose_policy_change,
)
from phikernel.scar import FailureScar


PREMISE_VERSION = "0.2.0"

ACTIVE = "ACTIVE"
CHALLENGED = "CHALLENGED"
QUARANTINED = "QUARANTINED"
RETIRED = "RETIRED"
VALID_PREMISE_STATES = {ACTIVE, CHALLENGED, QUARANTINED, RETIRED}

SCAR = "SCAR"
CONTRADICTION = "CONTRADICTION"
TEST = "TEST"
HUMAN_REVIEW = "HUMAN_REVIEW"
VALID_CHALLENGE_KINDS = {SCAR, CONTRADICTION, TEST, HUMAN_REVIEW}


class PremiseError(Exception):
    """Base exception for premise-governance failures."""


@dataclass(frozen=True)
class PremisePolicy:
    challenge_threshold: float = 0.50
    quarantine_threshold: float = 1.25
    challenged_routing_pressure: float = 0.50
    quarantined_routing_pressure: float = 1.00
    routing_half_life_seconds: float = 3600.0

    def __post_init__(self) -> None:
        if self.challenge_threshold <= 0:
            raise PremiseError("challenge_threshold must be > 0")
        if self.quarantine_threshold < self.challenge_threshold:
            raise PremiseError(
                "quarantine_threshold must be >= challenge_threshold"
            )
        if self.challenged_routing_pressure < 0:
            raise PremiseError("challenged_routing_pressure must be >= 0")
        if self.quarantined_routing_pressure < self.challenged_routing_pressure:
            raise PremiseError(
                "quarantined routing pressure must be >= challenged pressure"
            )
        if self.routing_half_life_seconds <= 0:
            raise PremiseError("routing_half_life_seconds must be > 0")


@dataclass(frozen=True)
class PremiseObject:
    premise_id: str
    statement: str
    claim_key: str
    provenance_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    dependent_route_keys: tuple[str, ...]
    dependent_carriage_ids: tuple[str, ...]
    created_at: float
    state: str = ACTIVE
    challenge_refs: tuple[str, ...] = ()
    state_reason: str = "premise registered"
    state_changed_at: float | None = None
    retired_at: float | None = None
    resolution_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    authority_change: str = "NONE"
    policy_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = PREMISE_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("premise_id", self.premise_id),
            ("statement", self.statement),
            ("claim_key", self.claim_key),
            ("state_reason", self.state_reason),
        ):
            if not value.strip():
                raise PremiseError(f"{name} must be non-empty")

        if self.state not in VALID_PREMISE_STATES:
            raise PremiseError("invalid premise state")
        for field_name, values in (
            ("provenance_refs", self.provenance_refs),
            ("evidence_refs", self.evidence_refs),
            ("dependent_route_keys", self.dependent_route_keys),
            ("dependent_carriage_ids", self.dependent_carriage_ids),
            ("challenge_refs", self.challenge_refs),
        ):
            if any(not value.strip() for value in values):
                raise PremiseError(f"{field_name} may not contain empty values")
            if len(values) != len(set(values)):
                raise PremiseError(f"{field_name} may not contain duplicates")

        if self.state == RETIRED and self.retired_at is None:
            raise PremiseError("retired premise requires retired_at")
        if self.state != RETIRED and self.retired_at is not None:
            raise PremiseError("only retired premise may contain retired_at")

        for field_name, value in (
            ("authority_change", self.authority_change),
            ("policy_change", self.policy_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise PremiseError(f"{field_name} must remain NONE")

    @classmethod
    def create(
        cls,
        *,
        statement: str,
        claim_key: str,
        provenance_refs: tuple[str, ...] | list[str] = (),
        evidence_refs: tuple[str, ...] | list[str] = (),
        dependent_route_keys: tuple[str, ...] | list[str] = (),
        dependent_carriage_ids: tuple[str, ...] | list[str] = (),
        created_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "PremiseObject":
        return cls(
            premise_id=f"premise:{uuid.uuid4()}",
            statement=statement,
            claim_key=claim_key,
            provenance_refs=tuple(provenance_refs),
            evidence_refs=tuple(evidence_refs),
            dependent_route_keys=tuple(dependent_route_keys),
            dependent_carriage_ids=tuple(dependent_carriage_ids),
            created_at=time.time() if created_at is None else float(created_at),
            metadata=dict(metadata or {}),
        )

    @property
    def blocks_dependents(self) -> bool:
        return self.state in {QUARANTINED, RETIRED}

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "premise_id": self.premise_id,
            "statement": self.statement,
            "claim_key": self.claim_key,
            "provenance_refs": list(self.provenance_refs),
            "evidence_refs": list(self.evidence_refs),
            "dependent_route_keys": list(self.dependent_route_keys),
            "dependent_carriage_ids": list(self.dependent_carriage_ids),
            "created_at": self.created_at,
            "state": self.state,
            "challenge_refs": list(self.challenge_refs),
            "state_reason": self.state_reason,
            "state_changed_at": self.state_changed_at,
            "retired_at": self.retired_at,
            "resolution_ref": self.resolution_ref,
            "metadata": dict(self.metadata),
            "authority_change": self.authority_change,
            "policy_change": self.policy_change,
            "constitutional_change": self.constitutional_change,
        }


@dataclass(frozen=True)
class PremiseChallenge:
    challenge_id: str
    premise_id: str
    challenge_kind: str
    source_ref: str
    severity: float
    challenger_id: str
    reason: str
    verified: bool
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    authority_change: str = "NONE"
    policy_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = PREMISE_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("challenge_id", self.challenge_id),
            ("premise_id", self.premise_id),
            ("challenge_kind", self.challenge_kind),
            ("source_ref", self.source_ref),
            ("challenger_id", self.challenger_id),
            ("reason", self.reason),
        ):
            if not value.strip():
                raise PremiseError(f"{name} must be non-empty")
        if self.challenge_kind not in VALID_CHALLENGE_KINDS:
            raise PremiseError("invalid challenge_kind")
        if not (0.0 <= self.severity <= 1.0):
            raise PremiseError("challenge severity must be in [0, 1]")
        for field_name, value in (
            ("authority_change", self.authority_change),
            ("policy_change", self.policy_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise PremiseError(f"{field_name} must remain NONE")


@dataclass(frozen=True)
class PremiseEvaluation:
    premise_id: str
    verified_challenge_count: int
    challenge_score: float
    recommended_state: str
    reason: str
    evaluated_at: float
    authority_change: str = "NONE"
    policy_change: str = "NONE"
    constitutional_change: str = "NONE"

    def __post_init__(self) -> None:
        if self.recommended_state not in VALID_PREMISE_STATES:
            raise PremiseError("invalid recommended_state")
        for field_name, value in (
            ("authority_change", self.authority_change),
            ("policy_change", self.policy_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise PremiseError(f"{field_name} must remain NONE")


@dataclass(frozen=True)
class PremiseStateReceipt:
    receipt_id: str
    premise_id: str
    prior_state: str
    resulting_state: str
    challenge_score: float
    challenge_refs: tuple[str, ...]
    reason: str
    changed_at: float
    authority_change: str = "NONE"
    policy_change: str = "NONE"
    constitutional_change: str = "NONE"


@dataclass(frozen=True)
class PremiseDependencyDecision:
    premise_id: str
    dependent_kind: str
    dependent_id: str
    allowed: bool
    restricted: bool
    reason: str
    premise_state: str
    evaluated_at: float
    authority_change: str = "NONE"


@dataclass(frozen=True)
class PremiseResolutionReceipt:
    receipt_id: str
    premise_id: str
    prior_state: str
    resulting_state: str
    resolution_kind: str
    resolver_id: str
    authority_ref: str
    resolution_ref: str
    resolved_at: float
    authority_change: str = "NONE"
    policy_change: str = "NONE"
    constitutional_change: str = "NONE"


def challenge_from_scar(
    premise: PremiseObject,
    scar: FailureScar,
    *,
    challenger_id: str,
    reason: str,
    created_at: float | None = None,
) -> PremiseChallenge:
    return PremiseChallenge(
        challenge_id=str(uuid.uuid4()),
        premise_id=premise.premise_id,
        challenge_kind=SCAR,
        source_ref=f"scar:{scar.scar_id}",
        severity=scar.severity,
        challenger_id=challenger_id,
        reason=reason,
        verified=True,
        created_at=time.time() if created_at is None else float(created_at),
        metadata={
            "scar_subject_id": scar.subject_id,
            "scar_route_key": scar.route_key,
            "scar_failure_kind": scar.failure_kind,
        },
    )


def challenge_from_contradiction(
    premise: PremiseObject,
    contradiction: ContradictionObject,
    *,
    challenger_id: str,
    severity: float = 1.0,
    reason: str | None = None,
    created_at: float | None = None,
) -> PremiseChallenge:
    if not contradiction.is_open:
        raise PremiseError("resolved contradiction cannot create a new premise challenge")
    return PremiseChallenge(
        challenge_id=str(uuid.uuid4()),
        premise_id=premise.premise_id,
        challenge_kind=CONTRADICTION,
        source_ref=f"contradiction:{contradiction.contradiction_id}",
        severity=float(severity),
        challenger_id=challenger_id,
        reason=reason or contradiction.reason,
        verified=True,
        created_at=time.time() if created_at is None else float(created_at),
        metadata={
            "contradiction_claim_key": contradiction.claim_key,
            "exhibit_carriage_ids": list(contradiction.exhibit_carriage_ids),
        },
    )


def record_unverified_challenge(
    premise: PremiseObject,
    *,
    challenge_kind: str,
    source_ref: str,
    severity: float,
    challenger_id: str,
    reason: str,
    created_at: float | None = None,
) -> PremiseChallenge:
    return PremiseChallenge(
        challenge_id=str(uuid.uuid4()),
        premise_id=premise.premise_id,
        challenge_kind=challenge_kind,
        source_ref=source_ref,
        severity=float(severity),
        challenger_id=challenger_id,
        reason=reason,
        verified=False,
        created_at=time.time() if created_at is None else float(created_at),
    )


def evaluate_premise(
    premise: PremiseObject,
    challenges: tuple[PremiseChallenge, ...] | list[PremiseChallenge],
    *,
    policy: PremisePolicy | None = None,
    now: float | None = None,
) -> PremiseEvaluation:
    active_policy = policy or PremisePolicy()
    evaluated_at = time.time() if now is None else float(now)

    relevant = tuple(
        challenge for challenge in challenges
        if challenge.premise_id == premise.premise_id and challenge.verified
    )
    score = sum(challenge.severity for challenge in relevant)

    if premise.state == RETIRED:
        recommended = RETIRED
        reason = "retired premise remains retired until replaced by a new premise"
    elif premise.state == QUARANTINED:
        recommended = QUARANTINED
        reason = "quarantined premise requires explicit resolution; evaluation cannot auto-restore it"
    elif score >= active_policy.quarantine_threshold:
        recommended = QUARANTINED
        reason = "verified premise challenges exceed quarantine threshold"
    elif score >= active_policy.challenge_threshold or premise.state == CHALLENGED:
        recommended = CHALLENGED
        reason = "verified premise challenges exceed challenge threshold"
    else:
        recommended = ACTIVE
        reason = "verified challenge evidence remains below challenge threshold"

    return PremiseEvaluation(
        premise_id=premise.premise_id,
        verified_challenge_count=len(relevant),
        challenge_score=score,
        recommended_state=recommended,
        reason=reason,
        evaluated_at=evaluated_at,
    )


def apply_premise_evaluation(
    premise: PremiseObject,
    evaluation: PremiseEvaluation,
    challenges: tuple[PremiseChallenge, ...] | list[PremiseChallenge],
) -> tuple[PremiseObject, PremiseStateReceipt]:
    if evaluation.premise_id != premise.premise_id:
        raise PremiseError("evaluation belongs to a different premise")
    if premise.state == RETIRED:
        raise PremiseError("retired premise cannot be mutated by runtime evaluation")

    rank = {ACTIVE: 0, CHALLENGED: 1, QUARANTINED: 2}
    if rank[evaluation.recommended_state] < rank[premise.state]:
        raise PremiseError("runtime premise evaluation may not auto-demote restrictions")

    relevant_refs = tuple(
        challenge.challenge_id
        for challenge in challenges
        if challenge.premise_id == premise.premise_id and challenge.verified
    )
    combined_refs = tuple(dict.fromkeys(premise.challenge_refs + relevant_refs))
    changed_at = evaluation.evaluated_at
    updated = replace(
        premise,
        state=evaluation.recommended_state,
        challenge_refs=combined_refs,
        state_reason=evaluation.reason,
        state_changed_at=changed_at,
    )
    receipt = PremiseStateReceipt(
        receipt_id=str(uuid.uuid4()),
        premise_id=premise.premise_id,
        prior_state=premise.state,
        resulting_state=updated.state,
        challenge_score=evaluation.challenge_score,
        challenge_refs=combined_refs,
        reason=evaluation.reason,
        changed_at=changed_at,
    )
    return updated, receipt


def evaluate_dependent_route(
    premise: PremiseObject,
    route_key: str,
    *,
    now: float | None = None,
) -> PremiseDependencyDecision:
    if route_key not in set(premise.dependent_route_keys):
        raise PremiseError("route is not registered as dependent on premise")
    timestamp = time.time() if now is None else float(now)

    if premise.state in {QUARANTINED, RETIRED}:
        return PremiseDependencyDecision(
            premise_id=premise.premise_id,
            dependent_kind="ROUTE",
            dependent_id=route_key,
            allowed=False,
            restricted=True,
            reason=f"dependent route blocked by {premise.state.lower()} premise",
            premise_state=premise.state,
            evaluated_at=timestamp,
        )
    if premise.state == CHALLENGED:
        return PremiseDependencyDecision(
            premise_id=premise.premise_id,
            dependent_kind="ROUTE",
            dependent_id=route_key,
            allowed=True,
            restricted=True,
            reason="dependent route allowed with premise challenge restriction",
            premise_state=premise.state,
            evaluated_at=timestamp,
        )
    return PremiseDependencyDecision(
        premise_id=premise.premise_id,
        dependent_kind="ROUTE",
        dependent_id=route_key,
        allowed=True,
        restricted=False,
        reason="dependent route premise is active",
        premise_state=premise.state,
        evaluated_at=timestamp,
    )


def evaluate_dependent_carriage(
    premise: PremiseObject,
    carriage_id: str,
    *,
    now: float | None = None,
) -> PremiseDependencyDecision:
    if carriage_id not in set(premise.dependent_carriage_ids):
        raise PremiseError("carriage is not registered as dependent on premise")
    timestamp = time.time() if now is None else float(now)

    blocked = premise.blocks_dependents
    restricted = premise.state != ACTIVE
    return PremiseDependencyDecision(
        premise_id=premise.premise_id,
        dependent_kind="CARRIAGE",
        dependent_id=carriage_id,
        allowed=not blocked,
        restricted=restricted,
        reason=(
            f"dependent carriage blocked by {premise.state.lower()} premise"
            if blocked
            else (
                "dependent carriage carries challenged premise restriction"
                if restricted
                else "dependent carriage premise is active"
            )
        ),
        premise_state=premise.state,
        evaluated_at=timestamp,
    )


def write_premise_routing_weather(
    premise: PremiseObject,
    state: RoutingWeatherState,
    *,
    route_key: str,
    actor_id: str,
    source_ref: str,
    policy: PremisePolicy | None = None,
    applied_at: float | None = None,
) -> tuple[RoutingWeatherState, RoutingWeatherReceipt]:
    active_policy = policy or PremisePolicy()
    if route_key not in set(premise.dependent_route_keys):
        raise PremiseError("route is not registered as dependent on premise")
    if premise.state == ACTIVE:
        raise PremiseError("active premise does not create routing pressure")

    pressure = (
        active_policy.quarantined_routing_pressure
        if premise.state in {QUARANTINED, RETIRED}
        else active_policy.challenged_routing_pressure
    )
    return apply_routing_weather_update(
        state,
        key=f"premise:{premise.premise_id}:{route_key}",
        value=pressure,
        actor_id=actor_id,
        source_ref=source_ref,
        half_life_seconds=active_policy.routing_half_life_seconds,
        applied_at=applied_at,
    )


def propose_policy_change_from_premise(
    premise: PremiseObject,
    policy: PolicyParameter,
    *,
    proposed_value: float,
    proposer_id: str,
    source_ref: str,
    reason: str,
    created_at: float | None = None,
) -> PolicyChangeProposal:
    if premise.state not in {CHALLENGED, QUARANTINED, RETIRED}:
        raise PremiseError("active premise does not justify a policy proposal")
    return propose_policy_change(
        policy,
        proposed_value=proposed_value,
        proposer_id=proposer_id,
        source_ref=f"{source_ref}|premise:{premise.premise_id}",
        reason=reason,
        created_at=created_at,
    )


def petition_constitution_from_premise(
    premise: PremiseObject,
    snapshot: ConstitutionSnapshot,
    *,
    clause_id: str,
    proposed_value: Any,
    petitioner_id: str,
    reason: str,
    created_at: float | None = None,
) -> ConstitutionPetition:
    if premise.state not in {QUARANTINED, RETIRED}:
        raise PremiseError(
            "only quarantined/retired premise may justify an L0 petition"
        )
    evidence_refs = tuple(
        dict.fromkeys(
            premise.evidence_refs
            + premise.provenance_refs
            + premise.challenge_refs
            + (f"premise:{premise.premise_id}",)
        )
    )
    return petition_constitution_change(
        snapshot,
        clause_id=clause_id,
        proposed_value=proposed_value,
        petitioner_id=petitioner_id,
        reason=reason,
        evidence_refs=evidence_refs,
        created_at=created_at,
    )


def resolve_premise(
    premise: PremiseObject,
    *,
    resulting_state: str,
    resolution_kind: str,
    resolver_id: str,
    authority_ref: str,
    resolution_ref: str,
    resolved_at: float | None = None,
) -> tuple[PremiseObject, PremiseResolutionReceipt]:
    if premise.state not in {CHALLENGED, QUARANTINED}:
        raise PremiseError("only challenged/quarantined premise may be resolved")
    if resulting_state not in {ACTIVE, RETIRED}:
        raise PremiseError("resolution may result only in ACTIVE or RETIRED")
    if resolution_kind not in VALID_RESOLUTION_KINDS:
        raise PremiseError(
            f"resolution_kind must be one of {sorted(VALID_RESOLUTION_KINDS)}"
        )
    for name, value in (
        ("resolver_id", resolver_id),
        ("authority_ref", authority_ref),
        ("resolution_ref", resolution_ref),
    ):
        if not value.strip():
            raise PremiseError(f"{name} must be non-empty")

    timestamp = time.time() if resolved_at is None else float(resolved_at)
    updated = replace(
        premise,
        state=resulting_state,
        state_reason=f"premise resolved via {resolution_kind}",
        state_changed_at=timestamp,
        retired_at=timestamp if resulting_state == RETIRED else None,
        resolution_ref=resolution_ref,
    )
    receipt = PremiseResolutionReceipt(
        receipt_id=str(uuid.uuid4()),
        premise_id=premise.premise_id,
        prior_state=premise.state,
        resulting_state=resulting_state,
        resolution_kind=resolution_kind,
        resolver_id=resolver_id,
        authority_ref=authority_ref,
        resolution_ref=resolution_ref,
        resolved_at=timestamp,
    )
    return updated, receipt
