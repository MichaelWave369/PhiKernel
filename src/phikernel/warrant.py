from __future__ import annotations

"""PhiKernel warrant primitives.

A Warrant is a temporary, scoped, budgeted grant to attempt governed work.
It is deliberately separate from capability, routing preference, success history,
or model confidence.

Design invariants:
- capability does not imply authority
- warrants are bearer-bound and non-transferable by construction
- budgets never overdraft
- expiry/revocation fail closed
- spending a warrant cannot enlarge its scope, lifetime, or budget
"""

from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from typing import Any
import time
import uuid


WARRANT_VERSION = "0.2.0"


class WarrantError(Exception):
    """Base exception for warrant failures."""


class WarrantDeniedError(WarrantError):
    """Raised when a bearer/scope/lifetime check denies an act."""


class WarrantBudgetExceededError(WarrantError):
    """Raised when a resource spend would exceed the granted budget."""


@dataclass(frozen=True)
class ResourceBudget:
    kind: str
    limit: float
    spent: float = 0.0

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise WarrantError("budget kind must be non-empty")
        if self.limit < 0:
            raise WarrantError("budget limit must be >= 0")
        if self.spent < 0:
            raise WarrantError("budget spent must be >= 0")
        if self.spent > self.limit:
            raise WarrantError("budget spent may not exceed limit")

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.spent)

    def debit(self, amount: float) -> "ResourceBudget":
        if amount < 0:
            raise WarrantError("budget debit amount must be >= 0")
        if amount > self.remaining:
            raise WarrantBudgetExceededError(
                f"budget '{self.kind}' exhausted: requested={amount}, remaining={self.remaining}"
            )
        return replace(self, spent=self.spent + amount)


@dataclass(frozen=True)
class WarrantCheck:
    allowed: bool
    reason: str
    warrant_id: str
    bearer: str
    requested_scope: str
    expires_at: float | None
    revoked: bool

    def to_record(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "warrant_id": self.warrant_id,
            "bearer": self.bearer,
            "requested_scope": self.requested_scope,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
        }


@dataclass(frozen=True)
class WarrantSpendReceipt:
    receipt_id: str
    warrant_id: str
    bearer: str
    resource_kind: str
    amount: float
    before_remaining: float
    after_remaining: float
    spent_at: float
    authority_change: str = "NONE"

    def to_record(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "warrant_id": self.warrant_id,
            "bearer": self.bearer,
            "resource_kind": self.resource_kind,
            "amount": self.amount,
            "before_remaining": self.before_remaining,
            "after_remaining": self.after_remaining,
            "spent_at": self.spent_at,
            "authority_change": self.authority_change,
        }


