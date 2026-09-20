from __future__ import annotations

"""Crash recovery and reconciliation for bounded constitutional actions.

Recovery is intentionally non-executing. It never replays a bounded action.

Safe cases:
- pending action + journaled OUTCOME -> apply already-recorded outcome
- completed state + missing COMMITTED -> repair journal marker only
- pending action + no OUTCOME -> outcome is unknown; rollback/stop/collapse
- expired active bounded lease -> stop/collapse
- orphaned LICENSED transaction after privilege already collapsed -> close audit

All recovery paths preserve or reduce authority. None expand it.
"""

from dataclasses import dataclass, replace
from typing import Any
import hashlib
import json
import time
import uuid

from phikernel.constitutional_action import (
    COMMITTED_EVENT,
    LICENSED_EVENT,
    OUTCOME_EVENT,
    RECOVERY_EVENT,
    ActionJournalEvent,
    ConstitutionalActionJournal,
)
from phikernel.constitutional_store import (
    ConstitutionalStateStore,
    PersistedConstitutionalState,
)
from phikernel.control_witness import (
    ControlOutcomeReceipt,
    ControlSession,
    collapse_control_to_shadow,
    record_control_outcome,
    terminate_control_session,
)
from phikernel.witness_bench import BOUNDED_CONTROL, SHADOW


ACTION_RECOVERY_VERSION = "0.2.0"

CLEAN = "CLEAN"
PENDING_UNKNOWN = "PENDING_UNKNOWN"
PENDING_OUTCOME_READY = "PENDING_OUTCOME_READY"
COMMIT_MARKER_MISSING = "COMMIT_MARKER_MISSING"
ORPHANED_LICENSE = "ORPHANED_LICENSE"
EXPIRED_ACTIVE = "EXPIRED_ACTIVE"

VALID_RECOVERY_STATUSES = {
    CLEAN,
    PENDING_UNKNOWN,
    PENDING_OUTCOME_READY,
    COMMIT_MARKER_MISSING,
    ORPHANED_LICENSE,
    EXPIRED_ACTIVE,
}


class ConstitutionalRecoveryError(Exception):
    """Raised when recovery evidence is ambiguous or inconsistent."""


@dataclass(frozen=True)
class ActionRecoveryInspection:
    status: str
    mode: str
    revision: int
    action_id: str | None
    transaction_id: str | None
    license_event_hash: str | None
    outcome_event_hash: str | None
    committed_event_hash: str | None
    reason: str
    can_reconcile_without_execution: bool
    authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = ACTION_RECOVERY_VERSION

    def __post_init__(self) -> None:
        if self.status not in VALID_RECOVERY_STATUSES:
            raise ConstitutionalRecoveryError("invalid recovery status")
        if self.authority_change != "NONE":
            raise ConstitutionalRecoveryError(
                "inspection may not change authority"
            )
        if self.constitutional_change != "NONE":
            raise ConstitutionalRecoveryError(
                "inspection may not change constitution"
            )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "mode": self.mode,
            "revision": self.revision,
            "action_id": self.action_id,
            "transaction_id": self.transaction_id,
            "license_event_hash": self.license_event_hash,
            "outcome_event_hash": self.outcome_event_hash,
            "committed_event_hash": self.committed_event_hash,
            "reason": self.reason,
            "can_reconcile_without_execution": (
                self.can_reconcile_without_execution
            ),
            "authority_change": self.authority_change,
            "constitutional_change": self.constitutional_change,
        }


@dataclass(frozen=True)
class ActionRecoveryResult:
    inspection_before: ActionRecoveryInspection
    recovery_kind: str
    resulting_mode: str
    resulting_revision: int
    state_changed: bool
    executor_called: bool
    final_snapshot_hash: str | None
    recovery_event_hash: str | None
    committed_event_hash: str | None
    generated_outcome_receipt_id: str | None
    version: str = ACTION_RECOVERY_VERSION

    def __post_init__(self) -> None:
        if self.executor_called:
            raise ConstitutionalRecoveryError(
                "recovery may never call executor"
            )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "inspection_before": self.inspection_before.to_record(),
            "recovery_kind": self.recovery_kind,
            "resulting_mode": self.resulting_mode,
            "resulting_revision": self.resulting_revision,
            "state_changed": self.state_changed,
            "executor_called": self.executor_called,
            "final_snapshot_hash": self.final_snapshot_hash,
            "recovery_event_hash": self.recovery_event_hash,
            "committed_event_hash": self.committed_event_hash,
            "generated_outcome_receipt_id": self.generated_outcome_receipt_id,
        }


