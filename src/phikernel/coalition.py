from __future__ import annotations

"""PhiKernel ensemble / coalition charters.

A coalition is a temporary governed identity formed from multiple participants.
Members contribute capability, but coalition authority belongs only to the
coalition identity and expires with the charter/warrant.

Core invariants:
- member capability does not imply coalition authority
- coalition authority does not transfer back to members
- coalition warrants must be strict subsets of a sponsoring warrant
- coalition lifetime may not outlive the sponsor
- coalition budgets may not exceed sponsor remaining budgets
- success is credited to the coalition identity, not promoted into member authority
- disbanding revokes the coalition warrant and leaves zero temporary grants
"""

from dataclasses import dataclass, field, replace
from typing import Any
import time
import uuid

from phikernel.transition import TransitionEvaluation
from phikernel.warrant import ResourceBudget, Warrant, WarrantSpendReceipt


COALITION_VERSION = "0.2.0"

DRAFT = "DRAFT"
ACTIVE = "ACTIVE"
DEGRADED = "DEGRADED"
DISSOLVED = "DISSOLVED"
VALID_STATES = {DRAFT, ACTIVE, DEGRADED, DISSOLVED}


class CoalitionError(Exception):
    """Base exception for coalition lifecycle failures."""


@dataclass(frozen=True)
class MemberCapability:
    member_id: str
    capability_tags: tuple[str, ...]
    source_ref: str

    def __post_init__(self) -> None:
        if not self.member_id.strip():
            raise CoalitionError("member_id must be non-empty")
        if not self.capability_tags:
            raise CoalitionError("member must contribute at least one capability")
        if any(not tag.strip() for tag in self.capability_tags):
            raise CoalitionError("capability tags must be non-empty")
        if len(self.capability_tags) != len(set(self.capability_tags)):
            raise CoalitionError("capability tags must be unique per member")
        if not self.source_ref.strip():
            raise CoalitionError("source_ref must be non-empty")


@dataclass(frozen=True)
class CoalitionCharter:
    coalition_id: str
    purpose: str
    members: tuple[MemberCapability, ...]
    created_at: float
    expires_at: float
    state: str = DRAFT
    active_member_ids: tuple[str, ...] = ()
    degradation_reason: str | None = None
    dissolved_at: float | None = None
    authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = COALITION_VERSION

    def __post_init__(self) -> None:
        if not self.coalition_id.strip():
            raise CoalitionError("coalition_id must be non-empty")
        if not self.purpose.strip():
            raise CoalitionError("purpose must be non-empty")
        if len(self.members) < 2:
            raise CoalitionError("coalition requires at least two distinct members")
        member_ids = [member.member_id for member in self.members]
        if len(member_ids) != len(set(member_ids)):
            raise CoalitionError("coalition member ids must be unique")
        if self.expires_at <= self.created_at:
            raise CoalitionError("coalition expires_at must be after created_at")
        if self.state not in VALID_STATES:
            raise CoalitionError("invalid coalition state")
        if self.authority_change != "NONE":
            raise CoalitionError("charter creation may not itself grant authority")
        if self.constitutional_change != "NONE":
            raise CoalitionError("coalition lifecycle may not change constitution")

        known = set(member_ids)
        if any(member_id not in known for member_id in self.active_member_ids):
            raise CoalitionError("active_member_ids must belong to charter members")
        if len(self.active_member_ids) != len(set(self.active_member_ids)):
            raise CoalitionError("active_member_ids must be unique")

        if self.state == DRAFT and self.active_member_ids:
            raise CoalitionError("draft coalition may not have active members")
        if self.state in {ACTIVE, DEGRADED} and len(self.active_member_ids) < 2:
            raise CoalitionError("active/degraded coalition requires at least two active members")
        if self.state == DEGRADED and not (self.degradation_reason or "").strip():
            raise CoalitionError("degraded coalition requires degradation_reason")
        if self.state == DISSOLVED and self.dissolved_at is None:
            raise CoalitionError("dissolved coalition requires dissolved_at")

    @classmethod
    def draft(
        cls,
        *,
        purpose: str,
        members: tuple[MemberCapability, ...] | list[MemberCapability],
        lifetime_seconds: float,
        created_at: float | None = None,
    ) -> "CoalitionCharter":
        if lifetime_seconds <= 0:
            raise CoalitionError("lifetime_seconds must be > 0")
        timestamp = time.time() if created_at is None else float(created_at)
        return cls(
            coalition_id=f"coalition:{uuid.uuid4()}",
            purpose=purpose,
            members=tuple(members),
            created_at=timestamp,
            expires_at=timestamp + float(lifetime_seconds),
        )

    @property
    def member_ids(self) -> tuple[str, ...]:
        return tuple(member.member_id for member in self.members)

    @property
    def capability_union(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    tag
                    for member in self.members
                    if (
                        self.state == DRAFT
                        or member.member_id in set(self.active_member_ids)
                    )
                    for tag in member.capability_tags
                }
            )
        )

    def active_at(self, now: float) -> bool:
        return (
            self.state in {ACTIVE, DEGRADED}
            and now < self.expires_at
            and self.dissolved_at is None
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "coalition_id": self.coalition_id,
            "purpose": self.purpose,
            "members": [
                {
                    "member_id": member.member_id,
                    "capability_tags": list(member.capability_tags),
                    "source_ref": member.source_ref,
                }
                for member in self.members
            ],
            "capability_union": list(self.capability_union),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "state": self.state,
            "active_member_ids": list(self.active_member_ids),
            "degradation_reason": self.degradation_reason,
            "dissolved_at": self.dissolved_at,
            "authority_change": self.authority_change,
            "constitutional_change": self.constitutional_change,
        }