@dataclass(frozen=True)
class Warrant:
    """Immutable authority lease.

    Scope entries are deterministic glob patterns over strings shaped as
    operation:target. Examples include read:memory/* and execute:tool/python.
    """

    warrant_id: str
    issuer: str
    bearer: str
    scopes: tuple[str, ...]
    budgets: tuple[ResourceBudget, ...] = ()
    issued_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    revoked: bool = False
    revocation_reason: str | None = None
    parent_warrant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = WARRANT_VERSION

    def __post_init__(self) -> None:
        if not self.warrant_id.strip():
            raise WarrantError("warrant_id must be non-empty")
        if not self.issuer.strip():
            raise WarrantError("issuer must be non-empty")
        if not self.bearer.strip():
            raise WarrantError("bearer must be non-empty")
        if not self.scopes:
            raise WarrantError("warrant must contain at least one scope")
        if any(not scope.strip() for scope in self.scopes):
            raise WarrantError("warrant scopes must be non-empty")
        kinds = [budget.kind for budget in self.budgets]
        if len(kinds) != len(set(kinds)):
            raise WarrantError("budget kinds must be unique")
        if self.expires_at is not None and self.expires_at < self.issued_at:
            raise WarrantError("expires_at may not precede issued_at")

    @classmethod
    def issue(
        cls,
        *,
        issuer: str,
        bearer: str,
        scopes: tuple[str, ...] | list[str],
        budgets: tuple[ResourceBudget, ...] | list[ResourceBudget] = (),
        lifetime_seconds: float | None = None,
        issued_at: float | None = None,
        parent_warrant_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Warrant":
        now = time.time() if issued_at is None else float(issued_at)
        if lifetime_seconds is not None and lifetime_seconds < 0:
            raise WarrantError("lifetime_seconds must be >= 0")
        expires_at = None if lifetime_seconds is None else now + lifetime_seconds
        return cls(
            warrant_id=str(uuid.uuid4()),
            issuer=issuer,
            bearer=bearer,
            scopes=tuple(scopes),
            budgets=tuple(budgets),
            issued_at=now,
            expires_at=expires_at,
            parent_warrant_id=parent_warrant_id,
            metadata=dict(metadata or {}),
        )

    def is_expired(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        return self.expires_at is not None and current >= self.expires_at

    def budget(self, kind: str) -> ResourceBudget | None:
        return next((budget for budget in self.budgets if budget.kind == kind), None)

    def remaining(self, kind: str) -> float | None:
        budget = self.budget(kind)
        return None if budget is None else budget.remaining

    def authorize(
        self,
        *,
        actor_id: str,
        operation: str,
        target: str,
        now: float | None = None,
    ) -> WarrantCheck:
        requested_scope = f"{operation}:{target}"

        if actor_id != self.bearer:
            return WarrantCheck(
                allowed=False,
                reason="bearer mismatch; warrants are non-transferable",
                warrant_id=self.warrant_id,
                bearer=self.bearer,
                requested_scope=requested_scope,
                expires_at=self.expires_at,
                revoked=self.revoked,
            )

        if self.revoked:
            return WarrantCheck(
                allowed=False,
                reason=f"warrant revoked: {self.revocation_reason or 'unspecified'}",
                warrant_id=self.warrant_id,
                bearer=self.bearer,
                requested_scope=requested_scope,
                expires_at=self.expires_at,
                revoked=True,
            )

        if self.is_expired(now=now):
            return WarrantCheck(
                allowed=False,
                reason="warrant expired",
                warrant_id=self.warrant_id,
                bearer=self.bearer,
                requested_scope=requested_scope,
                expires_at=self.expires_at,
                revoked=False,
            )

        if not any(fnmatchcase(requested_scope, pattern) for pattern in self.scopes):
            return WarrantCheck(
                allowed=False,
                reason="requested operation is outside warrant scope",
                warrant_id=self.warrant_id,
                bearer=self.bearer,
                requested_scope=requested_scope,
                expires_at=self.expires_at,
                revoked=False,
            )

        return WarrantCheck(
            allowed=True,
            reason="warrant permits requested operation",
            warrant_id=self.warrant_id,
            bearer=self.bearer,
            requested_scope=requested_scope,
            expires_at=self.expires_at,
            revoked=False,
        )

    def spend(
        self,
        *,
        actor_id: str,
        resource_kind: str,
        amount: float,
        now: float | None = None,
    ) -> tuple["Warrant", WarrantSpendReceipt]:
        current = time.time() if now is None else float(now)

        if actor_id != self.bearer:
            raise WarrantDeniedError("bearer mismatch; warrants are non-transferable")
        if self.revoked:
            raise WarrantDeniedError(
                f"warrant revoked: {self.revocation_reason or 'unspecified'}"
            )
        if self.is_expired(now=current):
            raise WarrantDeniedError("warrant expired")

        existing = self.budget(resource_kind)
        if existing is None:
            raise WarrantDeniedError(
                f"resource '{resource_kind}' is not granted by this warrant"
            )

        updated_budget = existing.debit(amount)
        updated_budgets = tuple(
            updated_budget if budget.kind == resource_kind else budget
            for budget in self.budgets
        )
        updated = replace(self, budgets=updated_budgets)

        return updated, WarrantSpendReceipt(
            receipt_id=str(uuid.uuid4()),
            warrant_id=self.warrant_id,
            bearer=self.bearer,
            resource_kind=resource_kind,
            amount=amount,
            before_remaining=existing.remaining,
            after_remaining=updated_budget.remaining,
            spent_at=current,
        )

    def revoke(self, *, reason: str) -> "Warrant":
        if not reason.strip():
            raise WarrantError("revocation reason must be non-empty")
        return replace(self, revoked=True, revocation_reason=reason)

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "warrant_id": self.warrant_id,
            "issuer": self.issuer,
            "bearer": self.bearer,
            "scopes": list(self.scopes),
            "budgets": [
                {
                    "kind": budget.kind,
                    "limit": budget.limit,
                    "spent": budget.spent,
                    "remaining": budget.remaining,
                }
                for budget in self.budgets
            ],
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
            "revocation_reason": self.revocation_reason,
            "parent_warrant_id": self.parent_warrant_id,
            "metadata": dict(self.metadata),
        }
