from dataclasses import replace
import json

import pytest

from phikernel.constitutional_store import (
    ConstitutionalPersistenceExpiredError,
    ConstitutionalPersistenceIntegrityError,
    ConstitutionalPersistenceLineageError,
    ConstitutionalStateStore,
    PersistedConstitutionalState,
)
from phikernel.control_witness import (
    ActionUsage,
    BoundedControlGrant,
    ControlActionRule,
    ControlContract,
    ControlPromotionReceipt,
    ControlSession,
)
from phikernel.warrant import ResourceBudget, Warrant
from phikernel.witness_bench import (
    ADVISE,
    BOUNDED_CONTROL,
    SHADOW,
    PromotionAuthorizationReceipt,
    PromotionCollapseReceipt,
    PromotionState,
)


def _advise():
    receipt = PromotionAuthorizationReceipt(
        receipt_id="receipt:advise",
        proposal_id="proposal:advise",
        witness_report_id="report:advise",
        witness_report_hash="a" * 64,
        prior_mode=SHADOW,
        resulting_mode=ADVISE,
        prior_revision=0,
        resulting_revision=1,
        human_seal_id="seal:advise",
        human_actor_id="human:mikey",
        authority_ref="anchor:human",
        applied_at=110.0,
    )
    state = PromotionState(
        mode=ADVISE,
        revision=1,
        last_receipt_id=receipt.receipt_id,
        authorized_by_seal_id=receipt.human_seal_id,
    )
    return PersistedConstitutionalState(
        promotion_state=state,
        advise_receipt=receipt,
    )


def _bounded():
    advise = _advise().advise_receipt
    assert advise is not None

    contract = ControlContract.create(
        actor_id="node:flow",
        action_rules=(
            ControlActionRule(
                rule_id="rule:python",
                operation="execute",
                target="tool/python",
                max_count=3,
            ),
        ),
        resource_limits=(ResourceBudget("compute_ms", 100.0),),
        max_total_actions=3,
        max_clock_ticks=20.0,
        lifetime_seconds=1000.0,
        required_evidence_refs=("evidence:approved",),
        created_at=120.0,
    )

    proposal_id = "proposal:control"
    witness_hash = "b" * 64
    warrant = Warrant.issue(
        issuer="human:mikey",
        bearer=contract.actor_id,
        scopes=contract.scopes,
        budgets=(
            ResourceBudget("compute_ms", 100.0),
        ),
        lifetime_seconds=contract.lifetime_seconds,
        issued_at=130.0,
        metadata={
            "control_contract_id": contract.contract_id,
            "control_contract_hash": contract.contract_hash,
            "control_promotion_proposal_id": proposal_id,
            "control_witness_report_hash": witness_hash,
            "human_seal_id": "seal:control",
        },
    )
    assert warrant.expires_at is not None

    grant = BoundedControlGrant(
        grant_id="grant:control",
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        promotion_proposal_id=proposal_id,
        witness_report_id="report:control",
        witness_report_hash=witness_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        human_seal_id="seal:control",
        human_actor_id="human:mikey",
        authority_ref="anchor:human-control",
        granted_at=130.0,
        expires_at=warrant.expires_at,
    )
    receipt = ControlPromotionReceipt(
        receipt_id="receipt:control",
        proposal_id=proposal_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        witness_report_id=grant.witness_report_id,
        witness_report_hash=witness_hash,
        prior_mode=ADVISE,
        resulting_mode=BOUNDED_CONTROL,
        prior_revision=1,
        resulting_revision=2,
        grant_id=grant.grant_id,
        warrant_id=warrant.warrant_id,
        human_seal_id=grant.human_seal_id,
        human_actor_id=grant.human_actor_id,
        authority_ref=grant.authority_ref,
        applied_at=130.0,
    )
    promotion = PromotionState(
        mode=BOUNDED_CONTROL,
        revision=2,
        last_receipt_id=receipt.receipt_id,
        authorized_by_seal_id=grant.human_seal_id,
    )
    session = ControlSession(
        session_id="session:control",
        grant_id=grant.grant_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        started_at=131.0,
        actions_used=0,
        clock_ticks_used=0.0,
        action_usage=(ActionUsage(rule_id="rule:python", count=0),),
    )
    return PersistedConstitutionalState(
        promotion_state=promotion,
        advise_receipt=advise,
        control_contract=contract,
        control_grant=grant,
        control_receipt=receipt,
        control_session=session,
    )