@dataclass(frozen=True)
class CoalitionActivationReceipt:
    receipt_id: str
    coalition_id: str
    activated_member_ids: tuple[str, ...]
    activated_at: float
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    constitutional_change: str = "NONE"


@dataclass(frozen=True)
class CoalitionDegradationReceipt:
    receipt_id: str
    coalition_id: str
    prior_member_ids: tuple[str, ...]
    active_member_ids: tuple[str, ...]
    reason: str
    degraded_at: float
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    constitutional_change: str = "NONE"


@dataclass(frozen=True)
class CoalitionOutcomeReceipt:
    receipt_id: str
    coalition_id: str
    transition_verdict_id: str
    success: bool
    source_ref: str
    recorded_at: float
    credit_subject_id: str
    member_authority_inheritance: str = "NONE"
    authority_change: str = "NONE"
    constitutional_change: str = "NONE"

    def __post_init__(self) -> None:
        if self.credit_subject_id != self.coalition_id:
            raise CoalitionError("coalition outcome credit must remain with coalition identity")
        if self.member_authority_inheritance != "NONE":
            raise CoalitionError("coalition outcomes may not promote member authority")
        if self.authority_change != "NONE":
            raise CoalitionError("coalition outcomes may not change authority")
        if self.constitutional_change != "NONE":
            raise CoalitionError("coalition outcomes may not change constitution")


@dataclass(frozen=True)
class CoalitionWarrantGrant:
    coalition_warrant: Warrant
    sponsor_warrant_after: Warrant
    reservation_receipts: tuple[WarrantSpendReceipt, ...]
    reserved_budgets: tuple[ResourceBudget, ...]
    authority_change: str = "NONE"
    constitutional_change: str = "NONE"

    def __post_init__(self) -> None:
        if self.coalition_warrant.parent_warrant_id is None:
            raise CoalitionError("coalition warrant must name a parent sponsor warrant")
        if self.authority_change != "NONE":
            raise CoalitionError("warrant reservation may not grant extra authority")
        if self.constitutional_change != "NONE":
            raise CoalitionError("warrant reservation may not change constitution")
        if len(self.reservation_receipts) != len(self.reserved_budgets):
            raise CoalitionError("each reserved budget requires one reservation receipt")
        for receipt in self.reservation_receipts:
            if receipt.authority_change != "NONE":
                raise CoalitionError("reservation receipt may not change authority")


@dataclass(frozen=True)
class CoalitionDisbandReceipt:
    receipt_id: str
    coalition_id: str
    warrant_id: str
    dissolved_at: float
    warrant_revoked: bool
    temporary_grants_remaining: int
    member_authority_inheritance: str = "NONE"
    constitutional_change: str = "NONE"

    def __post_init__(self) -> None:
        if not self.warrant_revoked:
            raise CoalitionError("disband receipt requires coalition warrant revocation")
        if self.temporary_grants_remaining != 0:
            raise CoalitionError("disband must leave zero temporary grants")
        if self.member_authority_inheritance != "NONE":
            raise CoalitionError("members may not inherit coalition authority")
        if self.constitutional_change != "NONE":
            raise CoalitionError("disbanding may not change constitution")