def inspect_action_recovery(
    state_store: ConstitutionalStateStore,
    *,
    action_journal: ConstitutionalActionJournal | None = None,
    now: float | None = None,
) -> ActionRecoveryInspection:
    """Inspect current state/journal without mutating either."""

    timestamp = time.time() if now is None else float(now)
    if not state_store.exists():
        return ActionRecoveryInspection(
            status=CLEAN,
            mode=SHADOW,
            revision=0,
            action_id=None,
            transaction_id=None,
            license_event_hash=None,
            outcome_event_hash=None,
            committed_event_hash=None,
            reason="no persisted constitutional state; genesis SHADOW is clean",
            can_reconcile_without_execution=False,
        )

    snapshot = state_store.load_snapshot_for_recovery()
    state = snapshot.state
    journal = action_journal or ConstitutionalActionJournal(
        state_store.runtime_root
    )
    events = journal.history()
    transactions = _transaction_index(events)

    if (
        state.promotion_state.mode == BOUNDED_CONTROL
        and state.control_session is not None
        and state.control_session.pending_action_id is not None
    ):
        action_id = state.control_session.pending_action_id
        matches = _licensed_transactions_for_action(
            transactions,
            action_id,
        )
        if len(matches) > 1:
            raise ConstitutionalRecoveryError(
                "pending action maps to multiple LICENSED transactions"
            )
        if not matches:
            return ActionRecoveryInspection(
                status=PENDING_UNKNOWN,
                mode=state.promotion_state.mode,
                revision=state.promotion_state.revision,
                action_id=action_id,
                transaction_id=None,
                license_event_hash=None,
                outcome_event_hash=None,
                committed_event_hash=None,
                reason=(
                    "pending action exists but LICENSED journal event is absent; "
                    "execution outcome is unknown and must not be replayed"
                ),
                can_reconcile_without_execution=True,
            )

        transaction_id, tx_events = matches[0]
        license_event = _one_event(
            tx_events,
            LICENSED_EVENT,
            required=True,
        )
        _validate_pending_license(
            state.control_session,
            license_event,
        )
        committed = _one_event(
            tx_events,
            COMMITTED_EVENT,
            required=False,
        )
        if committed is not None:
            raise ConstitutionalRecoveryError(
                "pending state conflicts with COMMITTED transaction marker"
            )
        outcome = _one_event(
            tx_events,
            OUTCOME_EVENT,
            required=False,
        )
        if outcome is None:
            return ActionRecoveryInspection(
                status=PENDING_UNKNOWN,
                mode=state.promotion_state.mode,
                revision=state.promotion_state.revision,
                action_id=action_id,
                transaction_id=transaction_id,
                license_event_hash=license_event.event_hash,
                outcome_event_hash=None,
                committed_event_hash=None,
                reason=(
                    "action was licensed and spent, but no OUTCOME receipt "
                    "survived; recovery must not replay execution"
                ),
                can_reconcile_without_execution=True,
            )

        receipt = _validated_outcome_for_pending(
            state.control_session,
            outcome,
        )
        return ActionRecoveryInspection(
            status=PENDING_OUTCOME_READY,
            mode=state.promotion_state.mode,
            revision=state.promotion_state.revision,
            action_id=action_id,
            transaction_id=transaction_id,
            license_event_hash=license_event.event_hash,
            outcome_event_hash=outcome.event_hash,
            committed_event_hash=None,
            reason=(
                "journaled OUTCOME matches pending action and can be applied "
                "without re-execution"
            ),
            can_reconcile_without_execution=True,
        )

    open_transactions = _open_licensed_transactions(transactions)
    if len(open_transactions) > 1:
        raise ConstitutionalRecoveryError(
            "multiple incomplete licensed transactions require manual audit"
        )
    if open_transactions:
        transaction_id, tx_events = open_transactions[0]
        license_event = _one_event(
            tx_events,
            LICENSED_EVENT,
            required=True,
        )
        action_id = _action_id_from_license(license_event)
        outcome = _one_event(
            tx_events,
            OUTCOME_EVENT,
            required=False,
        )
        if outcome is not None:
            receipt = _outcome_receipt_from_event(outcome)
            _validate_completed_state_for_outcome(
                state,
                receipt,
            )
            return ActionRecoveryInspection(
                status=COMMIT_MARKER_MISSING,
                mode=state.promotion_state.mode,
                revision=state.promotion_state.revision,
                action_id=action_id,
                transaction_id=transaction_id,
                license_event_hash=license_event.event_hash,
                outcome_event_hash=outcome.event_hash,
                committed_event_hash=None,
                reason=(
                    "outcome and resulting state are present but COMMITTED "
                    "journal marker is missing"
                ),
                can_reconcile_without_execution=True,
            )

        if state.promotion_state.mode == SHADOW:
            return ActionRecoveryInspection(
                status=ORPHANED_LICENSE,
                mode=SHADOW,
                revision=state.promotion_state.revision,
                action_id=action_id,
                transaction_id=transaction_id,
                license_event_hash=license_event.event_hash,
                outcome_event_hash=None,
                committed_event_hash=None,
                reason=(
                    "licensed transaction has no outcome, but privilege is "
                    "already collapsed; audit can be closed without execution"
                ),
                can_reconcile_without_execution=True,
            )

        raise ConstitutionalRecoveryError(
            "licensed transaction is incomplete but active state is not pending"
        )

    if (
        state.promotion_state.mode == BOUNDED_CONTROL
        and state.control_grant is not None
        and timestamp >= state.control_grant.expires_at
    ):
        return ActionRecoveryInspection(
            status=EXPIRED_ACTIVE,
            mode=state.promotion_state.mode,
            revision=state.promotion_state.revision,
            action_id=None,
            transaction_id=None,
            license_event_hash=None,
            outcome_event_hash=None,
            committed_event_hash=None,
            reason="persisted bounded-control lease is expired and must collapse",
            can_reconcile_without_execution=True,
        )

    return ActionRecoveryInspection(
        status=CLEAN,
        mode=state.promotion_state.mode,
        revision=state.promotion_state.revision,
        action_id=None,
        transaction_id=None,
        license_event_hash=None,
        outcome_event_hash=None,
        committed_event_hash=None,
        reason="constitutional action state and journal are consistent",
        can_reconcile_without_execution=False,
    )


