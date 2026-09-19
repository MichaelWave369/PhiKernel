from __future__ import annotations

"""PhiKernel governed transition contracts.

The transition is the unit of governance. A process, model, tool, sensor, or
Protocol Organ may propose a change; PhiKernel decides whether that exact change
is admissible under a live warrant.

This module intentionally does not replace ANC/trust governance. It provides the
constitutional proposal/verdict contract that those layers can later enrich
with DEGRADE and QUARANTINE decisions.
"""

from dataclasses import dataclass, field
from typing import Any
import time
import uuid

from phikernel.warrant import (
    Warrant,
    WarrantBudgetExceededError,
    WarrantDeniedError,
    WarrantSpendReceipt,
)


TRANSITION_VERSION = "0.2.0"

LICENSE = "LICENSE"
DEGRADE = "DEGRADE"
QUARANTINE = "QUARANTINE"
REFUSE = "REFUSE"
VALID_DECISIONS = {LICENSE, DEGRADE, QUARANTINE, REFUSE}


class TransitionError(Exception):
    """Base exception for governed transition failures."""


@dataclass(frozen=True)
class ResourceSpend:
    kind: str
    amount: float

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise TransitionError("resource kind must be non-empty")
        if self.amount < 0:
            raise TransitionError("resource amount must be >= 0")


