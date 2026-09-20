from __future__ import annotations

"""Restart-safe bounded constitutional actions for PhiKernel v0.2.

This module is the first execution boundary that consumes a persisted
BOUNDED_CONTROL lease.

The safe v0.2 executor profile is intentionally narrow:
- operation must be "execute"
- target must be one of the built-in normalized runtime adapters
- no subprocess execution
- no arbitrary Python execution
- no shell command execution

Transaction ordering is fail-conservative:

    license
      -> persist pending/spent session
      -> append LICENSED journal event
      -> execute bounded runtime adapter
      -> append OUTCOME journal event
      -> persist completed session or collapsed SHADOW

If a crash occurs after licensing but before the completed-state persistence,
the stored control session remains pending and cannot silently reuse the spent
authority.

The action journal is hash-chained for integrity/replay evidence. Like the
constitutional state hash chain, it is not a human-authentication signature.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import time
import uuid

from phikernel.constitutional_runtime import (
    BOUNDED_LICENSED,
    ConstitutionalRuntimeResult,
    orchestrate_runtime,
)
from phikernel.constitutional_store import (
    ConstitutionalSnapshot,
    ConstitutionalStateStore,
    PersistedConstitutionalState,
)
from phikernel.control_state import RuntimeControlState
from phikernel.control_witness import (
    ControlActionLicenseReceipt,
    ControlActionRequest,
    ControlOutcomeReceipt,
    collapse_control_to_shadow,
    record_control_outcome,
)
from phikernel.heart import RuntimeBridge
from phikernel.relational_router import (
    RelationalRoutingRequest,
    RouteCandidate,
)
from phikernel.router import CoachRouter
from phikernel.routing_shadow import CoachRouteBinding
from phikernel.transition import ResourceSpend
from phikernel.witness_bench import BOUNDED_CONTROL, SHADOW


CONSTITUTIONAL_ACTION_VERSION = "0.2.0"

LICENSED_EVENT = "LICENSED"
REFUSED_EVENT = "REFUSED"
OUTCOME_EVENT = "OUTCOME"
COMMITTED_EVENT = "COMMITTED"
RECOVERY_EVENT = "RECOVERY"
VALID_EVENT_TYPES = {
    LICENSED_EVENT,
    REFUSED_EVENT,
    OUTCOME_EVENT,
    COMMITTED_EVENT,
    RECOVERY_EVENT,
}

SUPPORTED_EXECUTOR_TARGETS = {
    "runtime/adapter/legacy": "legacy",
    "runtime/adapter/tiekat_v50": "tiekat_v50",
}


class ConstitutionalActionError(Exception):
    """Base exception for bounded constitutional action failures."""


class ConstitutionalActionJournalError(ConstitutionalActionError):
    """Raised when the append-only action journal fails integrity checks."""


@dataclass(frozen=True)
class BoundedExecutorResult:
    success: bool
    target: str
    adapter: str
    result_ref: str
    result: dict[str, Any] | None
    error: str | None
    rollback_performed: bool
    rollback_ref: str | None
    executed_at: float
    version: str = CONSTITUTIONAL_ACTION_VERSION

    def __post_init__(self) -> None:
        if self.target not in SUPPORTED_EXECUTOR_TARGETS:
            raise ConstitutionalActionError("unsupported executor target")
        if self.adapter != SUPPORTED_EXECUTOR_TARGETS[self.target]:
            raise ConstitutionalActionError(
                "executor adapter does not match target"
            )
        if len(self.result_ref) != 64:
            raise ConstitutionalActionError(
                "result_ref must be SHA-256 hex"
            )
        try:
            int(self.result_ref, 16)
        except ValueError as exc:
            raise ConstitutionalActionError(
                "result_ref must be hexadecimal"
            ) from exc

        if self.success:
            if self.error is not None:
                raise ConstitutionalActionError(
                    "successful executor result may not carry error"
                )
            if self.result is None:
                raise ConstitutionalActionError(
                    "successful executor result requires result"
                )
            if self.rollback_performed or self.rollback_ref is not None:
                raise ConstitutionalActionError(
                    "successful executor result may not claim rollback"
                )
        else:
            if not (self.error or "").strip():
                raise ConstitutionalActionError(
                    "failed executor result requires error"
                )
            if not self.rollback_performed:
                raise ConstitutionalActionError(
                    "failed safe-profile executor requires rollback handling"
                )
            if not (self.rollback_ref or "").strip():
                raise ConstitutionalActionError(
                    "failed executor result requires rollback_ref"
                )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "success": self.success,
            "target": self.target,
            "adapter": self.adapter,
            "result_ref": self.result_ref,
            "result": self.result,
            "error": self.error,
            "rollback_performed": self.rollback_performed,
            "rollback_ref": self.rollback_ref,
            "executed_at": self.executed_at,
        }


@dataclass(frozen=True)
class ActionJournalEvent:
    event_id: str
    transaction_id: str
    event_type: str
    recorded_at: float
    payload: dict[str, Any]
    previous_event_hash: str | None
    event_hash: str
    version: str = CONSTITUTIONAL_ACTION_VERSION

    def __post_init__(self) -> None:
        if self.event_type not in VALID_EVENT_TYPES:
            raise ConstitutionalActionJournalError(
                "invalid action journal event type"
            )
        for name, value in (
            ("event_id", self.event_id),
            ("transaction_id", self.transaction_id),
        ):
            if not value.strip():
                raise ConstitutionalActionJournalError(
                    f"{name} must be non-empty"
                )
        _require_hash("event_hash", self.event_hash)
        if self.previous_event_hash is not None:
            _require_hash(
                "previous_event_hash",
                self.previous_event_hash,
            )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "event_id": self.event_id,
            "transaction_id": self.transaction_id,
            "event_type": self.event_type,
            "recorded_at": self.recorded_at,
            "payload": self.payload,
            "previous_event_hash": self.previous_event_hash,
            "event_hash": self.event_hash,
        }


class ConstitutionalActionJournal:
    """Append-only hash-chained bounded-action evidence."""

    def __init__(self, runtime_root: str | Path) -> None:
        self.root = Path(runtime_root) / "constitutional"
        self.path = self.root / "actions.jsonl"

    def append(
        self,
        *,
        transaction_id: str,
        event_type: str,
        payload: dict[str, Any],
        recorded_at: float | None = None,
    ) -> ActionJournalEvent:
        if event_type not in VALID_EVENT_TYPES:
            raise ConstitutionalActionJournalError(
                "invalid action journal event type"
            )
        if not transaction_id.strip():
            raise ConstitutionalActionJournalError(
                "transaction_id must be non-empty"
            )

        history = self.history()
        previous_hash = (
            None if not history else history[-1].event_hash
        )
        timestamp = (
            time.time()
            if recorded_at is None
            else float(recorded_at)
        )
        event_id = str(uuid.uuid4())
        body = {
            "version": CONSTITUTIONAL_ACTION_VERSION,
            "event_id": event_id,
            "transaction_id": transaction_id,
            "event_type": event_type,
            "recorded_at": timestamp,
            "payload": payload,
            "previous_event_hash": previous_hash,
        }
        digest = _hash_json(body)
        event = ActionJournalEvent(
            event_id=event_id,
            transaction_id=transaction_id,
            event_type=event_type,
            recorded_at=timestamp,
            payload=payload,
            previous_event_hash=previous_hash,
            event_hash=digest,
        )

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical_json(event.to_record()) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return event

    def history(self) -> tuple[ActionJournalEvent, ...]:
        if not self.path.exists():
            return ()

        events: list[ActionJournalEvent] = []
        previous_hash: str | None = None
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ConstitutionalActionJournalError(
                        f"action journal line {line_no} is invalid JSON"
                    ) from exc
                if not isinstance(record, dict):
                    raise ConstitutionalActionJournalError(
                        f"action journal line {line_no} is not an object"
                    )

                event = _event_from_record(record)
                if event.previous_event_hash != previous_hash:
                    raise ConstitutionalActionJournalError(
                        f"action journal chain broken at line {line_no}"
                    )
                previous_hash = event.event_hash
                events.append(event)
        return tuple(events)


@dataclass(frozen=True)
class ConstitutionalActionTransactionResult:
    transaction_id: str
    action: ControlActionRequest
    runtime: ConstitutionalRuntimeResult
    license_receipt: ControlActionLicenseReceipt | None
    executor_result: BoundedExecutorResult | None
    outcome_receipt: ControlOutcomeReceipt | None
    pending_snapshot_hash: str | None
    final_snapshot_hash: str | None
    resulting_mode: str
    resulting_session_id: str | None
    journal_event_hashes: tuple[str, ...]
    version: str = CONSTITUTIONAL_ACTION_VERSION

    @property
    def executed(self) -> bool:
        return self.executor_result is not None

    @property
    def success(self) -> bool:
        return (
            self.executor_result is not None
            and self.executor_result.success
            and self.outcome_receipt is not None
            and self.outcome_receipt.success
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "transaction_id": self.transaction_id,
            "action": _action_to_record(self.action),
            "runtime": self.runtime.to_record(),
            "license_receipt": (
                None
                if self.license_receipt is None
                else _license_to_record(self.license_receipt)
            ),
            "executor_result": (
                None
                if self.executor_result is None
                else self.executor_result.to_record()
            ),
            "outcome_receipt": (
                None
                if self.outcome_receipt is None
                else _outcome_to_record(self.outcome_receipt)
            ),
            "pending_snapshot_hash": self.pending_snapshot_hash,
            "final_snapshot_hash": self.final_snapshot_hash,
            "resulting_mode": self.resulting_mode,
            "resulting_session_id": self.resulting_session_id,
            "journal_event_hashes": list(self.journal_event_hashes),
            "executed": self.executed,
            "success": self.success,
        }


def execute_constitutional_action(
    *,
    think_bundle: dict[str, Any],
    state: PersistedConstitutionalState,
    state_store: ConstitutionalStateStore,
    runtime_control_state: RuntimeControlState,
    target: str,
    payload: dict[str, Any],
    resource_spends: tuple[ResourceSpend, ...] | list[ResourceSpend],
    clock_ticks: float,
    evidence_refs: tuple[str, ...] | list[str],
    rollback_ref: str,
    runtime_bridge: RuntimeBridge | None = None,
    legacy_router: CoachRouter | None = None,
    action_journal: ConstitutionalActionJournal | None = None,
    now: float | None = None,
) -> ConstitutionalActionTransactionResult:
    """License, execute, receipt, and persist one bounded runtime-adapter action."""

    timestamp = time.time() if now is None else float(now)
    if state.promotion_state.mode != BOUNDED_CONTROL:
        raise ConstitutionalActionError(
            "constitutional action requires persisted BOUNDED_CONTROL"
        )
    if (
        state.control_contract is None
        or state.control_grant is None
        or state.control_session is None
    ):
        raise ConstitutionalActionError(
            "persisted BOUNDED_CONTROL is missing contract/grant/session"
        )
    if target not in SUPPORTED_EXECUTOR_TARGETS:
        raise ConstitutionalActionError(
            "v0.2 constitutional action supports only normalized runtime adapters"
        )
    if not isinstance(payload, dict):
        raise ConstitutionalActionError(
            "bounded executor payload must be a JSON object"
        )
    if clock_ticks <= 0:
        raise ConstitutionalActionError("clock_ticks must be > 0")
    if not rollback_ref.strip():
        raise ConstitutionalActionError(
            "rollback_ref must be non-empty"
        )

    transaction_id = str(uuid.uuid4())
    action = ControlActionRequest.create(
        actor_id=state.control_session.actor_id,
        operation="execute",
        target=target,
        resource_spends=tuple(resource_spends),
        clock_ticks=float(clock_ticks),
        evidence_refs=tuple(evidence_refs),
        rollback_ref=rollback_ref,
        requested_at=timestamp,
    )

    route_key = f"control-action:{action.action_id}"
    candidate = RouteCandidate(
        route_key=route_key,
        actor_id=action.actor_id,
        operation=action.operation,
        target=action.target,
        warrant=state.control_session.warrant,
        base_cost=0.0,
        capability_tags=("control-action", "runtime-adapter"),
        resource_spends=tuple(action.resource_spends),
        metadata={
            "source": "phik-constitutional-action",
            "transaction_id": transaction_id,
            "action_id": action.action_id,
            "execution_authority": "BOUNDED_CONTROL_ONLY",
        },
    )
    routing_request = RelationalRoutingRequest.create(
        required_capabilities=("control-action",),
        requested_at=timestamp,
        metadata={
            "source": "phik-constitutional-action",
            "transaction_id": transaction_id,
            "action_id": action.action_id,
        },
    )
    bindings = (
        CoachRouteBinding("Titan", "route:titan"),
        CoachRouteBinding("Flow", "route:flow"),
        CoachRouteBinding("Sage", "route:sage"),
    )

    runtime = orchestrate_runtime(
        think_bundle,
        state.promotion_state,
        routing_request,
        (candidate,),
        bindings=bindings,
        runtime_control_state=runtime_control_state,
        legacy_router=legacy_router,
        control_contract=state.control_contract,
        control_grant=state.control_grant,
        control_session=state.control_session,
        control_action=action,
        now=timestamp,
    )

    journal = action_journal or ConstitutionalActionJournal(
        state_store.runtime_root
    )
    event_hashes: list[str] = []

    if runtime.disposition != BOUNDED_LICENSED:
        final_snapshot_hash: str | None = None
        if (
            runtime.mode_after == SHADOW
            and runtime.collapse_receipt is not None
        ):
            collapsed = PersistedConstitutionalState(
                promotion_state=runtime.promotion_state_after,
                collapse_receipt=runtime.collapse_receipt,
            )
            final_snapshot = state_store.save(
                collapsed,
                written_at=runtime.evaluated_at,
                now=runtime.evaluated_at,
            )
            final_snapshot_hash = final_snapshot.snapshot_hash

        refused_event = journal.append(
            transaction_id=transaction_id,
            event_type=REFUSED_EVENT,
            payload={
                "action": _action_to_record(action),
                "runtime": runtime.to_record(),
                "final_snapshot_hash": final_snapshot_hash,
            },
            recorded_at=runtime.evaluated_at,
        )
        event_hashes.append(refused_event.event_hash)

        return ConstitutionalActionTransactionResult(
            transaction_id=transaction_id,
            action=action,
            runtime=runtime,
            license_receipt=runtime.control_license_receipt,
            executor_result=None,
            outcome_receipt=None,
            pending_snapshot_hash=None,
            final_snapshot_hash=final_snapshot_hash,
            resulting_mode=runtime.mode_after,
            resulting_session_id=(
                None
                if runtime.control_session_after is None
                else runtime.control_session_after.session_id
            ),
            journal_event_hashes=tuple(event_hashes),
        )

    if (
        runtime.control_license_receipt is None
        or runtime.control_session_after is None
    ):
        raise ConstitutionalActionError(
            "licensed runtime result is missing control receipt/session"
        )

    pending_state = replace(
        state,
        control_session=runtime.control_session_after,
    )
    pending_snapshot = state_store.save(
        pending_state,
        written_at=runtime.evaluated_at,
        now=runtime.evaluated_at,
    )
    licensed_event = journal.append(
        transaction_id=transaction_id,
        event_type=LICENSED_EVENT,
        payload={
            "action": _action_to_record(action),
            "license_receipt": _license_to_record(
                runtime.control_license_receipt
            ),
            "selected_route_key": runtime.steering_route_key,
            "pending_snapshot_hash": pending_snapshot.snapshot_hash,
        },
        recorded_at=runtime.evaluated_at,
    )
    event_hashes.append(licensed_event.event_hash)

    executor = runtime_bridge or RuntimeBridge()
    executor_result = execute_runtime_adapter(
        action=action,
        payload=payload,
        runtime_bridge=executor,
        executed_at=timestamp,
    )

    outcome_session, outcome_receipt = record_control_outcome(
        runtime.control_session_after,
        state.control_contract,
        action_id=action.action_id,
        success=executor_result.success,
        result_ref=executor_result.result_ref,
        rollback_performed=executor_result.rollback_performed,
        rollback_ref=executor_result.rollback_ref,
        recorded_at=executor_result.executed_at,
    )

    outcome_event = journal.append(
        transaction_id=transaction_id,
        event_type=OUTCOME_EVENT,
        payload={
            "action_id": action.action_id,
            "executor_result": executor_result.to_record(),
            "outcome_receipt": _outcome_to_record(outcome_receipt),
        },
        recorded_at=executor_result.executed_at,
    )
    event_hashes.append(outcome_event.event_hash)

    if outcome_receipt.terminal:
        collapsed_state, collapse_receipt = collapse_control_to_shadow(
            state.promotion_state,
            outcome_session,
            source_ref=outcome_receipt.receipt_id,
            reason=outcome_receipt.reason,
            collapsed_at=executor_result.executed_at,
        )
        final_state = PersistedConstitutionalState(
            promotion_state=collapsed_state,
            collapse_receipt=collapse_receipt,
        )
    else:
        final_state = replace(
            state,
            control_session=outcome_session,
        )

    final_snapshot = state_store.save(
        final_state,
        written_at=executor_result.executed_at,
        now=executor_result.executed_at,
    )
    committed_event = journal.append(
        transaction_id=transaction_id,
        event_type=COMMITTED_EVENT,
        payload={
            "action_id": action.action_id,
            "outcome_receipt_id": outcome_receipt.receipt_id,
            "final_snapshot_hash": final_snapshot.snapshot_hash,
            "resulting_mode": final_state.promotion_state.mode,
            "resulting_session_id": (
                None
                if final_state.control_session is None
                else final_state.control_session.session_id
            ),
        },
        recorded_at=executor_result.executed_at,
    )
    event_hashes.append(committed_event.event_hash)

    return ConstitutionalActionTransactionResult(
        transaction_id=transaction_id,
        action=action,
        runtime=runtime,
        license_receipt=runtime.control_license_receipt,
        executor_result=executor_result,
        outcome_receipt=outcome_receipt,
        pending_snapshot_hash=pending_snapshot.snapshot_hash,
        final_snapshot_hash=final_snapshot.snapshot_hash,
        resulting_mode=final_state.promotion_state.mode,
        resulting_session_id=(
            None
            if final_state.control_session is None
            else final_state.control_session.session_id
        ),
        journal_event_hashes=tuple(event_hashes),
    )


def execute_runtime_adapter(
    *,
    action: ControlActionRequest,
    payload: dict[str, Any],
    runtime_bridge: RuntimeBridge,
    executed_at: float | None = None,
) -> BoundedExecutorResult:
    """Execute the narrow side-effect-free v0.2 runtime-adapter target set."""

    timestamp = time.time() if executed_at is None else float(executed_at)
    if action.operation != "execute":
        raise ConstitutionalActionError(
            "runtime adapter executor requires operation=execute"
        )
    adapter = SUPPORTED_EXECUTOR_TARGETS.get(action.target)
    if adapter is None:
        raise ConstitutionalActionError(
            "unsupported bounded executor target"
        )

    try:
        result = runtime_bridge.execute(
            payload,
            adapter=adapter,
            mode="constitutional_control",
        ).to_record()
        result_ref = _hash_json(
            {
                "target": action.target,
                "action_id": action.action_id,
                "result": result,
            }
        )
        return BoundedExecutorResult(
            success=True,
            target=action.target,
            adapter=adapter,
            result_ref=result_ref,
            result=result,
            error=None,
            rollback_performed=False,
            rollback_ref=None,
            executed_at=timestamp,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        result_ref = _hash_json(
            {
                "target": action.target,
                "action_id": action.action_id,
                "error": error,
            }
        )
        # RuntimeBridge actions are analysis-only and commit no external
        # side effects. Failure rollback is therefore a verified no-op bound
        # to the action's declared rollback reference.
        return BoundedExecutorResult(
            success=False,
            target=action.target,
            adapter=adapter,
            result_ref=result_ref,
            result=None,
            error=error,
            rollback_performed=True,
            rollback_ref=action.rollback_ref,
            executed_at=timestamp,
        )


def parse_resource_spends(
    values: tuple[str, ...] | list[str],
) -> tuple[ResourceSpend, ...]:
    """Parse repeated CLI KIND=AMOUNT resource requests."""
    spends: list[ResourceSpend] = []
    seen: set[str] = set()
    for raw in values:
        if "=" not in raw:
            raise ConstitutionalActionError(
                "resource spend must use KIND=AMOUNT"
            )
        kind, amount_text = raw.split("=", 1)
        kind = kind.strip()
        if not kind:
            raise ConstitutionalActionError(
                "resource spend kind must be non-empty"
            )
        if kind in seen:
            raise ConstitutionalActionError(
                f"resource spend kind '{kind}' may appear only once"
            )
        try:
            amount = float(amount_text)
        except ValueError as exc:
            raise ConstitutionalActionError(
                f"resource spend amount for '{kind}' must be numeric"
            ) from exc
        spends.append(ResourceSpend(kind=kind, amount=amount))
        seen.add(kind)
    return tuple(spends)


def _event_from_record(record: dict[str, Any]) -> ActionJournalEvent:
    if record.get("version") != CONSTITUTIONAL_ACTION_VERSION:
        raise ConstitutionalActionJournalError(
            "unsupported action journal version"
        )

    body = {
        "version": record.get("version"),
        "event_id": record.get("event_id"),
        "transaction_id": record.get("transaction_id"),
        "event_type": record.get("event_type"),
        "recorded_at": record.get("recorded_at"),
        "payload": record.get("payload"),
        "previous_event_hash": record.get("previous_event_hash"),
    }
    expected = _hash_json(body)
    actual = record.get("event_hash")
    if actual != expected:
        raise ConstitutionalActionJournalError(
            "action journal event hash mismatch"
        )
    if not isinstance(record.get("payload"), dict):
        raise ConstitutionalActionJournalError(
            "action journal payload must be an object"
        )

    return ActionJournalEvent(
        event_id=str(record["event_id"]),
        transaction_id=str(record["transaction_id"]),
        event_type=str(record["event_type"]),
        recorded_at=float(record["recorded_at"]),
        payload=dict(record["payload"]),
        previous_event_hash=record.get("previous_event_hash"),
        event_hash=str(actual),
    )


def _action_to_record(action: ControlActionRequest) -> dict[str, Any]:
    return {
        "version": action.version,
        "action_id": action.action_id,
        "actor_id": action.actor_id,
        "operation": action.operation,
        "target": action.target,
        "resource_spends": [
            {"kind": spend.kind, "amount": spend.amount}
            for spend in action.resource_spends
        ],
        "clock_ticks": action.clock_ticks,
        "evidence_refs": list(action.evidence_refs),
        "rollback_ref": action.rollback_ref,
        "requested_at": action.requested_at,
    }


def _license_to_record(
    receipt: ControlActionLicenseReceipt,
) -> dict[str, Any]:
    return {
        "version": receipt.version,
        "receipt_id": receipt.receipt_id,
        "session_id": receipt.session_id,
        "action_id": receipt.action_id,
        "rule_id": receipt.rule_id,
        "decision": receipt.decision,
        "allowed": receipt.allowed,
        "reason": receipt.reason,
        "transition_verdict_id": receipt.transition_verdict_id,
        "warrant_id": receipt.warrant_id,
        "before_actions_used": receipt.before_actions_used,
        "after_actions_used": receipt.after_actions_used,
        "before_clock_ticks_used": receipt.before_clock_ticks_used,
        "after_clock_ticks_used": receipt.after_clock_ticks_used,
        "terminal": receipt.terminal,
        "rollback_ref": receipt.rollback_ref,
        "evaluated_at": receipt.evaluated_at,
        "authority_change": receipt.authority_change,
        "steering_authority_change": receipt.steering_authority_change,
        "constitutional_change": receipt.constitutional_change,
    }


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


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConstitutionalActionJournalError(
            "action journal material must be deterministic JSON"
        ) from exc


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _require_hash(name: str, value: str) -> None:
    if len(value) != 64:
        raise ConstitutionalActionJournalError(
            f"{name} must be SHA-256 hex"
        )
    try:
        int(value, 16)
    except ValueError as exc:
        raise ConstitutionalActionJournalError(
            f"{name} must be hexadecimal"
        ) from exc