def reconcile_action_recovery(
    state_store: ConstitutionalStateStore,
    *,
    action_journal: ConstitutionalActionJournal | None = None,
    now: float | None = None,
) -> ActionRecoveryResult:
    """Reconcile one recoverable crash state without executing the action."""

    timestamp = time.time() if now is None else float(now)
    journal = action_journal or ConstitutionalActionJournal(
        state_store.runtime_root
    )
    inspection = inspect_action_recovery(
        state_store,
        action_journal=journal,
        now=timestamp,
    )

    if inspection.status == CLEAN:
        return ActionRecoveryResult(
            inspection_before=inspection,
            recovery_kind="NOOP_CLEAN",
            resulting_mode=inspection.mode,
            resulting_revision=inspection.revision,
            state_changed=False,
            executor_called=False,
            final_snapshot_hash=(
                None
                if not state_store.exists()
                else state_store.load_snapshot_for_recovery().snapshot_hash
            ),
            recovery_event_hash=None,
            committed_event_hash=None,
            generated_outcome_receipt_id=None,
        )

    snapshot = state_store.load_snapshot_for_recovery()
    state = snapshot.state

    if inspection.status == PENDING_OUTCOME_READY:
        assert state.control_session is not None
        assert state.control_contract is not None
        assert inspection.transaction_id is not None
        tx_events = _transaction_index(
            journal.history()
        )[inspection.transaction_id]
        outcome_event = _one_event(
            tx_events,
            OUTCOME_EVENT,
            required=True,
        )
        receipt = _validated_outcome_for_pending(
            state.control_session,
            outcome_event,
        )
        updated_session = _apply_recorded_outcome(
            state.control_session,
            receipt,
        )

        if receipt.terminal:
            collapsed_state = _collapse_from_terminal_outcome(
                state,
                updated_session,
                receipt,
                at=timestamp,
            )
            final_state = collapsed_state
            kind = "APPLIED_TERMINAL_OUTCOME"
        elif (
            state.control_grant is not None
            and timestamp >= state.control_grant.expires_at
        ):
            final_state = _collapse_expired_session(
                state,
                updated_session,
                source_ref=receipt.receipt_id,
                at=timestamp,
            )
            kind = "APPLIED_OUTCOME_THEN_EXPIRED_COLLAPSE"
        else:
            final_state = replace(
                state,
                control_session=updated_session,
            )
            kind = "APPLIED_JOURNALED_OUTCOME"

        final_snapshot = state_store.save(
            final_state,
            written_at=timestamp,
            now=timestamp,
        )
        recovery_event = journal.append(
            transaction_id=inspection.transaction_id,
            event_type=RECOVERY_EVENT,
            payload={
                "recovery_kind": kind,
                "action_id": receipt.action_id,
                "outcome_receipt_id": receipt.receipt_id,
                "final_snapshot_hash": final_snapshot.snapshot_hash,
                "resulting_mode": final_state.promotion_state.mode,
                "executor_called": False,
            },
            recorded_at=timestamp,
        )
        committed = _append_committed(
            journal,
            transaction_id=inspection.transaction_id,
            action_id=receipt.action_id,
            outcome_receipt_id=receipt.receipt_id,
            final_state=final_state,
            final_snapshot_hash=final_snapshot.snapshot_hash,
            recorded_at=timestamp,
        )
        return ActionRecoveryResult(
            inspection_before=inspection,
            recovery_kind=kind,
            resulting_mode=final_state.promotion_state.mode,
            resulting_revision=final_state.promotion_state.revision,
            state_changed=True,
            executor_called=False,
            final_snapshot_hash=final_snapshot.snapshot_hash,
            recovery_event_hash=recovery_event.event_hash,
            committed_event_hash=committed.event_hash,
            generated_outcome_receipt_id=None,
        )

    if inspection.status == PENDING_UNKNOWN:
        if (
            state.promotion_state.mode != BOUNDED_CONTROL
            or state.control_session is None
            or state.control_contract is None
        ):
            raise ConstitutionalRecoveryError(
                "pending recovery requires active bounded session"
            )

        result_ref = _hash_json(
            {
                "kind": "UNKNOWN_CRASH_OUTCOME",
                "action_id": state.control_session.pending_action_id,
                "session_id": state.control_session.session_id,
                "recovery_time": timestamp,
            }
        )
        stopped_session, generated = record_control_outcome(
            state.control_session,
            state.control_contract,
            action_id=state.control_session.pending_action_id or "",
            success=False,
            result_ref=result_ref,
            rollback_performed=True,
            rollback_ref=state.control_session.pending_rollback_ref,
            recorded_at=timestamp,
        )
        collapsed, collapse_receipt = collapse_control_to_shadow(
            state.promotion_state,
            stopped_session,
            source_ref=generated.receipt_id,
            reason=(
                "crash recovery: execution outcome unknown; no replay; "
                "verified no-op rollback and terminal privilege collapse"
            ),
            collapsed_at=timestamp,
        )
        final_state = PersistedConstitutionalState(
            promotion_state=collapsed,
            collapse_receipt=collapse_receipt,
        )
        final_snapshot = state_store.save(
            final_state,
            written_at=timestamp,
            now=timestamp,
        )
        transaction_id = (
            inspection.transaction_id
            or f"recovery:{uuid.uuid4()}"
        )
        recovery_event = journal.append(
            transaction_id=transaction_id,
            event_type=RECOVERY_EVENT,
            payload={
                "recovery_kind": "UNKNOWN_OUTCOME_NO_REPLAY_COLLAPSE",
                "action_id": inspection.action_id,
                "generated_outcome_receipt": _outcome_to_record(generated),
                "final_snapshot_hash": final_snapshot.snapshot_hash,
                "resulting_mode": SHADOW,
                "executor_called": False,
            },
            recorded_at=timestamp,
        )
        committed = _append_committed(
            journal,
            transaction_id=transaction_id,
            action_id=inspection.action_id,
            outcome_receipt_id=generated.receipt_id,
            final_state=final_state,
            final_snapshot_hash=final_snapshot.snapshot_hash,
            recorded_at=timestamp,
        )
        return ActionRecoveryResult(
            inspection_before=inspection,
            recovery_kind="UNKNOWN_OUTCOME_NO_REPLAY_COLLAPSE",
            resulting_mode=SHADOW,
            resulting_revision=collapsed.revision,
            state_changed=True,
            executor_called=False,
            final_snapshot_hash=final_snapshot.snapshot_hash,
            recovery_event_hash=recovery_event.event_hash,
            committed_event_hash=committed.event_hash,
            generated_outcome_receipt_id=generated.receipt_id,
        )

    if inspection.status == COMMIT_MARKER_MISSING:
        assert inspection.transaction_id is not None
        tx_events = _transaction_index(
            journal.history()
        )[inspection.transaction_id]
        outcome_event = _one_event(
            tx_events,
            OUTCOME_EVENT,
            required=True,
        )
        receipt = _outcome_receipt_from_event(outcome_event)

        final_state = state
        final_snapshot_hash = snapshot.snapshot_hash
        state_changed = False
        kind = "REPAIRED_COMMIT_MARKER"

        if (
            state.promotion_state.mode == BOUNDED_CONTROL
            and state.control_grant is not None
            and state.control_session is not None
            and timestamp >= state.control_grant.expires_at
        ):
            final_state = _collapse_expired_session(
                state,
                state.control_session,
                source_ref=receipt.receipt_id,
                at=timestamp,
            )
            final_snapshot = state_store.save(
                final_state,
                written_at=timestamp,
                now=timestamp,
            )
            final_snapshot_hash = final_snapshot.snapshot_hash
            state_changed = True
            kind = "REPAIRED_COMMIT_THEN_EXPIRED_COLLAPSE"

        recovery_event = journal.append(
            transaction_id=inspection.transaction_id,
            event_type=RECOVERY_EVENT,
            payload={
                "recovery_kind": kind,
                "action_id": receipt.action_id,
                "outcome_receipt_id": receipt.receipt_id,
                "final_snapshot_hash": final_snapshot_hash,
                "resulting_mode": final_state.promotion_state.mode,
                "executor_called": False,
            },
            recorded_at=timestamp,
        )
        committed = _append_committed(
            journal,
            transaction_id=inspection.transaction_id,
            action_id=receipt.action_id,
            outcome_receipt_id=receipt.receipt_id,
            final_state=final_state,
            final_snapshot_hash=final_snapshot_hash,
            recorded_at=timestamp,
        )
        return ActionRecoveryResult(
            inspection_before=inspection,
            recovery_kind=kind,
            resulting_mode=final_state.promotion_state.mode,
            resulting_revision=final_state.promotion_state.revision,
            state_changed=state_changed,
            executor_called=False,
            final_snapshot_hash=final_snapshot_hash,
            recovery_event_hash=recovery_event.event_hash,
            committed_event_hash=committed.event_hash,
            generated_outcome_receipt_id=None,
        )

    if inspection.status == ORPHANED_LICENSE:
        assert inspection.transaction_id is not None
        recovery_event = journal.append(
            transaction_id=inspection.transaction_id,
            event_type=RECOVERY_EVENT,
            payload={
                "recovery_kind": "CLOSED_ORPHANED_LICENSE_AFTER_COLLAPSE",
                "action_id": inspection.action_id,
                "final_snapshot_hash": snapshot.snapshot_hash,
                "resulting_mode": state.promotion_state.mode,
                "executor_called": False,
            },
            recorded_at=timestamp,
        )
        committed = _append_committed(
            journal,
            transaction_id=inspection.transaction_id,
            action_id=inspection.action_id,
            outcome_receipt_id=None,
            final_state=state,
            final_snapshot_hash=snapshot.snapshot_hash,
            recorded_at=timestamp,
        )
        return ActionRecoveryResult(
            inspection_before=inspection,
            recovery_kind="CLOSED_ORPHANED_LICENSE_AFTER_COLLAPSE",
            resulting_mode=state.promotion_state.mode,
            resulting_revision=state.promotion_state.revision,
            state_changed=False,
            executor_called=False,
            final_snapshot_hash=snapshot.snapshot_hash,
            recovery_event_hash=recovery_event.event_hash,
            committed_event_hash=committed.event_hash,
            generated_outcome_receipt_id=None,
        )

    if inspection.status == EXPIRED_ACTIVE:
        if (
            state.promotion_state.mode != BOUNDED_CONTROL
            or state.control_session is None
        ):
            raise ConstitutionalRecoveryError(
                "expired recovery requires bounded session"
            )
        final_state = _collapse_expired_session(
            state,
            state.control_session,
            source_ref="constitutional-recovery:expired",
            at=timestamp,
        )
        final_snapshot = state_store.save(
            final_state,
            written_at=timestamp,
            now=timestamp,
        )
        transaction_id = f"recovery:{uuid.uuid4()}"
        recovery_event = journal.append(
            transaction_id=transaction_id,
            event_type=RECOVERY_EVENT,
            payload={
                "recovery_kind": "EXPIRED_LEASE_COLLAPSE",
                "action_id": None,
                "final_snapshot_hash": final_snapshot.snapshot_hash,
                "resulting_mode": SHADOW,
                "executor_called": False,
            },
            recorded_at=timestamp,
        )
        return ActionRecoveryResult(
            inspection_before=inspection,
            recovery_kind="EXPIRED_LEASE_COLLAPSE",
            resulting_mode=SHADOW,
            resulting_revision=final_state.promotion_state.revision,
            state_changed=True,
            executor_called=False,
            final_snapshot_hash=final_snapshot.snapshot_hash,
            recovery_event_hash=recovery_event.event_hash,
            committed_event_hash=None,
            generated_outcome_receipt_id=None,
        )

    raise ConstitutionalRecoveryError(
        f"unsupported recovery status {inspection.status}"
    )


