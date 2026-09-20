from dataclasses import replace
import json
import time

import pytest

from phikernel.constitutional_action import (
    COMMITTED_EVENT,
    LICENSED_EVENT,
    OUTCOME_EVENT,
    RECOVERY_EVENT,
    ConstitutionalActionJournal,
    execute_constitutional_action,
)
from phikernel.constitutional_recovery import (
    CLEAN,
    COMMIT_MARKER_MISSING,
    EXPIRED_ACTIVE,
    PENDING_OUTCOME_READY,
    PENDING_UNKNOWN,
    ConstitutionalRecoveryError,
    inspect_action_recovery,
    reconcile_action_recovery,
)
from phikernel.constitutional_store import (
    ConstitutionalPersistenceExpiredError,
    ConstitutionalStateStore,
    PersistedConstitutionalState,
)
from phikernel.control_state import RuntimeControlState
from phikernel.control_witness import (
    ActionUsage,
    BoundedControlGrant,
    ControlActionRule,
    ControlContract,
    ControlPromotionReceipt,
    ControlSession,
    record_control_outcome,
)
from phikernel.heart import RuntimeBridge
from phikernel.transition import ResourceSpend
from phikernel.warrant import ResourceBudget, Warrant
from phikernel.witness_bench import (
    ADVISE,
    BOUNDED_CONTROL,
    SHADOW,
    PromotionAuthorizationReceipt,
    PromotionState,
)


def _bundle() -> dict:
    return {
        "shell_version": "0.2.0",
        "prompt": "recover bounded runtime action",
        "anchor": {"verification": {"valid": True, "reason": "fixture"}},
        "heart": {"running": True},
        "field": {
            "recommended_action": "observe",
            "drift_band": "stable",
        },
        "latest_capsule": {"capsule_id": "cap-recovery"},
        "generated_at": 100.0,
        "next_hint": "stable",
    }


def _bounded_state(
    *,
    base: float | None = None,
    lifetime_seconds: float = 3600.0,
) -> PersistedConstitutionalState:
    t0 = time.time() if base is None else float(base)

    advise_receipt = PromotionAuthorizationReceipt(
        receipt_id="receipt:advise-recovery",
        proposal_id="proposal:advise-recovery",
        witness_report_id="report:advise-recovery",
        witness_report_hash="a" * 64,
        prior_mode=SHADOW,
        resulting_mode=ADVISE,
        prior_revision=0,
        resulting_revision=1,
        human_seal_id="seal:advise-recovery",
        human_actor_id="human:mikey",
        authority_ref="anchor:human",
        applied_at=t0,
    )
    contract = ControlContract.create(
        actor_id="node:runtime",
        action_rules=(
            ControlActionRule(
                rule_id="rule:runtime-recovery",
                operation="execute",
                target="runtime/adapter/legacy",
                max_count=3,
            ),
        ),
        resource_limits=(ResourceBudget("compute_ms", 100.0),),
        max_total_actions=3,
        max_clock_ticks=20.0,
        lifetime_seconds=lifetime_seconds,
        required_evidence_refs=("evidence:approved",),
        created_at=t0 + 1.0,
    )
    proposal_id = "proposal:control-recovery"
    witness_hash = "b" * 64
    warrant = Warrant.issue(
        issuer="human:mikey",
        bearer=contract.actor_id,
        scopes=contract.scopes,
        budgets=(ResourceBudget("compute_ms", 100.0),),
        lifetime_seconds=contract.lifetime_seconds,
        issued_at=t0 + 2.0,
        metadata={
            "control_contract_id": contract.contract_id,
            "control_contract_hash": contract.contract_hash,
            "control_promotion_proposal_id": proposal_id,
            "control_witness_report_hash": witness_hash,
            "human_seal_id": "seal:control-recovery",
        },
    )
    assert warrant.expires_at is not None

    grant = BoundedControlGrant(
        grant_id="grant:control-recovery",
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        promotion_proposal_id=proposal_id,
        witness_report_id="report:control-recovery",
        witness_report_hash=witness_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        human_seal_id="seal:control-recovery",
        human_actor_id="human:mikey",
        authority_ref="anchor:human-control",
        granted_at=t0 + 2.0,
        expires_at=warrant.expires_at,
    )
    control_receipt = ControlPromotionReceipt(
        receipt_id="receipt:control-recovery",
        proposal_id=proposal_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        witness_report_id=grant.witness_report_id,
        witness_report_hash=grant.witness_report_hash,
        prior_mode=ADVISE,
        resulting_mode=BOUNDED_CONTROL,
        prior_revision=1,
        resulting_revision=2,
        grant_id=grant.grant_id,
        warrant_id=warrant.warrant_id,
        human_seal_id=grant.human_seal_id,
        human_actor_id=grant.human_actor_id,
        authority_ref=grant.authority_ref,
        applied_at=t0 + 2.0,
    )
    promotion = PromotionState(
        mode=BOUNDED_CONTROL,
        revision=2,
        last_receipt_id=control_receipt.receipt_id,
        authorized_by_seal_id=grant.human_seal_id,
    )
    session = ControlSession(
        session_id="session:control-recovery",
        grant_id=grant.grant_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        started_at=t0 + 3.0,
        actions_used=0,
        clock_ticks_used=0.0,
        action_usage=(
            ActionUsage(rule_id="rule:runtime-recovery", count=0),
        ),
    )
    return PersistedConstitutionalState(
        promotion_state=promotion,
        advise_receipt=advise_receipt,
        control_contract=contract,
        control_grant=grant,
        control_receipt=control_receipt,
        control_session=session,
    )