def _collapsed_from_advise():
    collapse = PromotionCollapseReceipt(
        receipt_id="receipt:collapse",
        prior_mode=ADVISE,
        resulting_mode=SHADOW,
        prior_revision=1,
        resulting_revision=2,
        source_ref="runtime:health",
        reason="invariant failure",
        collapsed_at=150.0,
    )
    state = PromotionState(
        mode=SHADOW,
        revision=2,
        last_receipt_id=collapse.receipt_id,
        authorized_by_seal_id=None,
    )
    return PersistedConstitutionalState(
        promotion_state=state,
        collapse_receipt=collapse,
    )


def test_missing_store_loads_genesis_shadow(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)

    state = store.load(now=100.0)

    assert state.promotion_state.mode == SHADOW
    assert state.promotion_state.revision == 0
    assert store.exists() is False


def test_genesis_shadow_round_trips_and_creates_history(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)

    snapshot = store.save(
        PersistedConstitutionalState.genesis(),
        written_at=100.0,
        now=100.0,
    )
    loaded = store.load(now=100.0)

    assert loaded.promotion_state == PromotionState.genesis()
    assert store.exists() is True
    assert len(store.history()) == 1
    assert store.history()[0].snapshot_hash == snapshot.snapshot_hash


def test_advise_round_trip_requires_exact_authorization_receipt(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    advise = _advise()

    store.save(advise, written_at=120.0, now=120.0)
    loaded = store.load(now=120.0)

    assert loaded.promotion_state.mode == ADVISE
    assert loaded.promotion_state.revision == 1
    assert loaded.advise_receipt == advise.advise_receipt
    assert loaded.control_grant is None


def test_advise_without_receipt_fails_closed(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    state = PromotionState(
        mode=ADVISE,
        revision=1,
        last_receipt_id="receipt:missing",
        authorized_by_seal_id="seal:advise",
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(
            PersistedConstitutionalState(promotion_state=state),
            written_at=120.0,
            now=120.0,
        )


def test_advise_wrong_seal_lineage_fails_closed(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    advise = _advise()
    forged = replace(
        advise,
        promotion_state=replace(
            advise.promotion_state,
            authorized_by_seal_id="seal:other",
        ),
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(forged, written_at=120.0, now=120.0)


def test_bounded_control_full_lineage_round_trips(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()

    store.save(bounded, written_at=140.0, now=140.0)
    loaded = store.load(now=140.0)

    assert loaded.promotion_state.mode == BOUNDED_CONTROL
    assert loaded.promotion_state.revision == 2
    assert loaded.control_contract.contract_hash == bounded.control_contract.contract_hash
    assert loaded.control_grant.grant_id == bounded.control_grant.grant_id
    assert loaded.control_session.session_id == bounded.control_session.session_id
    assert loaded.control_session.warrant.warrant_id == bounded.control_grant.warrant.warrant_id


def test_bounded_control_missing_prior_advise_receipt_fails_closed(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = replace(_bounded(), advise_receipt=None)

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(bounded, written_at=140.0, now=140.0)


def test_bounded_control_grant_seal_must_match_promotion_state(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()
    forged_grant = replace(
        bounded.control_grant,
        human_seal_id="seal:other",
    )
    forged = replace(bounded, control_grant=forged_grant)

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(forged, written_at=140.0, now=140.0)


def test_bounded_session_warrant_must_match_grant_identity(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()
    other_warrant = replace(
        bounded.control_session.warrant,
        warrant_id="warrant:other",
    )
    forged_session = replace(
        bounded.control_session,
        warrant=other_warrant,
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(
            replace(bounded, control_session=forged_session),
            written_at=140.0,
            now=140.0,
        )


def test_bounded_session_cannot_exceed_contract_action_limit(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()
    forged_session = replace(
        bounded.control_session,
        actions_used=4,
        action_usage=(ActionUsage("rule:python", 4),),
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(
            replace(bounded, control_session=forged_session),
            written_at=140.0,
            now=140.0,
        )


def test_bounded_session_cannot_exceed_clock_budget(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()
    forged_session = replace(
        bounded.control_session,
        clock_ticks_used=21.0,
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(
            replace(bounded, control_session=forged_session),
            written_at=140.0,
            now=140.0,
        )


def test_bounded_authority_does_not_resurrect_after_expiry(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()
    store.save(bounded, written_at=140.0, now=140.0)

    with pytest.raises(ConstitutionalPersistenceExpiredError):
        store.load(now=bounded.control_grant.expires_at)


def test_revoked_bounded_warrant_cannot_be_persisted_as_active(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()
    revoked = bounded.control_session.warrant.revoke(
        reason="operator veto",
    )
    stopped = replace(
        bounded.control_session,
        warrant=revoked,
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(
            replace(bounded, control_session=stopped),
            written_at=140.0,
            now=140.0,
        )


def test_same_revision_session_progress_is_persistable_and_monotonic(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()
    store.save(bounded, written_at=140.0, now=140.0)

    updated_warrant, _ = bounded.control_session.warrant.spend(
        actor_id=bounded.control_session.actor_id,
        resource_kind="compute_ms",
        amount=10.0,
        now=145.0,
    )
    progressed_session = replace(
        bounded.control_session,
        warrant=updated_warrant,
        actions_used=1,
        clock_ticks_used=2.0,
        action_usage=(ActionUsage("rule:python", 1),),
        last_receipt_id="action-license:1",
    )
    progressed = replace(
        bounded,
        control_session=progressed_session,
    )

    store.save(progressed, written_at=145.0, now=145.0)
    loaded = store.load(now=145.0)

    assert loaded.promotion_state.revision == 2
    assert loaded.control_session.actions_used == 1
    assert loaded.control_session.clock_ticks_used == pytest.approx(2.0)
    assert loaded.control_session.warrant.remaining("compute_ms") == pytest.approx(90.0)
    assert len(store.history()) == 2


def test_same_revision_session_spend_may_not_move_backward(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()

    spent_warrant, _ = bounded.control_session.warrant.spend(
        actor_id=bounded.control_session.actor_id,
        resource_kind="compute_ms",
        amount=20.0,
        now=140.0,
    )
    progressed = replace(
        bounded,
        control_session=replace(
            bounded.control_session,
            warrant=spent_warrant,
            actions_used=1,
            clock_ticks_used=2.0,
            action_usage=(ActionUsage("rule:python", 1),),
        ),
    )
    store.save(progressed, written_at=140.0, now=140.0)

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(bounded, written_at=145.0, now=145.0)


def test_same_revision_may_not_swap_control_grant(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bounded = _bounded()
    store.save(bounded, written_at=140.0, now=140.0)

    forged_grant = replace(
        bounded.control_grant,
        grant_id="grant:replacement",
    )
    forged_session = replace(
        bounded.control_session,
        grant_id="grant:replacement",
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(
            replace(
                bounded,
                control_grant=forged_grant,
                control_session=forged_session,
            ),
            written_at=145.0,
            now=145.0,
        )


def test_promotion_revision_may_not_move_backward(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    store.save(_advise(), written_at=120.0, now=120.0)

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(
            PersistedConstitutionalState.genesis(),
            written_at=130.0,
            now=130.0,
        )


def test_promotion_revision_may_not_skip_forward(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    store.save(
        PersistedConstitutionalState.genesis(),
        written_at=100.0,
        now=100.0,
    )
    advise = _advise()
    skipped = replace(
        advise,
        promotion_state=replace(
            advise.promotion_state,
            revision=2,
        ),
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(skipped, written_at=120.0, now=120.0)


def test_illegal_shadow_to_bounded_transition_is_rejected(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    store.save(
        PersistedConstitutionalState.genesis(),
        written_at=100.0,
        now=100.0,
    )
    bounded = _bounded()
    forced_revision = replace(
        bounded,
        promotion_state=replace(
            bounded.promotion_state,
            revision=1,
        ),
        control_receipt=replace(
            bounded.control_receipt,
            prior_revision=0,
            resulting_revision=1,
        ),
        advise_receipt=replace(
            bounded.advise_receipt,
            prior_revision=-1,
            resulting_revision=0,
        ),
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(forced_revision, written_at=140.0, now=140.0)


def test_advise_may_collapse_to_shadow_with_receipt(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    store.save(_advise(), written_at=120.0, now=120.0)

    collapsed = _collapsed_from_advise()
    store.save(collapsed, written_at=150.0, now=150.0)
    loaded = store.load(now=150.0)

    assert loaded.promotion_state.mode == SHADOW
    assert loaded.promotion_state.revision == 2
    assert loaded.collapse_receipt.receipt_id == "receipt:collapse"
    assert loaded.control_session is None


def test_non_genesis_shadow_without_collapse_receipt_fails_closed(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    bad = PersistedConstitutionalState(
        promotion_state=PromotionState(
            mode=SHADOW,
            revision=2,
            last_receipt_id="receipt:missing",
        )
    )

    with pytest.raises(ConstitutionalPersistenceLineageError):
        store.save(bad, written_at=150.0, now=150.0)


def test_snapshot_payload_tampering_is_detected(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    store.save(_advise(), written_at=120.0, now=120.0)

    record = json.loads(store.state_file.read_text(encoding="utf-8"))
    record["payload"]["promotion_state"]["revision"] = 99
    store.state_file.write_text(
        json.dumps(record),
        encoding="utf-8",
    )

    with pytest.raises(ConstitutionalPersistenceIntegrityError):
        store.load(now=120.0)


def test_history_tampering_is_detected(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    store.save(
        PersistedConstitutionalState.genesis(),
        written_at=100.0,
        now=100.0,
    )
    store.save(_advise(), written_at=120.0, now=120.0)

    rows = store.history_file.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["snapshot_hash"] = "0" * 64
    rows[0] = json.dumps(first)
    store.history_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    with pytest.raises(ConstitutionalPersistenceIntegrityError):
        store.load(now=120.0)


def test_missing_history_fails_closed_when_canonical_state_exists(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    store.save(_advise(), written_at=120.0, now=120.0)
    store.history_file.unlink()

    with pytest.raises(ConstitutionalPersistenceIntegrityError):
        store.load(now=120.0)


def test_corrupt_state_json_fails_closed_instead_of_resetting_to_shadow(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    store.root.mkdir(parents=True)
    store.state_file.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ConstitutionalPersistenceIntegrityError):
        store.load(now=120.0)


def test_hash_chain_links_successive_snapshots(tmp_path) -> None:
    store = ConstitutionalStateStore(tmp_path)
    first = store.save(
        PersistedConstitutionalState.genesis(),
        written_at=100.0,
        now=100.0,
    )
    second = store.save(_advise(), written_at=120.0, now=120.0)

    history = store.history()

    assert len(history) == 2
    assert history[0].snapshot_hash == first.snapshot_hash
    assert history[1].snapshot_hash == second.snapshot_hash
    assert history[1].previous_snapshot_hash == history[0].snapshot_hash