def _transaction_index(
    events: tuple[ActionJournalEvent, ...],
) -> dict[str, tuple[ActionJournalEvent, ...]]:
    grouped: dict[str, list[ActionJournalEvent]] = {}
    for event in events:
        grouped.setdefault(event.transaction_id, []).append(event)
    return {
        transaction_id: tuple(items)
        for transaction_id, items in grouped.items()
    }


def _licensed_transactions_for_action(
    transactions: dict[str, tuple[ActionJournalEvent, ...]],
    action_id: str,
) -> list[tuple[str, tuple[ActionJournalEvent, ...]]]:
    matches = []
    for transaction_id, events in transactions.items():
        license_event = _one_event(
            events,
            LICENSED_EVENT,
            required=False,
        )
        if (
            license_event is not None
            and _action_id_from_license(license_event) == action_id
        ):
            matches.append((transaction_id, events))
    return matches


def _open_licensed_transactions(
    transactions: dict[str, tuple[ActionJournalEvent, ...]],
) -> list[tuple[str, tuple[ActionJournalEvent, ...]]]:
    open_rows = []
    for transaction_id, events in transactions.items():
        licensed = _one_event(
            events,
            LICENSED_EVENT,
            required=False,
        )
        committed = _one_event(
            events,
            COMMITTED_EVENT,
            required=False,
        )
        if licensed is not None and committed is None:
            open_rows.append((transaction_id, events))
    return open_rows