def _save_bounded(
    tmp_path,
    *,
    lifetime_seconds: float = 3600.0,
):
    state = _bounded_state(lifetime_seconds=lifetime_seconds)
    store = ConstitutionalStateStore(tmp_path)
    store.save(
        state,
        written_at=state.control_session.started_at,
        now=state.control_session.started_at,
    )
    return store, state


class _InterruptingBridge(RuntimeBridge):
    def execute(self, payload, *, adapter, mode):
        raise KeyboardInterrupt()


def _create_pending_transaction(tmp_path):
    store, state = _save_bounded(tmp_path)
    when = state.control_session.started_at + 1.0
    with pytest.raises(KeyboardInterrupt):
        execute_constitutional_action(
            think_bundle=_bundle(),
            state=state,
            state_store=store,
            runtime_control_state=RuntimeControlState(),
            target="runtime/adapter/legacy",
            payload={"prompt": "normal"},
            resource_spends=(ResourceSpend("compute_ms", 10.0),),
            clock_ticks=2.0,
            evidence_refs=("evidence:approved",),
            rollback_ref="rollback:no-side-effect",
            runtime_bridge=_InterruptingBridge(),
            now=when,
        )
    return store, state, when


def _outcome_record(receipt) -> dict:
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


def _append_success_outcome_for_pending(
    store: ConstitutionalStateStore,
    *,
    when: float,
):
    pending = store.load(now=when)
    journal = ConstitutionalActionJournal(store.runtime_root)
    licensed = journal.history()[0]
    txid = licensed.transaction_id
    result_ref = "c" * 64
    updated, receipt = record_control_outcome(
        pending.control_session,
        pending.control_contract,
        action_id=pending.control_session.pending_action_id,
        success=True,
        result_ref=result_ref,
        recorded_at=when + 0.1,
    )
    journal.append(
        transaction_id=txid,
        event_type=OUTCOME_EVENT,
        payload={
            "action_id": receipt.action_id,
            "executor_result": {
                "version": "0.2.0",
                "success": True,
                "target": "runtime/adapter/legacy",
                "adapter": "legacy",
                "result_ref": result_ref,
                "result": {"adapter": "legacy", "fixture": True},
                "error": None,
                "rollback_performed": False,
                "rollback_ref": None,
                "executed_at": when + 0.1,
            },
            "outcome_receipt": _outcome_record(receipt),
        },
        recorded_at=when + 0.1,
    )
    return pending, updated, receipt, txid


def test_clean_genesis_inspection_is_non_mutating(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)

    inspection = inspect_action_recovery(store, now=100.0)
    result = reconcile_action_recovery(store, now=100.0)

    assert inspection.status == CLEAN
    assert inspection.mode == SHADOW
    assert inspection.can_reconcile_without_execution is False
    assert result.recovery_kind == "NOOP_CLEAN"
    assert result.state_changed is False
    assert result.executor_called is False
    assert store.exists() is False


def test_clean_bounded_state_needs_no_recovery(tmp_path) -> None:
    store, state = _save_bounded(tmp_path)
    now = state.control_session.started_at + 1.0

    inspection = inspect_action_recovery(store, now=now)

    assert inspection.status == CLEAN
    assert inspection.mode == BOUNDED_CONTROL
    assert inspection.action_id is None


def test_license_only_crash_is_classified_unknown_and_never_replayed(tmp_path) -> None:
    store, _, when = _create_pending_transaction(tmp_path)

    inspection = inspect_action_recovery(store, now=when + 1.0)
    pending = store.load(now=when + 1.0)

    assert inspection.status == PENDING_UNKNOWN
    assert inspection.transaction_id is not None
    assert inspection.action_id == pending.control_session.pending_action_id
    assert pending.control_session.actions_used == 1
    assert pending.control_session.clock_ticks_used == pytest.approx(2.0)
    assert pending.control_session.warrant.remaining("compute_ms") == pytest.approx(90.0)