@dataclass(frozen=True)
class TransitionProposal:
    proposal_id: str
    actor_id: str
    operation: str
    target: str
    warrant_id: str
    resource_spends: tuple[ResourceSpend, ...] = ()
    provenance_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    requested_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = TRANSITION_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("proposal_id", self.proposal_id),
            ("actor_id", self.actor_id),
            ("operation", self.operation),
            ("target", self.target),
            ("warrant_id", self.warrant_id),
        ):
            if not value.strip():
                raise TransitionError(f"{name} must be non-empty")

        kinds = [spend.kind for spend in self.resource_spends]
        if len(kinds) != len(set(kinds)):
            raise TransitionError("resource_spends may contain each resource kind only once")

    @classmethod
    def create(
        cls,
        *,
        actor_id: str,
        operation: str,
        target: str,
        warrant_id: str,
        resource_spends: tuple[ResourceSpend, ...] | list[ResourceSpend] = (),
        provenance_refs: tuple[str, ...] | list[str] = (),
        evidence_refs: tuple[str, ...] | list[str] = (),
        requested_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "TransitionProposal":
        return cls(
            proposal_id=str(uuid.uuid4()),
            actor_id=actor_id,
            operation=operation,
            target=target,
            warrant_id=warrant_id,
            resource_spends=tuple(resource_spends),
            provenance_refs=tuple(provenance_refs),
            evidence_refs=tuple(evidence_refs),
            requested_at=time.time() if requested_at is None else float(requested_at),
            metadata=dict(metadata or {}),
        )

    @property
    def requested_scope(self) -> str:
        return f"{self.operation}:{self.target}"

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "proposal_id": self.proposal_id,
            "actor_id": self.actor_id,
            "operation": self.operation,
            "target": self.target,
            "requested_scope": self.requested_scope,
            "warrant_id": self.warrant_id,
            "resource_spends": [
                {"kind": spend.kind, "amount": spend.amount}
                for spend in self.resource_spends
            ],
            "provenance_refs": list(self.provenance_refs),
            "evidence_refs": list(self.evidence_refs),
            "requested_at": self.requested_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TransitionVerdict:
    verdict_id: str
    proposal_id: str
    decision: str
    allowed: bool
    reason: str
    warrant_id: str
    evaluated_at: float
    authority_change: str = "NONE"
    claims_promoted: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = TRANSITION_VERSION

    def __post_init__(self) -> None:
        if self.decision not in VALID_DECISIONS:
            raise TransitionError(
                f"decision must be one of {sorted(VALID_DECISIONS)}"
            )
        if self.decision == LICENSE and not self.allowed:
            raise TransitionError("LICENSE verdict must be allowed")
        if self.decision in {QUARANTINE, REFUSE} and self.allowed:
            raise TransitionError(f"{self.decision} verdict may not be allowed")
        if self.authority_change != "NONE":
            raise TransitionError("transition evaluation may not grant authority")
        if self.claims_promoted != 0:
            raise TransitionError("transition evaluation may not promote claims")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "verdict_id": self.verdict_id,
            "proposal_id": self.proposal_id,
            "decision": self.decision,
            "allowed": self.allowed,
            "reason": self.reason,
            "warrant_id": self.warrant_id,
            "evaluated_at": self.evaluated_at,
            "authority_change": self.authority_change,
            "claims_promoted": self.claims_promoted,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TransitionEvaluation:
    proposal: TransitionProposal
    verdict: TransitionVerdict
    warrant_after: Warrant
    spend_receipts: tuple[WarrantSpendReceipt, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal.to_record(),
            "verdict": self.verdict.to_record(),
            "warrant_after": self.warrant_after.to_record(),
            "spend_receipts": [receipt.to_record() for receipt in self.spend_receipts],
        }


def evaluate_transition(
    proposal: TransitionProposal,
    warrant: Warrant,
    *,
    now: float | None = None,
) -> TransitionEvaluation:
    """Evaluate one proposed state transition against one explicit warrant.

    This first constitutional evaluator is intentionally narrow:
    - scope/bearer/lifetime/revocation checks
    - hard budget checks
    - deterministic LICENSE or REFUSE

    ANC/trust layers can later wrap this primitive to introduce DEGRADE and
    QUARANTINE while preserving the same proposal identity.
    """

    evaluated_at = time.time() if now is None else float(now)

    if proposal.warrant_id != warrant.warrant_id:
        return _refused(
            proposal,
            warrant,
            evaluated_at,
            "proposal warrant_id does not match supplied warrant",
        )

    check = warrant.authorize(
        actor_id=proposal.actor_id,
        operation=proposal.operation,
        target=proposal.target,
        now=evaluated_at,
    )
    if not check.allowed:
        return _refused(proposal, warrant, evaluated_at, check.reason)

    # Preflight every resource before mutating the immutable warrant copy.
    for spend in proposal.resource_spends:
        budget = warrant.budget(spend.kind)
        if budget is None:
            return _refused(
                proposal,
                warrant,
                evaluated_at,
                f"resource '{spend.kind}' is not granted by this warrant",
            )
        if spend.amount > budget.remaining:
            return _refused(
                proposal,
                warrant,
                evaluated_at,
                (
                    f"budget '{spend.kind}' exhausted: "
                    f"requested={spend.amount}, remaining={budget.remaining}"
                ),
            )

    updated = warrant
    receipts: list[WarrantSpendReceipt] = []
    try:
        for spend in proposal.resource_spends:
            updated, receipt = updated.spend(
                actor_id=proposal.actor_id,
                resource_kind=spend.kind,
                amount=spend.amount,
                now=evaluated_at,
            )
            receipts.append(receipt)
    except (WarrantDeniedError, WarrantBudgetExceededError) as exc:
        # The preflight above should make this unreachable for deterministic
        # single-threaded evaluation. Fail closed if invariants ever diverge.
        return _refused(
            proposal,
            warrant,
            evaluated_at,
            f"warrant spend invariant failed: {exc}",
        )

    verdict = TransitionVerdict(
        verdict_id=str(uuid.uuid4()),
        proposal_id=proposal.proposal_id,
        decision=LICENSE,
        allowed=True,
        reason="transition licensed by explicit warrant",
        warrant_id=warrant.warrant_id,
        evaluated_at=evaluated_at,
        metadata={
            "requested_scope": proposal.requested_scope,
            "resource_spend_count": len(receipts),
        },
    )
    return TransitionEvaluation(
        proposal=proposal,
        verdict=verdict,
        warrant_after=updated,
        spend_receipts=tuple(receipts),
    )


def _refused(
    proposal: TransitionProposal,
    warrant: Warrant,
    evaluated_at: float,
    reason: str,
) -> TransitionEvaluation:
    verdict = TransitionVerdict(
        verdict_id=str(uuid.uuid4()),
        proposal_id=proposal.proposal_id,
        decision=REFUSE,
        allowed=False,
        reason=reason,
        warrant_id=warrant.warrant_id,
        evaluated_at=evaluated_at,
        metadata={"requested_scope": proposal.requested_scope},
    )
    return TransitionEvaluation(
        proposal=proposal,
        verdict=verdict,
        warrant_after=warrant,
        spend_receipts=(),
    )