def _one_event(
    events: tuple[ActionJournalEvent, ...],
    event_type: str,
    *,
    required: bool,
) -> ActionJournalEvent | None:
    matches = [
        event for event in events if event.event_type == event_type
    ]
    if len(matches) > 1:
        raise ConstitutionalRecoveryError(
            f"transaction contains multiple {event_type} events"
        )
    if required and not matches:
        raise ConstitutionalRecoveryError(
            f"transaction is missing {event_type} event"
        )
    return None if not matches else matches[0]


def _action_id_from_license(event: ActionJournalEvent) -> str:
    action = event.payload.get("action")
    if not isinstance(action, dict):
        raise ConstitutionalRecoveryError(
            "LICENSED event is missing action record"
        )
    action_id = action.get("action_id")
    if not isinstance(action_id, str) or not action_id.strip():
        raise ConstitutionalRecoveryError(
            "LICENSED action_id is missing"
        )
    return action_id


def _validate_pending_license(
    session: ControlSession,
    event: ActionJournalEvent,
) -> None:
    action_id = _action_id_from_license(event)
    if action_id != session.pending_action_id:
        raise ConstitutionalRecoveryError(
            "LICENSED action does not match pending session action"
        )
    receipt = event.payload.get("license_receipt")
    if not isinstance(receipt, dict):
        raise ConstitutionalRecoveryError(
            "LICENSED event is missing license receipt"
        )
    if receipt.get("action_id") != action_id:
        raise ConstitutionalRecoveryError(
            "license receipt action id mismatch"
        )
    if receipt.get("session_id") != session.session_id:
        raise ConstitutionalRecoveryError(
            "license receipt session id mismatch"
        )
    if receipt.get("receipt_id") != session.last_receipt_id:
        raise ConstitutionalRecoveryError(
            "pending session last receipt does not match LICENSED receipt"
        )
    if receipt.get("warrant_id") != session.warrant.warrant_id:
        raise ConstitutionalRecoveryError(
            "license receipt warrant id mismatch"
        )
    if receipt.get("allowed") is not True:
        raise ConstitutionalRecoveryError(
            "pending session must reference an allowed license receipt"
        )