def test_unknown_crash_outcome_collapses_without_refund_or_reexecution(tmp_path) -> None:
    store, _, when = _create_pending_transaction(tmp_path)

    result = reconcile_action_recovery(store, now=when + 1.0)
    loaded = store.load(now=when + 1.0)
    history = store.history()
    journal = ConstitutionalActionJournal(tmp_path).history()

    assert result.inspection_before.status == PENDING_UNKNOWN
    assert result.recovery_kind == "UNKNOWN_OUTCOME_NO_REPLAY_COLLAPSE"
    assert result.executor_called is False
    assert result.resulting_mode == SHADOW
    assert result.resulting_revision == 3
    assert result.generated_outcome_receipt_id is not None
    assert loaded.promotion_state.mode == SHADOW
    assert loaded.control_session is None

    pending_snapshot = history[-2]
    assert pending_snapshot.state.control_session.actions_used == 1
    assert pending_snapshot.state.control_session.clock_ticks_used == pytest.approx(2.0)
    assert pending_snapshot.state.control_session.warrant.remaining("compute_ms") == pytest.approx(90.0)

    assert [event.event_type for event in journal] == [
        LICENSED_EVENT,
        RECOVERY_EVENT,
        COMMITTED_EVENT,
    ]


def test_pending_with_journaled_success_outcome_is_completed_without_execution(tmp_path) -> None:
    store, _, when = _create_pending_transaction(tmp_path)
    _, expected_session, receipt, _ = _append_success_outcome_for_pending(
        store,
        when=when,
    )

    inspection = inspect_action_recovery(store, now=when + 1.0)
    result = reconcile_action_recovery(store, now=when + 1.0)
    loaded = store.load(now=when + 1.0)
    journal = ConstitutionalActionJournal(tmp_path).history()

    assert inspection.status == PENDING_OUTCOME_READY
    assert result.recovery_kind == "APPLIED_JOURNALED_OUTCOME"
    assert result.executor_called is False
    assert result.resulting_mode == BOUNDED_CONTROL
    assert result.generated_outcome_receipt_id is None

    assert loaded.control_session.pending_action_id is None
    assert loaded.control_session.actions_used == expected_session.actions_used
    assert loaded.control_session.clock_ticks_used == pytest.approx(
        expected_session.clock_ticks_used
    )
    assert loaded.control_session.warrant.remaining("compute_ms") == pytest.approx(90.0)
    assert loaded.control_session.last_receipt_id == receipt.receipt_id
    assert [event.event_type for event in journal] == [
        LICENSED_EVENT,
        OUTCOME_EVENT,
        RECOVERY_EVENT,
        COMMITTED_EVENT,
    ]


def test_pending_with_journaled_failed_outcome_collapses_without_reexecution(tmp_path) -> None:
    store, _, when = _create_pending_transaction(tmp_path)
    pending = store.load(now=when)
    journal = ConstitutionalActionJournal(tmp_path)
    licensed = journal.history()[0]
    result_ref = "d" * 64
    _, receipt = record_control_outcome(
        pending.control_session,
        pending.control_contract,
        action_id=pending.control_session.pending_action_id,
        success=False,
        result_ref=result_ref,
        rollback_performed=True,
        rollback_ref=pending.control_session.pending_rollback_ref,
        recorded_at=when + 0.1,
    )
    journal.append(
        transaction_id=licensed.transaction_id,
        event_type=OUTCOME_EVENT,
        payload={
            "action_id": receipt.action_id,
            "executor_result": {
                "version": "0.2.0",
                "success": False,
                "target": "runtime/adapter/legacy",
                "adapter": "legacy",
                "result_ref": result_ref,
                "result": None,
                "error": "RuntimeError: fixture",
                "rollback_performed": True,
                "rollback_ref": pending.control_session.pending_rollback_ref,
                "executed_at": when + 0.1,
            },
            "outcome_receipt": _outcome_record(receipt),
        },
        recorded_at=when + 0.1,
    )

    result = reconcile_action_recovery(store, now=when + 1.0)
    loaded = store.load(now=when + 1.0)

    assert result.inspection_before.status == PENDING_OUTCOME_READY
    assert result.recovery_kind == "APPLIED_TERMINAL_OUTCOME"
    assert result.executor_called is False
    assert result.resulting_mode == SHADOW
    assert loaded.promotion_state.mode == SHADOW
    assert loaded.control_session is None