def activate_coalition(
    charter: CoalitionCharter,
    *,
    active_member_ids: tuple[str, ...] | list[str] | None = None,
    activated_at: float | None = None,
) -> tuple[CoalitionCharter, CoalitionActivationReceipt]:
    if charter.state != DRAFT:
        raise CoalitionError("only DRAFT coalition may be activated")
    timestamp = time.time() if activated_at is None else float(activated_at)
    if timestamp >= charter.expires_at:
        raise CoalitionError("cannot activate expired coalition")

    member_ids = (
        charter.member_ids
        if active_member_ids is None
        else tuple(active_member_ids)
    )
    if len(member_ids) < 2:
        raise CoalitionError("activation requires at least two active members")
    if any(member_id not in set(charter.member_ids) for member_id in member_ids):
        raise CoalitionError("activation includes unknown member")
    if len(member_ids) != len(set(member_ids)):
        raise CoalitionError("activation member ids must be unique")

    updated = replace(
        charter,
        state=ACTIVE,
        active_member_ids=member_ids,
    )
    receipt = CoalitionActivationReceipt(
        receipt_id=str(uuid.uuid4()),
        coalition_id=charter.coalition_id,
        activated_member_ids=member_ids,
        activated_at=timestamp,
    )
    return updated, receipt


def issue_coalition_warrant(
    charter: CoalitionCharter,
    sponsor_warrant: Warrant,
    *,
    scopes: tuple[str, ...] | list[str],
    budgets: tuple[ResourceBudget, ...] | list[ResourceBudget] = (),
    issued_at: float | None = None,
) -> CoalitionWarrantGrant:
    """Create and reserve a strict child warrant for the coalition identity.

    v0.2 requires exact sponsor-scope subset membership. Child resource budgets
    are atomically reserved from the sponsor at grant time so multiple coalitions
    cannot each spend the same apparent remaining capacity.
    """
    timestamp = time.time() if issued_at is None else float(issued_at)

    if not charter.active_at(timestamp):
        raise CoalitionError("coalition must be active and unexpired")
    if sponsor_warrant.revoked:
        raise CoalitionError("sponsor warrant is revoked")
    if sponsor_warrant.is_expired(now=timestamp):
        raise CoalitionError("sponsor warrant is expired")

    requested_scopes = tuple(scopes)
    if not requested_scopes:
        raise CoalitionError("coalition warrant requires at least one scope")
    if any(scope not in set(sponsor_warrant.scopes) for scope in requested_scopes):
        raise CoalitionError(
            "coalition warrant scope must be an exact subset of sponsor scopes"
        )

    requested_budgets = tuple(budgets)
    requested_kinds = [budget.kind for budget in requested_budgets]
    if len(requested_kinds) != len(set(requested_kinds)):
        raise CoalitionError("coalition budget kinds must be unique")

    # Full preflight before creating either immutable successor object.
    for budget in requested_budgets:
        sponsor_budget = sponsor_warrant.budget(budget.kind)
        if sponsor_budget is None:
            raise CoalitionError(
                f"coalition budget '{budget.kind}' is not granted by sponsor"
            )
        if budget.spent != 0:
            raise CoalitionError("coalition budget must begin with spent=0")
        if budget.limit > sponsor_budget.remaining:
            raise CoalitionError(
                f"coalition budget '{budget.kind}' exceeds sponsor remaining budget"
            )

    sponsor_expiry = sponsor_warrant.expires_at
    expires_at = charter.expires_at
    if sponsor_expiry is not None:
        expires_at = min(expires_at, sponsor_expiry)

    lifetime = expires_at - timestamp
    if lifetime <= 0:
        raise CoalitionError("coalition warrant would have no positive lifetime")

    sponsor_after = sponsor_warrant
    reservations: list[WarrantSpendReceipt] = []
    for budget in requested_budgets:
        sponsor_after, receipt = sponsor_after.spend(
            actor_id=sponsor_warrant.bearer,
            resource_kind=budget.kind,
            amount=budget.limit,
            now=timestamp,
        )
        reservations.append(receipt)

    child = Warrant.issue(
        issuer=sponsor_warrant.issuer,
        bearer=charter.coalition_id,
        scopes=requested_scopes,
        budgets=requested_budgets,
        lifetime_seconds=lifetime,
        issued_at=timestamp,
        parent_warrant_id=sponsor_warrant.warrant_id,
        metadata={
            "coalition_id": charter.coalition_id,
            "coalition_purpose": charter.purpose,
            "sponsor_warrant_id": sponsor_warrant.warrant_id,
            "member_ids": list(charter.active_member_ids),
            "budget_reservation": "CONSUMED_AT_GRANT",
        },
    )

    return CoalitionWarrantGrant(
        coalition_warrant=child,
        sponsor_warrant_after=sponsor_after,
        reservation_receipts=tuple(reservations),
        reserved_budgets=requested_budgets,
    )