def _validated_outcome_for_pending(
    session: ControlSession,
    event: ActionJournalEvent,
) -> ControlOutcomeReceipt:
    receipt = _outcome_receipt_from_event(event)
    if receipt.action_id != session.pending_action_id:
        raise ConstitutionalRecoveryError(
            "OUTCOME action does not match pending action"
        )
    if receipt.session_id != session.session_id:
        raise ConstitutionalRecoveryError(
            "OUTCOME session does not match pending session"
        )

    executor = event.payload.get("executor_result")
    if not isinstance(executor, dict):
        raise ConstitutionalRecoveryError(
            "OUTCOME event is missing executor result"
        )
    if executor.get("result_ref") != receipt.result_ref:
        raise ConstitutionalRecoveryError(
            "executor result_ref does not match OUTCOME receipt"
        )
    if bool(executor.get("success")) != receipt.success:
        raise ConstitutionalRecoveryError(
            "executor success does not match OUTCOME receipt"
        )
    if bool(executor.get("rollback_performed")) != receipt.rollback_performed:
        raise ConstitutionalRecoveryError(
            "executor rollback flag does not match OUTCOME receipt"
        )
    if executor.get("rollback_ref") != receipt.rollback_ref:
        raise ConstitutionalRecoveryError(
            "executor rollback_ref does not match OUTCOME receipt"
        )

    expected_rollback_satisfied = (
        True
        if receipt.success
        else (
            receipt.rollback_performed
            and receipt.rollback_ref is not None
            and receipt.rollback_ref == session.pending_rollback_ref
        )
    )
    if receipt.rollback_satisfied != expected_rollback_satisfied:
        raise ConstitutionalRecoveryError(
            "OUTCOME rollback satisfaction is inconsistent"
        )
    if receipt.success:
        if receipt.terminal:
            raise ConstitutionalRecoveryError(
                "successful recovered outcome may not be terminal"
            )
        if receipt.rollback_performed or receipt.rollback_ref is not None:
            raise ConstitutionalRecoveryError(
                "successful recovered outcome may not claim rollback"
            )
    elif not receipt.terminal:
        raise ConstitutionalRecoveryError(
            "failed v0.2 recovered outcome must be terminal"
        )
    return receipt


