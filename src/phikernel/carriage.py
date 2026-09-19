from __future__ import annotations

"""PhiKernel carriage membrane.

A licensed transition may produce output, but output is not automatically shared
reality. A CarriageCandidate is immutable lineage metadata for one candidate
payload. The membrane decides whether that candidate may be admitted, inspected
only under quarantine, or refused.

Citation rights are contextual rather than stored as a permanent mutable bit.
This matters because a carriage admitted yesterday may become blocked tomorrow
when an open contradiction is materialized. The historical admission receipt
stays unchanged while citation is denied by current state.
"""

from dataclasses import dataclass, field
from typing import Any
import hashlib
import json
import time
import uuid

from phikernel.contradiction import ContradictionObject
from phikernel.transition import LICENSE, TransitionEvaluation


CARRIAGE_VERSION = "0.2.0"

ADMIT = "ADMIT"
QUARANTINE = "QUARANTINE"
REFUSE = "REFUSE"
VALID_MEMBRANE_DECISIONS = {ADMIT, QUARANTINE, REFUSE}


class CarriageError(Exception):
    """Base exception for carriage/membrane failures."""


def canonical_payload_bytes(payload: Any) -> bytes:
    """Serialize JSON-compatible payloads deterministically for hashing."""
    try:
        text = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CarriageError("payload must be deterministically JSON-serializable") from exc
    return text.encode("utf-8")


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class CarriageCandidate:
    carriage_id: str
    payload_hash: str
    payload_kind: str
    claim_key: str
    producer_proposal_id: str
    producer_verdict_id: str
    producer_decision: str
    warrant_id: str
    parent_carriage_ids: tuple[str, ...] = ()
    provenance_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = CARRIAGE_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("carriage_id", self.carriage_id),
            ("payload_hash", self.payload_hash),
            ("payload_kind", self.payload_kind),
            ("claim_key", self.claim_key),
            ("producer_proposal_id", self.producer_proposal_id),
            ("producer_verdict_id", self.producer_verdict_id),
            ("producer_decision", self.producer_decision),
            ("warrant_id", self.warrant_id),
        ):
            if not value.strip():
                raise CarriageError(f"{name} must be non-empty")
        if len(self.payload_hash) != 64:
            raise CarriageError("payload_hash must be a SHA-256 hex digest")
        try:
            int(self.payload_hash, 16)
        except ValueError as exc:
            raise CarriageError("payload_hash must be hexadecimal") from exc

        for field_name, values in (
            ("parent_carriage_ids", self.parent_carriage_ids),
            ("provenance_refs", self.provenance_refs),
            ("evidence_refs", self.evidence_refs),
        ):
            if any(not value.strip() for value in values):
                raise CarriageError(f"{field_name} may not contain empty values")
            if len(values) != len(set(values)):
                raise CarriageError(f"{field_name} may not contain duplicates")

    @classmethod
    def create(
        cls,
        *,
        payload: Any,
        claim_key: str,
        transition: TransitionEvaluation,
        parent_carriage_ids: tuple[str, ...] | list[str] = (),
        provenance_refs: tuple[str, ...] | list[str] | None = None,
        evidence_refs: tuple[str, ...] | list[str] | None = None,
        created_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "CarriageCandidate":
        proposal = transition.proposal
        verdict = transition.verdict
        return cls(
            carriage_id=str(uuid.uuid4()),
            payload_hash=payload_hash(payload),
            payload_kind=type(payload).__name__,
            claim_key=claim_key,
            producer_proposal_id=proposal.proposal_id,
            producer_verdict_id=verdict.verdict_id,
            producer_decision=verdict.decision,
            warrant_id=proposal.warrant_id,
            parent_carriage_ids=tuple(parent_carriage_ids),
            provenance_refs=tuple(
                proposal.provenance_refs if provenance_refs is None else provenance_refs
            ),
            evidence_refs=tuple(
                proposal.evidence_refs if evidence_refs is None else evidence_refs
            ),
            created_at=time.time() if created_at is None else float(created_at),
            metadata=dict(metadata or {}),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "carriage_id": self.carriage_id,
            "payload_hash": self.payload_hash,
            "payload_kind": self.payload_kind,
            "claim_key": self.claim_key,
            "producer_proposal_id": self.producer_proposal_id,
            "producer_verdict_id": self.producer_verdict_id,
            "producer_decision": self.producer_decision,
            "warrant_id": self.warrant_id,
            "parent_carriage_ids": list(self.parent_carriage_ids),
            "provenance_refs": list(self.provenance_refs),
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MembraneVerdict:
    membrane_verdict_id: str
    carriage_id: str
    decision: str
    citation_allowed_at_admission: bool
    reason: str
    missing_provenance_refs: tuple[str, ...] = ()
    missing_parent_carriage_ids: tuple[str, ...] = ()
    blocking_contradiction_ids: tuple[str, ...] = ()
    evaluated_at: float = field(default_factory=time.time)
    authority_change: str = "NONE"
    claims_promoted: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = CARRIAGE_VERSION

    def __post_init__(self) -> None:
        if self.decision not in VALID_MEMBRANE_DECISIONS:
            raise CarriageError(
                f"decision must be one of {sorted(VALID_MEMBRANE_DECISIONS)}"
            )
        expected_citation = self.decision == ADMIT
        if self.citation_allowed_at_admission != expected_citation:
            raise CarriageError(
                "citation_allowed_at_admission must be true only for ADMIT"
            )
        if self.authority_change != "NONE":
            raise CarriageError("membrane evaluation may not grant authority")
        if self.claims_promoted != 0:
            raise CarriageError("membrane evaluation may not promote claims")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "membrane_verdict_id": self.membrane_verdict_id,
            "carriage_id": self.carriage_id,
            "decision": self.decision,
            "citation_allowed_at_admission": self.citation_allowed_at_admission,
            "reason": self.reason,
            "missing_provenance_refs": list(self.missing_provenance_refs),
            "missing_parent_carriage_ids": list(self.missing_parent_carriage_ids),
            "blocking_contradiction_ids": list(self.blocking_contradiction_ids),
            "evaluated_at": self.evaluated_at,
            "authority_change": self.authority_change,
            "claims_promoted": self.claims_promoted,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CitationDecision:
    carriage_id: str
    allowed: bool
    reason: str
    blocking_contradiction_ids: tuple[str, ...] = ()
    evaluated_at: float = field(default_factory=time.time)

    def to_record(self) -> dict[str, Any]:
        return {
            "carriage_id": self.carriage_id,
            "allowed": self.allowed,
            "reason": self.reason,
            "blocking_contradiction_ids": list(self.blocking_contradiction_ids),
            "evaluated_at": self.evaluated_at,
        }


def evaluate_carriage(
    candidate: CarriageCandidate,
    transition: TransitionEvaluation,
    *,
    verified_provenance_refs: set[str] | frozenset[str] | tuple[str, ...] | list[str] = (),
    admitted_parent_carriage_ids: set[str] | frozenset[str] | tuple[str, ...] | list[str] = (),
    contradictions: tuple[ContradictionObject, ...] | list[ContradictionObject] = (),
    now: float | None = None,
) -> MembraneVerdict:
    """Evaluate a candidate output without modifying the candidate itself."""

    evaluated_at = time.time() if now is None else float(now)

    if (
        candidate.producer_proposal_id != transition.proposal.proposal_id
        or candidate.producer_verdict_id != transition.verdict.verdict_id
        or candidate.warrant_id != transition.proposal.warrant_id
    ):
        return _membrane_verdict(
            candidate,
            REFUSE,
            "candidate lineage does not match supplied transition evaluation",
            evaluated_at=evaluated_at,
        )

    if transition.verdict.decision != LICENSE or not transition.verdict.allowed:
        return _membrane_verdict(
            candidate,
            REFUSE,
            "producer transition was not licensed",
            evaluated_at=evaluated_at,
        )

    verified = set(verified_provenance_refs)
    missing_provenance = tuple(
        ref for ref in candidate.provenance_refs if ref not in verified
    )
    if missing_provenance:
        return _membrane_verdict(
            candidate,
            QUARANTINE,
            "candidate provenance is incomplete or unverifiable",
            evaluated_at=evaluated_at,
            missing_provenance_refs=missing_provenance,
        )

    admitted_parents = set(admitted_parent_carriage_ids)
    missing_parents = tuple(
        carriage_id
        for carriage_id in candidate.parent_carriage_ids
        if carriage_id not in admitted_parents
    )
    if missing_parents:
        return _membrane_verdict(
            candidate,
            QUARANTINE,
            "candidate depends on parent carriages without admission rights",
            evaluated_at=evaluated_at,
            missing_parent_carriage_ids=missing_parents,
        )

    blocking = _blocking_contradictions(candidate, contradictions)
    if blocking:
        return _membrane_verdict(
            candidate,
            QUARANTINE,
            "open contradiction blocks carriage admission/citation",
            evaluated_at=evaluated_at,
            blocking_contradiction_ids=tuple(
                item.contradiction_id for item in blocking
            ),
        )

    return _membrane_verdict(
        candidate,
        ADMIT,
        "candidate admitted with intact transition and provenance lineage",
        evaluated_at=evaluated_at,
    )


def evaluate_citation(
    candidate: CarriageCandidate,
    admission: MembraneVerdict,
    *,
    contradictions: tuple[ContradictionObject, ...] | list[ContradictionObject] = (),
    now: float | None = None,
) -> CitationDecision:
    """Evaluate current citation rights without rewriting historical admission."""

    evaluated_at = time.time() if now is None else float(now)

    if admission.carriage_id != candidate.carriage_id:
        return CitationDecision(
            carriage_id=candidate.carriage_id,
            allowed=False,
            reason="admission receipt belongs to a different carriage",
            evaluated_at=evaluated_at,
        )

    if admission.decision != ADMIT:
        return CitationDecision(
            carriage_id=candidate.carriage_id,
            allowed=False,
            reason=f"carriage was not admitted: {admission.decision}",
            evaluated_at=evaluated_at,
        )

    blocking = tuple(
        item
        for item in contradictions
        if item.is_open
        and (
            item.blocks_claim(candidate.claim_key)
            or item.blocks_carriage(candidate.carriage_id)
        )
    )
    if blocking:
        return CitationDecision(
            carriage_id=candidate.carriage_id,
            allowed=False,
            reason="citation blocked by open contradiction",
            blocking_contradiction_ids=tuple(
                item.contradiction_id for item in blocking
            ),
            evaluated_at=evaluated_at,
        )

    return CitationDecision(
        carriage_id=candidate.carriage_id,
        allowed=True,
        reason="carriage currently has citation rights",
        evaluated_at=evaluated_at,
    )


def _blocking_contradictions(
    candidate: CarriageCandidate,
    contradictions: tuple[ContradictionObject, ...] | list[ContradictionObject],
) -> tuple[ContradictionObject, ...]:
    return tuple(
        item
        for item in contradictions
        if item.is_open
        and (
            item.blocks_claim(candidate.claim_key)
            or item.blocks_carriage(candidate.carriage_id)
            or any(
                item.blocks_carriage(parent_id)
                for parent_id in candidate.parent_carriage_ids
            )
        )
    )


def _membrane_verdict(
    candidate: CarriageCandidate,
    decision: str,
    reason: str,
    *,
    evaluated_at: float,
    missing_provenance_refs: tuple[str, ...] = (),
    missing_parent_carriage_ids: tuple[str, ...] = (),
    blocking_contradiction_ids: tuple[str, ...] = (),
) -> MembraneVerdict:
    return MembraneVerdict(
        membrane_verdict_id=str(uuid.uuid4()),
        carriage_id=candidate.carriage_id,
        decision=decision,
        citation_allowed_at_admission=decision == ADMIT,
        reason=reason,
        missing_provenance_refs=missing_provenance_refs,
        missing_parent_carriage_ids=missing_parent_carriage_ids,
        blocking_contradiction_ids=blocking_contradiction_ids,
        evaluated_at=evaluated_at,
    )