def degrade_coalition(
    charter: CoalitionCharter,
    *,
    unavailable_member_ids: tuple[str, ...] | list[str],
    reason: str,
    degraded_at: float | None = None,
) -> tuple[CoalitionCharter, CoalitionDegradationReceipt]:
    if charter.state not in {ACTIVE, DEGRADED}:
        raise CoalitionError("only active/degraded coalition may degrade")
    if not reason.strip():
        raise CoalitionError("degradation reason must be non-empty")

    unavailable = set(unavailable_member_ids)
    if not unavailable:
        raise CoalitionError("degradation requires at least one unavailable member")
    if any(member_id not in set(charter.active_member_ids) for member_id in unavailable):
        raise CoalitionError("cannot degrade unknown/inactive member")

    remaining = tuple(
        member_id
        for member_id in charter.active_member_ids
        if member_id not in unavailable
    )
    if len(remaining) < 2:
        raise CoalitionError(
            "degradation would leave fewer than two members; dissolve instead"
        )

    timestamp = time.time() if degraded_at is None else float(degraded_at)
    if timestamp >= charter.expires_at:
        raise CoalitionError("cannot degrade expired coalition")

    updated = replace(
        charter,
        state=DEGRADED,
        active_member_ids=remaining,
        degradation_reason=reason,
    )
    receipt = CoalitionDegradationReceipt(
        receipt_id=str(uuid.uuid4()),
        coalition_id=charter.coalition_id,
        prior_member_ids=charter.active_member_ids,
        active_member_ids=remaining,
        reason=reason,
        degraded_at=timestamp,
    )
    return updated, receipt


def record_coalition_outcome(
    charter: CoalitionCharter,
    transition: TransitionEvaluation,
    *,
    success: bool,
    source_ref: str,
    recorded_at: float | None = None,
) -> CoalitionOutcomeReceipt:
    timestamp = time.time() if recorded_at is None else float(recorded_at)

    if charter.state not in {ACTIVE, DEGRADED}:
        raise CoalitionError("outcomes may be recorded only for active/degraded coalition")
    if transition.proposal.actor_id != charter.coalition_id:
        raise CoalitionError("transition was not performed by coalition identity")
    if not source_ref.strip():
        raise CoalitionError("source_ref must be non-empty")

    return CoalitionOutcomeReceipt(
        receipt_id=str(uuid.uuid4()),
        coalition_id=charter.coalition_id,
        transition_verdict_id=transition.verdict.verdict_id,
        success=bool(success),
        source_ref=source_ref,
        recorded_at=timestamp,
        credit_subject_id=charter.coalition_id,
    )


def disband_coalition(
    charter: CoalitionCharter,
    warrant: Warrant,
    *,
    reason: str,
    dissolved_at: float | None = None,
) -> tuple[CoalitionCharter, Warrant, CoalitionDisbandReceipt]:
    if charter.state == DISSOLVED:
        raise CoalitionError("coalition is already dissolved")
    if not reason.strip():
        raise CoalitionError("disband reason must be non-empty")
    if warrant.bearer != charter.coalition_id:
        raise CoalitionError("warrant does not belong to coalition identity")

    timestamp = time.time() if dissolved_at is None else float(dissolved_at)
    revoked = warrant if warrant.revoked else warrant.revoke(
        reason=f"coalition disbanded: {reason}"
    )
    updated = replace(
        charter,
        state=DISSOLVED,
        active_member_ids=(),
        dissolved_at=timestamp,
        degradation_reason=charter.degradation_reason,
    )
    receipt = CoalitionDisbandReceipt(
        receipt_id=str(uuid.uuid4()),
        coalition_id=charter.coalition_id,
        warrant_id=revoked.warrant_id,
        dissolved_at=timestamp,
        warrant_revoked=True,
        temporary_grants_remaining=0,
    )
    return updated, revoked, receipt


def assert_member_does_not_inherit_warrant(
    member_id: str,
    coalition_warrant: Warrant,
) -> None:
    """Explicit invariant helper for integration tests and callers."""
    if member_id == coalition_warrant.bearer:
        raise CoalitionError("coalition warrant bearer must be coalition identity")
    if member_id in set(coalition_warrant.metadata.get("member_ids", [])):
        return
    raise CoalitionError("member_id is not part of coalition warrant metadata")