def _outcome_receipt_from_event(
    event: ActionJournalEvent,
) -> ControlOutcomeReceipt:
    record = event.payload.get("outcome_receipt")
    if not isinstance(record, dict):
        raise ConstitutionalRecoveryError(
            "OUTCOME event is missing outcome receipt"
        )
    try:
        receipt = ControlOutcomeReceipt(
            receipt_id=str(record["receipt_id"]),
            session_id=str(record["session_id"]),
            action_id=str(record["action_id"]),
            success=bool(record["success"]),
            result_ref=str(record["result_ref"]),
            rollback_required=bool(record["rollback_required"]),
            rollback_performed=bool(record["rollback_performed"]),
            rollback_ref=record.get("rollback_ref"),
            rollback_satisfied=bool(record["rollback_satisfied"]),
            terminal=bool(record["terminal"]),
            reason=str(record["reason"]),
            recorded_at=float(record["recorded_at"]),
            authority_change=str(
                record.get("authority_change", "NONE")
            ),
            steering_authority_change=str(
                record.get("steering_authority_change", "NONE")
            ),
            constitutional_change=str(
                record.get("constitutional_change", "NONE")
            ),
            version=str(record.get("version", ACTION_RECOVERY_VERSION)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConstitutionalRecoveryError(
            "OUTCOME receipt is malformed"
        ) from exc

    if (
        receipt.authority_change != "NONE"
        or receipt.steering_authority_change != "NONE"
        or receipt.constitutional_change != "NONE"
    ):
        raise ConstitutionalRecoveryError(
            "OUTCOME receipt may not expand authority or constitution"
        )
    if len(receipt.result_ref) != 64:
        raise ConstitutionalRecoveryError(
            "OUTCOME result_ref must be SHA-256 hex"
        )
    try:
        int(receipt.result_ref, 16)
    except ValueError as exc:
        raise ConstitutionalRecoveryError(
            "OUTCOME result_ref must be hexadecimal"
        ) from exc
    return receipt


def _apply_recorded_outcome(
    session: ControlSession,
    receipt: ControlOutcomeReceipt,
) -> ControlSession:
    if receipt.success:
        return replace(
            session,
            pending_action_id=None,
            pending_rollback_ref=None,
            stopped=False,
            stop_reason=None,
            last_receipt_id=receipt.receipt_id,
        )

    revoked = (
        session.warrant
        if session.warrant.revoked
        else session.warrant.revoke(reason=receipt.reason)
    )
    return replace(
        session,
        warrant=revoked,
        pending_action_id=None,
        pending_rollback_ref=None,
        stopped=True,
        stop_reason=receipt.reason,
        last_receipt_id=receipt.receipt_id,
    )


def _validate_completed_state_for_outcome(
    state: PersistedConstitutionalState,
    receipt: ControlOutcomeReceipt,
) -> None:
    if state.promotion_state.mode == BOUNDED_CONTROL:
        session = state.control_session
        if session is None:
            raise ConstitutionalRecoveryError(
                "bounded completed state is missing control session"
            )
        if session.pending_action_id is not None:
            raise ConstitutionalRecoveryError(
                "completed outcome conflicts with pending action"
            )
        if session.session_id != receipt.session_id:
            raise ConstitutionalRecoveryError(
                "completed state session does not match OUTCOME"
            )
        if session.last_receipt_id != receipt.receipt_id:
            raise ConstitutionalRecoveryError(
                "completed state last receipt does not match OUTCOME"
            )
        if not receipt.success or receipt.terminal:
            raise ConstitutionalRecoveryError(
                "active bounded state requires successful non-terminal OUTCOME"
            )
        return

    if state.promotion_state.mode == SHADOW:
        collapse = state.collapse_receipt
        if collapse is None:
            raise ConstitutionalRecoveryError(
                "SHADOW recovery state lacks collapse receipt"
            )
        if collapse.source_ref != receipt.receipt_id:
            raise ConstitutionalRecoveryError(
                "SHADOW collapse does not reference OUTCOME receipt"
            )
        if not receipt.terminal:
            raise ConstitutionalRecoveryError(
                "SHADOW outcome recovery requires terminal OUTCOME"
            )
        return

    raise ConstitutionalRecoveryError(
        "OUTCOME without COMMITTED marker does not match current mode"
    )


def _collapse_from_terminal_outcome(
    state: PersistedConstitutionalState,
    session: ControlSession,
    receipt: ControlOutcomeReceipt,
    *,
    at: float,
) -> PersistedConstitutionalState:
    if not session.stopped:
        raise ConstitutionalRecoveryError(
            "terminal outcome must produce stopped session"
        )
    collapsed, collapse_receipt = collapse_control_to_shadow(
        state.promotion_state,
        session,
        source_ref=receipt.receipt_id,
        reason=receipt.reason,
        collapsed_at=at,
    )
    return PersistedConstitutionalState(
        promotion_state=collapsed,
        collapse_receipt=collapse_receipt,
    )


def _collapse_expired_session(
    state: PersistedConstitutionalState,
    session: ControlSession,
    *,
    source_ref: str,
    at: float,
) -> PersistedConstitutionalState:
    stopped = session
    if not stopped.stopped:
        stopped, stop_receipt = terminate_control_session(
            stopped,
            source_ref=source_ref,
            reason="bounded-control lease expired during recovery",
            stopped_at=at,
        )
        collapse_source = stop_receipt.receipt_id
    else:
        collapse_source = source_ref

    collapsed, collapse_receipt = collapse_control_to_shadow(
        state.promotion_state,
        stopped,
        source_ref=collapse_source,
        reason="bounded-control lease expired during recovery",
        collapsed_at=at,
    )
    return PersistedConstitutionalState(
        promotion_state=collapsed,
        collapse_receipt=collapse_receipt,
    )


def _append_committed(
    journal: ConstitutionalActionJournal,
    *,
    transaction_id: str,
    action_id: str | None,
    outcome_receipt_id: str | None,
    final_state: PersistedConstitutionalState,
    final_snapshot_hash: str,
    recorded_at: float,
) -> ActionJournalEvent:
    return journal.append(
        transaction_id=transaction_id,
        event_type=COMMITTED_EVENT,
        payload={
            "action_id": action_id,
            "outcome_receipt_id": outcome_receipt_id,
            "final_snapshot_hash": final_snapshot_hash,
            "resulting_mode": final_state.promotion_state.mode,
            "resulting_session_id": (
                None
                if final_state.control_session is None
                else final_state.control_session.session_id
            ),
            "recovered": True,
        },
        recorded_at=recorded_at,
    )


def _outcome_to_record(
    receipt: ControlOutcomeReceipt,
) -> dict[str, Any]:
    return {
        "version": receipt.version,
        "receipt_id": receipt.receipt_id,
        "session_id": receipt.session_id,
        "action_id": receipt.action_id,
        "success": receipt.success,
        "result_ref": receipt.result_ref,
        "rollback_required": receipt.rollback_required,
        "rollback_performed": receipt.rollback_performed,
        "rollback_ref": receipt.rollback_ref,
        "rollback_satisfied": receipt.rollback_satisfied,
        "terminal": receipt.terminal,
        "reason": receipt.reason,
        "recorded_at": receipt.recorded_at,
        "authority_change": receipt.authority_change,
        "steering_authority_change": receipt.steering_authority_change,
        "constitutional_change": receipt.constitutional_change,
    }


def _hash_json(value: Any) -> str:
    try:
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConstitutionalRecoveryError(
            "recovery evidence must be deterministic JSON"
        ) from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