def test_completed_state_missing_commit_marker_repairs_journal_only(tmp_path) -> None:
    store, state = _save_bounded(tmp_path)
    when = state.control_session.started_at + 1.0
    completed = execute_constitutional_action(
        think_bundle=_bundle(),
        state=state,
        state_store=store,
        runtime_control_state=RuntimeControlState(),
        target="runtime/adapter/legacy",
        payload={"prompt": "normal"},
        resource_spends=(ResourceSpend("compute_ms", 10.0),),
        clock_ticks=2.0,
        evidence_refs=("evidence:approved",),
        rollback_ref="rollback:no-side-effect",
        now=when,
    )
    assert completed.success is True

    journal = ConstitutionalActionJournal(tmp_path)
    rows = journal.path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 3
    journal.path.write_text("\n".join(rows[:2]) + "\n", encoding="utf-8")

    snapshot_before = store.load_snapshot(now=when + 1.0)
    inspection = inspect_action_recovery(store, now=when + 1.0)
    result = reconcile_action_recovery(store, now=when + 1.0)
    snapshot_after = store.load_snapshot(now=when + 1.0)
    events = journal.history()

    assert inspection.status == COMMIT_MARKER_MISSING
    assert result.recovery_kind == "REPAIRED_COMMIT_MARKER"
    assert result.executor_called is False
    assert result.state_changed is False
    assert snapshot_after.snapshot_hash == snapshot_before.snapshot_hash
    assert [event.event_type for event in events] == [
        LICENSED_EVENT,
        OUTCOME_EVENT,
        RECOVERY_EVENT,
        COMMITTED_EVENT,
    ]


def test_expired_active_lease_can_be_structurally_loaded_only_for_collapse(tmp_path) -> None:
    state = _bounded_state(lifetime_seconds=5.0)
    store = ConstitutionalStateStore(tmp_path)
    store.save(
        state,
        written_at=state.control_session.started_at,
        now=state.control_session.started_at,
    )
    expired_at = state.control_grant.expires_at + 1.0

    with pytest.raises(ConstitutionalPersistenceExpiredError):
        store.load(now=expired_at)

    structural = store.load_snapshot_for_recovery()
    inspection = inspect_action_recovery(store, now=expired_at)

    assert structural.state.promotion_state.mode == BOUNDED_CONTROL
    assert inspection.status == EXPIRED_ACTIVE

    result = reconcile_action_recovery(store, now=expired_at)
    loaded = store.load(now=expired_at)

    assert result.recovery_kind == "EXPIRED_LEASE_COLLAPSE"
    assert result.executor_called is False
    assert result.resulting_mode == SHADOW
    assert loaded.promotion_state.mode == SHADOW
    assert loaded.promotion_state.revision == 3


def test_recovery_rejects_outcome_that_does_not_match_pending_result_ref(tmp_path) -> None:
    store, _, when = _create_pending_transaction(tmp_path)
    pending = store.load(now=when)
    journal = ConstitutionalActionJournal(tmp_path)
    licensed = journal.history()[0]
    _, receipt = record_control_outcome(
        pending.control_session,
        pending.control_contract,
        action_id=pending.control_session.pending_action_id,
        success=True,
        result_ref="e" * 64,
        recorded_at=when + 0.1,
    )
    journal.append(
        transaction_id=licensed.transaction_id,
        event_type=OUTCOME_EVENT,
        payload={
            "action_id": receipt.action_id,
            "executor_result": {
                "version": "0.2.0",
                "success": True,
                "target": "runtime/adapter/legacy",
                "adapter": "legacy",
                "result_ref": "f" * 64,
                "result": {"fixture": True},
                "error": None,
                "rollback_performed": False,
                "rollback_ref": None,
                "executed_at": when + 0.1,
            },
            "outcome_receipt": _outcome_record(receipt),
        },
        recorded_at=when + 0.1,
    )

    with pytest.raises(ConstitutionalRecoveryError):
        inspect_action_recovery(store, now=when + 1.0)


def test_recovery_rejects_pending_state_with_multiple_license_transactions(tmp_path) -> None:
    store, _, when = _create_pending_transaction(tmp_path)
    pending = store.load(now=when)
    journal = ConstitutionalActionJournal(tmp_path)
    first = journal.history()[0]

    duplicate_payload = json.loads(json.dumps(first.payload))
    journal.append(
        transaction_id="tx:duplicate",
        event_type=LICENSED_EVENT,
        payload=duplicate_payload,
        recorded_at=when + 0.2,
    )

    with pytest.raises(ConstitutionalRecoveryError):
        inspect_action_recovery(store, now=when + 1.0)
