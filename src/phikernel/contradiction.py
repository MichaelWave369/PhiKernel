from __future__ import annotations

"""PhiKernel contradiction objects.

Contradiction is machine state, not a warning string. An open contradiction
blocks citation/use of the affected claim or exhibit carriages until an
externally authorized resolution is recorded.

This module does not decide which claim is true and never auto-resolves based
on confidence, popularity, fluency, or historical success.
"""

from dataclasses import dataclass, field, replace
from typing import Any
import time
import uuid


CONTRADICTION_VERSION = "0.2.0"

OPEN = "OPEN"
RESOLVED = "RESOLVED"

HUMAN_SEAL = "HUMAN_SEAL"
TEST_WARRANT = "TEST_WARRANT"
SCOPED_EXPERIMENT = "SCOPED_EXPERIMENT"
VALID_RESOLUTION_KINDS = {HUMAN_SEAL, TEST_WARRANT, SCOPED_EXPERIMENT}


class ContradictionError(Exception):
    """Base exception for contradiction-state failures."""


@dataclass(frozen=True)
class ContradictionObject:
    contradiction_id: str
    claim_key: str
    exhibit_carriage_ids: tuple[str, ...]
    reason: str
    opened_at: float = field(default_factory=time.time)
    status: str = OPEN
    resolved_at: float | None = None
    resolution_kind: str | None = None
    resolver_id: str | None = None
    authority_ref: str | None = None
    resolution_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = CONTRADICTION_VERSION

    def __post_init__(self) -> None:
        if not self.contradiction_id.strip():
            raise ContradictionError("contradiction_id must be non-empty")
        if not self.claim_key.strip():
            raise ContradictionError("claim_key must be non-empty")
        if len(self.exhibit_carriage_ids) < 2:
            raise ContradictionError("contradiction requires at least two exhibit carriages")
        if len(set(self.exhibit_carriage_ids)) != len(self.exhibit_carriage_ids):
            raise ContradictionError("exhibit carriage ids must be unique")
        if any(not item.strip() for item in self.exhibit_carriage_ids):
            raise ContradictionError("exhibit carriage ids must be non-empty")
        if not self.reason.strip():
            raise ContradictionError("contradiction reason must be non-empty")
        if self.status not in {OPEN, RESOLVED}:
            raise ContradictionError("invalid contradiction status")

        if self.status == OPEN:
            if any(
                value is not None
                for value in (
                    self.resolved_at,
                    self.resolution_kind,
                    self.resolver_id,
                    self.authority_ref,
                    self.resolution_ref,
                )
            ):
                raise ContradictionError("open contradiction may not contain resolution fields")

        if self.status == RESOLVED:
            if self.resolved_at is None:
                raise ContradictionError("resolved contradiction requires resolved_at")
            if self.resolution_kind not in VALID_RESOLUTION_KINDS:
                raise ContradictionError("resolved contradiction requires a valid resolution kind")
            if not (self.resolver_id or "").strip():
                raise ContradictionError("resolved contradiction requires resolver_id")
            if not (self.authority_ref or "").strip():
                raise ContradictionError("resolved contradiction requires authority_ref")
            if not (self.resolution_ref or "").strip():
                raise ContradictionError("resolved contradiction requires resolution_ref")

    @classmethod
    def open(
        cls,
        *,
        claim_key: str,
        exhibit_carriage_ids: tuple[str, ...] | list[str],
        reason: str,
        opened_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ContradictionObject":
        return cls(
            contradiction_id=str(uuid.uuid4()),
            claim_key=claim_key,
            exhibit_carriage_ids=tuple(exhibit_carriage_ids),
            reason=reason,
            opened_at=time.time() if opened_at is None else float(opened_at),
            metadata=dict(metadata or {}),
        )

    @property
    def is_open(self) -> bool:
        return self.status == OPEN

    def blocks_claim(self, claim_key: str) -> bool:
        return self.is_open and self.claim_key == claim_key

    def blocks_carriage(self, carriage_id: str) -> bool:
        return self.is_open and carriage_id in self.exhibit_carriage_ids

    def resolve(
        self,
        *,
        resolution_kind: str,
        resolver_id: str,
        authority_ref: str,
        resolution_ref: str,
        resolved_at: float | None = None,
    ) -> "ContradictionObject":
        """Record externally authorized resolution without choosing a winner.

        authority_ref is intentionally opaque here. A later integration layer
        must verify that the referenced human seal, test warrant, or scoped
        experiment is itself valid before calling this method.
        """
        if not self.is_open:
            raise ContradictionError("contradiction is already resolved")
        if resolution_kind not in VALID_RESOLUTION_KINDS:
            raise ContradictionError(
                f"resolution_kind must be one of {sorted(VALID_RESOLUTION_KINDS)}"
            )
        if not resolver_id.strip():
            raise ContradictionError("resolver_id must be non-empty")
        if not authority_ref.strip():
            raise ContradictionError("authority_ref must be non-empty")
        if not resolution_ref.strip():
            raise ContradictionError("resolution_ref must be non-empty")

        return replace(
            self,
            status=RESOLVED,
            resolved_at=time.time() if resolved_at is None else float(resolved_at),
            resolution_kind=resolution_kind,
            resolver_id=resolver_id,
            authority_ref=authority_ref,
            resolution_ref=resolution_ref,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "contradiction_id": self.contradiction_id,
            "claim_key": self.claim_key,
            "exhibit_carriage_ids": list(self.exhibit_carriage_ids),
            "reason": self.reason,
            "opened_at": self.opened_at,
            "status": self.status,
            "resolved_at": self.resolved_at,
            "resolution_kind": self.resolution_kind,
            "resolver_id": self.resolver_id,
            "authority_ref": self.authority_ref,
            "resolution_ref": self.resolution_ref,
            "metadata": dict(self.metadata),
        }


def open_contradictions_for_claim(
    contradictions: tuple[ContradictionObject, ...] | list[ContradictionObject],
    claim_key: str,
) -> tuple[ContradictionObject, ...]:
    return tuple(item for item in contradictions if item.blocks_claim(claim_key))


def open_contradictions_for_carriage(
    contradictions: tuple[ContradictionObject, ...] | list[ContradictionObject],
    carriage_id: str,
) -> tuple[ContradictionObject, ...]:
    return tuple(item for item in contradictions if item.blocks_carriage(carriage_id))
