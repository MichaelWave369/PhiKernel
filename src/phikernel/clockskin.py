from __future__ import annotations

"""PhiKernel clock skins / differential computational time.

Clock skins let different workloads experience different local computational
time while remaining under the same warrants and constitutional rules.

Two timing modes are supported in this first pass:
- WALL_RATE: local ticks accrue from elapsed wall time at a declared rate.
- EXPLICIT_SIGNAL: no ticks accrue from waiting; progress requires an explicit
  signal reference. This is suitable for human-governance time, where silence
  must never be interpreted as consent.

Cross-skin effects do not share mutable state directly. They require a carriage
with current citation rights.
"""

from dataclasses import dataclass, field, replace
from typing import Any
import time
import uuid

from phikernel.carriage import CitationDecision
from phikernel.warrant import (
    Warrant,
    WarrantBudgetExceededError,
    WarrantDeniedError,
    WarrantSpendReceipt,
)


CLOCK_SKIN_VERSION = "0.2.0"

WALL_RATE = "WALL_RATE"
EXPLICIT_SIGNAL = "EXPLICIT_SIGNAL"
VALID_CLOCK_MODES = {WALL_RATE, EXPLICIT_SIGNAL}


class ClockSkinError(Exception):
    """Base exception for clock-skin failures."""


class ClockBudgetExceededError(ClockSkinError):
    """Raised when local computational time would exceed its granted budget."""


@dataclass(frozen=True)
class ClockSkinContract:
    skin_id: str
    mode: str
    tick_rate_hz: float | None = None
    interruptible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = CLOCK_SKIN_VERSION

    def __post_init__(self) -> None:
        if not self.skin_id.strip():
            raise ClockSkinError("skin_id must be non-empty")
        if self.mode not in VALID_CLOCK_MODES:
            raise ClockSkinError(
                f"mode must be one of {sorted(VALID_CLOCK_MODES)}"
            )
        if self.mode == WALL_RATE:
            if self.tick_rate_hz is None or self.tick_rate_hz <= 0:
                raise ClockSkinError(
                    "WALL_RATE clock skin requires tick_rate_hz > 0"
                )
        elif self.tick_rate_hz is not None:
            raise ClockSkinError(
                "EXPLICIT_SIGNAL clock skin may not define tick_rate_hz"
            )


@dataclass(frozen=True)
class ClockLease:
    lease_id: str
    subject_id: str
    skin_id: str
    granted_ticks: float
    spent_ticks: float
    opened_at: float
    last_wall_time: float
    closed: bool = False
    close_reason: str | None = None
    authority_change: str = "NONE"
    version: str = CLOCK_SKIN_VERSION

    def __post_init__(self) -> None:
        if not self.lease_id.strip():
            raise ClockSkinError("lease_id must be non-empty")
        if not self.subject_id.strip():
            raise ClockSkinError("subject_id must be non-empty")
        if not self.skin_id.strip():
            raise ClockSkinError("skin_id must be non-empty")
        if self.granted_ticks < 0:
            raise ClockSkinError("granted_ticks must be >= 0")
        if self.spent_ticks < 0 or self.spent_ticks > self.granted_ticks:
            raise ClockSkinError(
                "spent_ticks must be inside [0, granted_ticks]"
            )
        if self.last_wall_time < self.opened_at:
            raise ClockSkinError("last_wall_time may not precede opened_at")
        if self.authority_change != "NONE":
            raise ClockSkinError("clock leases may not change authority")

    @property
    def remaining_ticks(self) -> float:
        return max(0.0, self.granted_ticks - self.spent_ticks)

    @property
    def exhausted(self) -> bool:
        return self.remaining_ticks <= 0.0


