from dataclasses import replace
import json
import time

import pytest

from phikernel.constitutional_action import (
    COMMITTED_EVENT,
    LICENSED_EVENT,
    OUTCOME_EVENT,
    REFUSED_EVENT,
    ConstitutionalActionError,
    ConstitutionalActionJournal,
    ConstitutionalActionJournalError,
    execute_constitutional_action,
    parse_resource_spends,
)
from phikernel.constitutional_store import (
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


def _bundle(prompt: str = "run bounded runtime analysis") -> dict:
    return {
        "shell_version": "0.2.0",
        "prompt": prompt,
        "anchor": {
            "verification": {
                "valid": True,
                "reason": "fixture",
            }
        },
        "heart": {"running": True},
        "field": {
            "recommended_action": "observe",
            "drift_band": "stable",
        },
        "latest_capsule": {"capsule_id": "cap-fixture"},
        "generated_at": 100.0,
        "next_hint": "stable",
    }


def _bounded_state(
    *,
    target: str = "runtime/adapter/legacy",
    max_count: int = 3,
    max_total_actions: int = 3,
    compute_limit: float = 100.0,
    required_evidence=("evidence:approved",),
    base: float | None = None,
) -> PersistedConstitutionalState:
    t0 = time.time() if base is None else float(base)

    advise_receipt = PromotionAuthorizationReceipt(
        receipt_id="receipt:advise-action",
        proposal_id="proposal:advise-action",
        witness_report_id="report:advise-action",
        witness_report_hash="a" * 64,
        prior_mode=SHADOW,
        resulting_mode=ADVISE,
        prior_revision=0,
        resulting_revision=1,
        human_seal_id="seal:advise-action",
        human_actor_id="human:mikey",
        authority_ref="anchor:human",
        applied_at=t0,
    )

    contract = ControlContract.create(
        actor_id="node:runtime",
        action_rules=(
            ControlActionRule(
                rule_id="rule:runtime",
                operation="execute",
                target=target,
                max_count=max_count,
            ),
        ),
        resource_limits=(
            ResourceBudget("compute_ms", compute_limit),
        ),
        max_total_actions=max_total_actions,
        max_clock_ticks=20.0,
        lifetime_seconds=3600.0,
        required_evidence_refs=required_evidence,
        created_at=t0 + 1.0,
    )
    proposal_id = "proposal:control-action"
    witness_hash = "b" * 64
    warrant = Warrant.issue(
        issuer="human:mikey",
        bearer=contract.actor_id,
        scopes=contract.scopes,
        budgets=(
            ResourceBudget("compute_ms", compute_limit),
        ),
        lifetime_seconds=contract.lifetime_seconds,
        issued_at=t0 + 2.0,
        metadata={
            "control_contract_id": contract.contract_id,
            "control_contract_hash": contract.contract_hash,
            "control_promotion_proposal_id": proposal_id,
            "control_witness_report_hash": witness_hash,
            "human_seal_id": "seal:control-action",
        },
    )
    assert warrant.expires_at is not None

    grant = BoundedControlGrant(
        grant_id="grant:control-action",
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        promotion_proposal_id=proposal_id,
        witness_report_id="report:control-action",
        witness_report_hash=witness_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        human_seal_id="seal:control-action",
        human_actor_id="human:mikey",
        authority_ref="anchor:human-control",
        granted_at=t0 + 2.0,
        expires_at=warrant.expires_at,
    )
    control_receipt = ControlPromotionReceipt(
        receipt_id="receipt:control-action",
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
        session_id="session:control-action",
        grant_id=grant.grant_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        started_at=t0 + 3.0,
        actions_used=0,
        clock_ticks_used=0.0,
        action_usage=(
            ActionUsage(rule_id="rule:runtime", count=0),
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


def _save_bounded(tmp_path, state=None):
    state = state or _bounded_state()
    store = ConstitutionalStateStore(tmp_path)
    now = state.control_session.started_at
    store.save(state, written_at=now, now=now)
    return store, state


def test_parse_resource_spends() -> None:
    spends = parse_resource_spends(["compute_ms=12.5", "gpu_ms=2"])

    assert spends == (
        ResourceSpend("compute_ms", 12.5),
        ResourceSpend("gpu_ms", 2.0),
    )


def test_parse_resource_spends_rejects_duplicate_kind() -> None:
    with pytest.raises(ConstitutionalActionError):
        parse_resource_spends(["compute_ms=1", "compute_ms=2"])


def test_arbitrary_executor_target_is_rejected_before_license(tmp_path) -> None:
    store, state = _save_bounded(tmp_path)

    with pytest.raises(ConstitutionalActionError):
        execute_constitutional_action(
            think_bundle=_bundle(),
            state=state,
            state_store=store,
            runtime_control_state=RuntimeControlState(),
            target="tool/python",
            payload={"prompt": "hello"},
            resource_spends=(ResourceSpend("compute_ms", 10.0),),
            clock_ticks=1.0,
            evidence_refs=("evidence:approved",),
            rollback_ref="rollback:no-side-effect",
            now=state.control_session.started_at + 1.0,
        )

    loaded = store.load(now=state.control_session.started_at + 1.0)
    assert loaded.control_session.actions_used == 0
    assert len(store.history()) == 1


def test_action_requires_persisted_bounded_control(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    state = PersistedConstitutionalState.genesis()

    with pytest.raises(ConstitutionalActionError):
        execute_constitutional_action(
            think_bundle=_bundle(),
            state=state,
            state_store=store,
            runtime_control_state=RuntimeControlState(),
            target="runtime/adapter/legacy",
            payload={"prompt": "hello"},
            resource_spends=(),
            clock_ticks=1.0,
            evidence_refs=(),
            rollback_ref="rollback:no-side-effect",
            now=100.0,
        )


def test_successful_action_is_licensed_executed_receipted_and_persisted(tmp_path) -> None:
    store, state = _save_bounded(tmp_path)
    when = state.control_session.started_at + 1.0

    result = execute_constitutional_action(
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

    loaded = store.load(now=when)
    journal = ConstitutionalActionJournal(tmp_path).history()

    assert result.runtime.disposition == "BOUNDED_LICENSED"
    assert result.license_receipt.allowed is True
    assert result.executed is True
    assert result.success is True
    assert result.executor_result.result["adapter"] == "legacy"
    assert result.outcome_receipt.success is True
    assert result.outcome_receipt.terminal is False
    assert result.resulting_mode == BOUNDED_CONTROL
    assert result.pending_snapshot_hash
    assert result.final_snapshot_hash

    assert loaded.promotion_state.mode == BOUNDED_CONTROL
    assert loaded.control_session.actions_used == 1
    assert loaded.control_session.clock_ticks_used == pytest.approx(2.0)
    assert loaded.control_session.pending_action_id is None
    assert loaded.control_session.warrant.remaining("compute_ms") == pytest.approx(90.0)

    assert [event.event_type for event in journal] == [
        LICENSED_EVENT,
        OUTCOME_EVENT,
        COMMITTED_EVENT,
    ]
    assert len(store.history()) == 3


def test_second_action_uses_persisted_successor_session(tmp_path) -> None:
    store, initial = _save_bounded(tmp_path)
    t1 = initial.control_session.started_at + 1.0
    first = execute_constitutional_action(
        think_bundle=_bundle(),
        state=initial,
        state_store=store,
        runtime_control_state=RuntimeControlState(),
        target="runtime/adapter/legacy",
        payload={"prompt": "first"},
        resource_spends=(ResourceSpend("compute_ms", 10.0),),
        clock_ticks=2.0,
        evidence_refs=("evidence:approved",),
        rollback_ref="rollback:no-side-effect",
        now=t1,
    )
    assert first.success is True

    successor = store.load(now=t1 + 1.0)
    second = execute_constitutional_action(
        think_bundle=_bundle(),
        state=successor,
        state_store=store,
        runtime_control_state=RuntimeControlState(),
        target="runtime/adapter/legacy",
        payload={"prompt": "second"},
        resource_spends=(ResourceSpend("compute_ms", 15.0),),
        clock_ticks=3.0,
        evidence_refs=("evidence:approved",),
        rollback_ref="rollback:no-side-effect",
        now=t1 + 1.0,
    )

    loaded = store.load(now=t1 + 1.0)

    assert second.success is True
    assert loaded.control_session.actions_used == 2
    assert loaded.control_session.clock_ticks_used == pytest.approx(5.0)
    assert loaded.control_session.warrant.remaining("compute_ms") == pytest.approx(75.0)
    assert len(ConstitutionalActionJournal(tmp_path).history()) == 6


def test_missing_required_evidence_refuses_and_durably_collapses(tmp_path) -> None:
    store, state = _save_bounded(tmp_path)
    when = state.control_session.started_at + 1.0

    result = execute_constitutional_action(
        think_bundle=_bundle(),
        state=state,
        state_store=store,
        runtime_control_state=RuntimeControlState(),
        target="runtime/adapter/legacy",
        payload={"prompt": "normal"},
        resource_spends=(ResourceSpend("compute_ms", 10.0),),
        clock_ticks=2.0,
        evidence_refs=(),
        rollback_ref="rollback:no-side-effect",
        now=when,
    )

    loaded = store.load(now=when)
    journal = ConstitutionalActionJournal(tmp_path).history()

    assert result.executed is False
    assert result.success is False
    assert result.license_receipt.allowed is False
    assert result.license_receipt.terminal is True
    assert result.resulting_mode == SHADOW
    assert loaded.promotion_state.mode == SHADOW
    assert loaded.promotion_state.revision == 3
    assert loaded.control_session is None
    assert [event.event_type for event in journal] == [REFUSED_EVENT]


class _FailingBridge(RuntimeBridge):
    def execute(self, payload, *, adapter, mode):
        raise RuntimeError("fixture executor failure")


def test_executor_failure_rolls_back_and_collapses_to_shadow(tmp_path) -> None:
    store, state = _save_bounded(tmp_path)
    when = state.control_session.started_at + 1.0

    result = execute_constitutional_action(
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
        runtime_bridge=_FailingBridge(),
        now=when,
    )

    loaded = store.load(now=when)
    journal = ConstitutionalActionJournal(tmp_path).history()

    assert result.executed is True
    assert result.success is False
    assert result.executor_result.success is False
    assert result.executor_result.rollback_performed is True
    assert result.executor_result.rollback_ref == "rollback:no-side-effect"
    assert result.outcome_receipt.success is False
    assert result.outcome_receipt.rollback_satisfied is True
    assert result.outcome_receipt.terminal is True
    assert result.resulting_mode == SHADOW
    assert loaded.promotion_state.mode == SHADOW
    assert loaded.control_session is None
    assert [event.event_type for event in journal] == [
        LICENSED_EVENT,
        OUTCOME_EVENT,
        COMMITTED_EVENT,
    ]


class _InterruptingBridge(RuntimeBridge):
    def execute(self, payload, *, adapter, mode):
        raise KeyboardInterrupt()


def test_process_interrupt_after_license_leaves_pending_spent_state(tmp_path) -> None:
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

    loaded = store.load(now=when)
    journal = ConstitutionalActionJournal(tmp_path).history()

    assert loaded.promotion_state.mode == BOUNDED_CONTROL
    assert loaded.control_session.actions_used == 1
    assert loaded.control_session.clock_ticks_used == pytest.approx(2.0)
    assert loaded.control_session.warrant.remaining("compute_ms") == pytest.approx(90.0)
    assert loaded.control_session.pending_action_id is not None
    assert len(store.history()) == 2
    assert [event.event_type for event in journal] == [LICENSED_EVENT]


def test_pending_action_prevents_silent_next_action_reuse(tmp_path) -> None:
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

    pending = store.load(now=when + 1.0)
    result = execute_constitutional_action(
        think_bundle=_bundle(),
        state=pending,
        state_store=store,
        runtime_control_state=RuntimeControlState(),
        target="runtime/adapter/legacy",
        payload={"prompt": "second"},
        resource_spends=(ResourceSpend("compute_ms", 1.0),),
        clock_ticks=1.0,
        evidence_refs=("evidence:approved",),
        rollback_ref="rollback:no-side-effect",
        now=when + 1.0,
    )

    loaded = store.load(now=when + 1.0)

    assert result.executed is False
    assert result.license_receipt.allowed is False
    assert "post-action receipt missing" in result.license_receipt.reason
    assert result.resulting_mode == SHADOW
    assert loaded.promotion_state.mode == SHADOW


def test_quarantine_blocks_action_and_persists_collapse(tmp_path) -> None:
    store, state = _save_bounded(tmp_path)
    when = state.control_session.started_at + 1.0

    result = execute_constitutional_action(
        think_bundle=_bundle(),
        state=state,
        state_store=store,
        runtime_control_state=RuntimeControlState(quarantined=True),
        target="runtime/adapter/legacy",
        payload={"prompt": "normal"},
        resource_spends=(ResourceSpend("compute_ms", 10.0),),
        clock_ticks=2.0,
        evidence_refs=("evidence:approved",),
        rollback_ref="rollback:no-side-effect",
        now=when,
    )

    loaded = store.load(now=when)

    assert result.executed is False
    assert result.runtime.disposition == "RUNTIME_BLOCKED"
    assert result.resulting_mode == SHADOW
    assert loaded.promotion_state.mode == SHADOW


def test_action_journal_detects_tampering(tmp_path) -> None:
    journal = ConstitutionalActionJournal(tmp_path)
    journal.append(
        transaction_id="tx:1",
        event_type=REFUSED_EVENT,
        payload={"reason": "fixture"},
        recorded_at=100.0,
    )

    rows = journal.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(rows[0])
    record["payload"]["reason"] = "tampered"
    journal.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ConstitutionalActionJournalError):
        journal.history()