@dataclass(frozen=True)
class ClockAdvanceReceipt:
    receipt_id: str
    lease_id: str
    skin_id: str
    subject_id: str
    mode: str
    ticks_advanced: float
    before_remaining_ticks: float
    after_remaining_ticks: float
    wall_from: float
    wall_to: float
    explicit_signal_ref: str | None
    evaluated_at: float
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = CLOCK_SKIN_VERSION

    def __post_init__(self) -> None:
        if self.ticks_advanced < 0:
            raise ClockSkinError("ticks_advanced must be >= 0")
        for field_name, value in (
            ("authority_change", self.authority_change),
            ("warrant_change", self.warrant_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise ClockSkinError(f"{field_name} must remain NONE")


@dataclass(frozen=True)
class ClockWarrantSpend:
    clock_receipt: ClockAdvanceReceipt
    warrant_receipt: WarrantSpendReceipt
    lease_after: ClockLease
    warrant_after: Warrant

    def __post_init__(self) -> None:
        if self.clock_receipt.authority_change != "NONE":
            raise ClockSkinError("clock advancement may not change authority")
        if self.warrant_receipt.authority_change != "NONE":
            raise ClockSkinError("warrant spend may not change authority")


@dataclass(frozen=True)
class CrossSkinHandoffReceipt:
    handoff_id: str
    source_skin_id: str
    target_skin_id: str
    carriage_id: str
    allowed: bool
    reason: str
    citation_evaluated_at: float
    handed_off_at: float
    authority_change: str = "NONE"
    citation_law_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = CLOCK_SKIN_VERSION

    def __post_init__(self) -> None:
        if self.source_skin_id == self.target_skin_id:
            raise ClockSkinError("cross-skin handoff requires distinct skins")
        for field_name, value in (
            ("authority_change", self.authority_change),
            ("citation_law_change", self.citation_law_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise ClockSkinError(f"{field_name} must remain NONE")


def issue_clock_lease(
    contract: ClockSkinContract,
    *,
    subject_id: str,
    granted_ticks: float,
    opened_at: float | None = None,
) -> ClockLease:
    if not subject_id.strip():
        raise ClockSkinError("subject_id must be non-empty")
    if granted_ticks < 0:
        raise ClockSkinError("granted_ticks must be >= 0")
    timestamp = time.time() if opened_at is None else float(opened_at)
    return ClockLease(
        lease_id=str(uuid.uuid4()),
        subject_id=subject_id,
        skin_id=contract.skin_id,
        granted_ticks=float(granted_ticks),
        spent_ticks=0.0,
        opened_at=timestamp,
        last_wall_time=timestamp,
    )


def advance_wall_time(
    contract: ClockSkinContract,
    lease: ClockLease,
    *,
    wall_now: float,
) -> tuple[ClockLease, ClockAdvanceReceipt]:
    """Advance a WALL_RATE lease from elapsed wall time."""
    _validate_contract_lease(contract, lease)
    if contract.mode != WALL_RATE:
        raise ClockSkinError(
            "EXPLICIT_SIGNAL skins do not advance from wall-clock waiting"
        )
    if lease.closed:
        raise ClockSkinError("clock lease is closed")
    if wall_now < lease.last_wall_time:
        raise ClockSkinError("wall_now may not move backwards")

    elapsed = float(wall_now) - lease.last_wall_time
    ticks = elapsed * float(contract.tick_rate_hz)
    return _advance(
        contract,
        lease,
        ticks=ticks,
        wall_to=float(wall_now),
        explicit_signal_ref=None,
    )


def advance_explicit(
    contract: ClockSkinContract,
    lease: ClockLease,
    *,
    ticks: float,
    signal_ref: str,
    at: float | None = None,
) -> tuple[ClockLease, ClockAdvanceReceipt]:
    """Advance an EXPLICIT_SIGNAL skin only when a signal is supplied."""
    _validate_contract_lease(contract, lease)
    if contract.mode != EXPLICIT_SIGNAL:
        raise ClockSkinError(
            "advance_explicit is reserved for EXPLICIT_SIGNAL skins"
        )
    if lease.closed:
        raise ClockSkinError("clock lease is closed")
    if not signal_ref.strip():
        raise ClockSkinError(
            "explicit clock advancement requires non-empty signal_ref"
        )
    timestamp = time.time() if at is None else float(at)
    if timestamp < lease.last_wall_time:
        raise ClockSkinError("explicit advancement time may not move backwards")

    return _advance(
        contract,
        lease,
        ticks=float(ticks),
        wall_to=timestamp,
        explicit_signal_ref=signal_ref,
    )


def spend_ticks_with_warrant(
    contract: ClockSkinContract,
    lease: ClockLease,
    warrant: Warrant,
    *,
    actor_id: str,
    ticks: float,
    resource_kind: str,
    cost_per_tick: float,
    signal_ref: str | None = None,
    at: float | None = None,
) -> ClockWarrantSpend:
    """Atomically advance local ticks and debit an existing warrant resource.

    This does not extend or otherwise alter warrant scope/lifetime. Both clock
    and warrant budgets are preflighted before either immutable copy advances.
    """

    _validate_contract_lease(contract, lease)
    if lease.closed:
        raise ClockSkinError("clock lease is closed")
    if ticks < 0:
        raise ClockSkinError("ticks must be >= 0")
    if cost_per_tick < 0:
        raise ClockSkinError("cost_per_tick must be >= 0")

    timestamp = time.time() if at is None else float(at)
    if timestamp < lease.last_wall_time:
        raise ClockSkinError("advancement time may not move backwards")
    if ticks > lease.remaining_ticks:
        raise ClockBudgetExceededError(
            f"clock budget exhausted: requested={ticks}, remaining={lease.remaining_ticks}"
        )

    if contract.mode == EXPLICIT_SIGNAL:
        if signal_ref is None or not signal_ref.strip():
            raise ClockSkinError(
                "EXPLICIT_SIGNAL warrant spend requires signal_ref"
            )
    elif signal_ref is not None:
        raise ClockSkinError(
            "WALL_RATE warrant spend may not supply explicit signal_ref"
        )

    resource_cost = ticks * cost_per_tick
    existing_budget = warrant.budget(resource_kind)
    if existing_budget is None:
        raise WarrantDeniedError(
            f"resource '{resource_kind}' is not granted by this warrant"
        )
    if resource_cost > existing_budget.remaining:
        raise WarrantBudgetExceededError(
            f"budget '{resource_kind}' exhausted: "
            f"requested={resource_cost}, remaining={existing_budget.remaining}"
        )

    # Preflight lifetime/bearer via zero-cost spend semantics without mutation.
    if actor_id != warrant.bearer:
        raise WarrantDeniedError(
            "bearer mismatch; warrants are non-transferable"
        )
    if warrant.revoked:
        raise WarrantDeniedError(
            f"warrant revoked: {warrant.revocation_reason or 'unspecified'}"
        )
    if warrant.is_expired(now=timestamp):
        raise WarrantDeniedError("warrant expired")

    lease_after, clock_receipt = _advance(
        contract,
        lease,
        ticks=ticks,
        wall_to=timestamp,
        explicit_signal_ref=signal_ref,
    )
    warrant_after, warrant_receipt = warrant.spend(
        actor_id=actor_id,
        resource_kind=resource_kind,
        amount=resource_cost,
        now=timestamp,
    )
    return ClockWarrantSpend(
        clock_receipt=clock_receipt,
        warrant_receipt=warrant_receipt,
        lease_after=lease_after,
        warrant_after=warrant_after,
    )


def close_clock_lease(
    lease: ClockLease,
    *,
    reason: str,
) -> ClockLease:
    if not reason.strip():
        raise ClockSkinError("close reason must be non-empty")
    if lease.closed:
        raise ClockSkinError("clock lease is already closed")
    return replace(lease, closed=True, close_reason=reason)


def cross_skin_handoff(
    *,
    source_contract: ClockSkinContract,
    target_contract: ClockSkinContract,
    carriage_id: str,
    citation: CitationDecision,
    handed_off_at: float | None = None,
) -> CrossSkinHandoffReceipt:
    """Require current carriage citation rights for cross-skin effects."""
    if source_contract.skin_id == target_contract.skin_id:
        raise ClockSkinError("cross-skin handoff requires distinct skins")
    if not carriage_id.strip():
        raise ClockSkinError("carriage_id must be non-empty")

    timestamp = time.time() if handed_off_at is None else float(handed_off_at)

    if citation.carriage_id != carriage_id:
        return CrossSkinHandoffReceipt(
            handoff_id=str(uuid.uuid4()),
            source_skin_id=source_contract.skin_id,
            target_skin_id=target_contract.skin_id,
            carriage_id=carriage_id,
            allowed=False,
            reason="citation decision belongs to a different carriage",
            citation_evaluated_at=citation.evaluated_at,
            handed_off_at=timestamp,
        )

    if not citation.allowed:
        return CrossSkinHandoffReceipt(
            handoff_id=str(uuid.uuid4()),
            source_skin_id=source_contract.skin_id,
            target_skin_id=target_contract.skin_id,
            carriage_id=carriage_id,
            allowed=False,
            reason=f"cross-skin effect blocked: {citation.reason}",
            citation_evaluated_at=citation.evaluated_at,
            handed_off_at=timestamp,
        )

    return CrossSkinHandoffReceipt(
        handoff_id=str(uuid.uuid4()),
        source_skin_id=source_contract.skin_id,
        target_skin_id=target_contract.skin_id,
        carriage_id=carriage_id,
        allowed=True,
        reason="cross-skin effect permitted through citeable carriage",
        citation_evaluated_at=citation.evaluated_at,
        handed_off_at=timestamp,
    )


def _validate_contract_lease(
    contract: ClockSkinContract,
    lease: ClockLease,
) -> None:
    if contract.skin_id != lease.skin_id:
        raise ClockSkinError("clock lease belongs to a different skin")


def _advance(
    contract: ClockSkinContract,
    lease: ClockLease,
    *,
    ticks: float,
    wall_to: float,
    explicit_signal_ref: str | None,
) -> tuple[ClockLease, ClockAdvanceReceipt]:
    if ticks < 0:
        raise ClockSkinError("ticks must be >= 0")
    if ticks > lease.remaining_ticks:
        raise ClockBudgetExceededError(
            f"clock budget exhausted: requested={ticks}, remaining={lease.remaining_ticks}"
        )

    updated = replace(
        lease,
        spent_ticks=lease.spent_ticks + ticks,
        last_wall_time=wall_to,
    )
    receipt = ClockAdvanceReceipt(
        receipt_id=str(uuid.uuid4()),
        lease_id=lease.lease_id,
        skin_id=lease.skin_id,
        subject_id=lease.subject_id,
        mode=contract.mode,
        ticks_advanced=ticks,
        before_remaining_ticks=lease.remaining_ticks,
        after_remaining_ticks=updated.remaining_ticks,
        wall_from=lease.last_wall_time,
        wall_to=wall_to,
        explicit_signal_ref=explicit_signal_ref,
        evaluated_at=wall_to,
    )
    return updated, receipt
