from dataclasses import replace
from types import SimpleNamespace

import pytest

from phikernel.control_witness import (
    ACTION_LIMIT_DENIAL,
    ADVISE,
    BOUNDED_CONTROL,
    CLOCK_LIMIT_DENIAL,
    EXPIRY_DENIAL,
    HAPPY_PATH,
    HUMAN_VETO,
    NOT_READY,
    RECEIPT_GATE,
    RESOURCE_DENIAL,
    ROLLBACK,
    SCOPE_DENIAL,
    TERMINAL_FAILURE,
    READY_FOR_HUMAN_REVIEW,
    ControlActionRequest,
    ControlActionRule,
    ControlContract,
    ControlWitnessCase,
    ControlWitnessError,
    ControlWitnessPolicy,
    REQUIRED_CONTROL_CASE_KINDS,
    apply_human_veto,
    authorize_bounded_control,
    collapse_control_to_shadow,
    evaluate_control_witness,
    license_control_action,
    propose_bounded_control,
    record_control_outcome,
    start_control_session,
    terminate_control_session,
)
from phikernel.mutability import HumanAuthoritySeal
from phikernel.transition import ResourceSpend
from phikernel.warrant import ResourceBudget
from phikernel.witness_bench import PromotionState


class _TestManifest:
    anchor_id = "anchor:test-control"

    def manifest_hash(self):
        return "a" * 64


class _TestAnchor:
    def load_manifest(self):
        return _TestManifest()

    def sign_bytes(self, passphrase, payload):
        return "fixture-signature"

    def verify_bytes(self, payload, signature_b64):
        return SimpleNamespace(
            valid=(signature_b64 == "fixture-signature"),
            reason="fixture signature verification",
        )


_TEST_ANCHOR = _TestAnchor()


def _contract(
    *,
    actor_id="router:crane-fly",
    execute_count=2,
    write_count=1,
    max_total_actions=3,
    max_clock_ticks=20.0,
    lifetime_seconds=100.0,
    compute_limit=100.0,
    token_limit=50.0,
):
    return ControlContract.create(
        actor_id=actor_id,
        action_rules=(
            ControlActionRule(
                rule_id="rule:execute-python",
                operation="execute",
                target="tool/python",
                max_count=execute_count,
            ),
            ControlActionRule(
                rule_id="rule:write-output",
                operation="write",
                target="sandbox/build/output.json",
                max_count=write_count,
            ),
        ),
        resource_limits=(
            ResourceBudget("compute_ms", compute_limit),
            ResourceBudget("tokens", token_limit),
        ),
        max_total_actions=max_total_actions,
        max_clock_ticks=max_clock_ticks,
        lifetime_seconds=lifetime_seconds,
        required_evidence_refs=("evidence:control-reviewed",),
        created_at=100.0,
    )


def _case(contract, kind, i, *, passed=True, replay=True, invariants=True, observable=True, evidence_ref=None, input_value=None):
    return ControlWitnessCase.create(
        contract,
        case_kind=kind,
        input_value={"case": i} if input_value is None else input_value,
        mechanism_enabled_effect={"enabled": kind, "case": i},
        mechanism_removed_effect=(
            {"removed": kind, "case": i}
            if observable
            else {"enabled": kind, "case": i}
        ),
        evidence_ref=evidence_ref or f"control-evidence:{kind}:{i}",
        passed=passed,
        replay_verified=replay,
        invariants_preserved=invariants,
        observed_at=120.0 + i,
    )


def _ready_cases(contract):
    return [
        _case(contract, kind, i)
        for i, kind in enumerate(sorted(REQUIRED_CONTROL_CASE_KINDS), start=1)
    ]


def _ready_report(contract):
    return evaluate_control_witness(
        contract,
        _ready_cases(contract),
        evaluated_at=150.0,
    )


def _advise_state():
    return PromotionState(
        mode=ADVISE,
        revision=1,
        last_receipt_id="receipt:advise",
        authorized_by_seal_id="seal:advise",
    )


def _proposal(contract, report, *, created_at=200.0):
    return propose_bounded_control(
        _advise_state(),
        contract,
        report,
        proposer_id="runtime:control-promotion",
        reason="control witness requirements satisfied",
        created_at=created_at,
    )


def _seal(contract, report, proposal, *, issued_at=210.0, metadata_override=None):
    metadata = {
        "control_promotion_proposal_id": proposal.proposal_id,
        "control_witness_report_hash": report.report_hash,
        "control_contract_hash": contract.contract_hash,
        "target_mode": BOUNDED_CONTROL,
    }
    if metadata_override:
        metadata.update(metadata_override)
    return HumanAuthoritySeal.create_signed(
        anchor_service=_TEST_ANCHOR,
        passphrase="fixture",
        actor_id="human:mikey",
        authority_ref="anchor:human-control",
        issued_at=issued_at,
        metadata=metadata,
    )


def _authorized(contract=None):
    contract = contract or _contract()
    report = _ready_report(contract)
    state = _advise_state()
    proposal = propose_bounded_control(
        state,
        contract,
        report,
        proposer_id="runtime:control-promotion",
        reason="control witness requirements satisfied",
        created_at=200.0,
    )
    seal = _seal(contract, report, proposal, issued_at=210.0)
    bounded, grant, receipt = authorize_bounded_control(
        state,
        proposal,
        contract,
        report,
        seal,
        anchor_service=_TEST_ANCHOR,
        applied_at=220.0,
    )
    return contract, report, bounded, grant, receipt, seal


def _session(contract=None):
    contract, report, bounded, grant, receipt, seal = _authorized(contract)
    session = start_control_session(
        bounded,
        grant,
        contract,
        started_at=221.0,
    )
    return contract, bounded, grant, session


def _request(
    session,
    *,
    operation="execute",
    target="tool/python",
    compute=10.0,
    tokens=0.0,
    ticks=5.0,
    evidence_refs=("evidence:control-reviewed",),
    rollback_ref="rollback:snapshot:1",
    requested_at=222.0,
):
    spends = [ResourceSpend("compute_ms", compute)]
    if tokens:
        spends.append(ResourceSpend("tokens", tokens))
    return ControlActionRequest.create(
        actor_id=session.actor_id,
        operation=operation,
        target=target,
        resource_spends=tuple(spends),
        clock_ticks=ticks,
        evidence_refs=evidence_refs,
        rollback_ref=rollback_ref,
        requested_at=requested_at,
    )


def test_control_contract_uses_exact_scopes() -> None:
    contract = _contract()

    assert contract.scopes == (
        "execute:tool/python",
        "write:sandbox/build/output.json",
    )
    assert contract.rule_for("execute", "tool/python").rule_id == "rule:execute-python"
    assert contract.rule_for("execute", "tool/other") is None


def test_control_rule_rejects_glob_scope_metacharacters() -> None:
    with pytest.raises(ControlWitnessError):
        ControlActionRule(
            rule_id="rule:bad",
            operation="execute",
            target="tool/*",
            max_count=1,
        )


def test_v02_contract_requires_all_safety_controls_enabled() -> None:
    base = _contract()

    with pytest.raises(ControlWitnessError):
        replace(base, human_veto_required=False)

    with pytest.raises(ControlWitnessError):
        replace(base, post_action_receipt_required=False)

    with pytest.raises(ControlWitnessError):
        replace(base, terminal_failure_on_violation=False)


def test_control_resource_limits_must_begin_unspent() -> None:
    with pytest.raises(ControlWitnessError):
        ControlContract.create(
            actor_id="router:crane-fly",
            action_rules=(
                ControlActionRule(
                    rule_id="rule:a",
                    operation="execute",
                    target="tool/python",
                    max_count=1,
                ),
            ),
            resource_limits=(
                ResourceBudget("compute_ms", 100.0, spent=1.0),
            ),
            max_total_actions=1,
            max_clock_ticks=10.0,
            lifetime_seconds=60.0,
        )


def test_complete_control_witness_is_ready_for_human_review_only() -> None:
    contract = _contract()
    report = _ready_report(contract)

    assert report.status == READY_FOR_HUMAN_REVIEW
    assert report.ready_for_human_review is True
    assert report.missing_case_kinds == ()
    assert report.pass_rate == pytest.approx(1.0)
    assert report.replay_verified_rate == pytest.approx(1.0)
    assert report.invariant_preservation_rate == pytest.approx(1.0)
    assert report.behavioral_reality_rate == pytest.approx(1.0)
    assert report.authority_change == "NONE"
    assert report.routing_mode_change == "NONE"
    assert report.steering_authority_change == "NONE"


def test_missing_required_control_case_kind_blocks_readiness() -> None:
    contract = _contract()
    cases = [
        case
        for case in _ready_cases(contract)
        if case.case_kind != HUMAN_VETO
    ]
    policy = ControlWitnessPolicy(
        min_cases=len(cases),
        min_distinct_inputs=len(cases),
    )

    report = evaluate_control_witness(
        contract,
        cases,
        policy=policy,
        evaluated_at=150.0,
    )

    assert report.status == NOT_READY
    assert HUMAN_VETO in report.missing_case_kinds


def test_duplicate_control_evidence_receipt_cannot_inflate_bench() -> None:
    contract = _contract()
    one = _case(contract, HAPPY_PATH, 1, evidence_ref="evidence:same")
    two = _case(contract, SCOPE_DENIAL, 2, evidence_ref="evidence:same")

    with pytest.raises(ControlWitnessError):
        evaluate_control_witness(
            contract,
            [one, two],
            policy=ControlWitnessPolicy(
                min_cases=2,
                min_distinct_inputs=2,
                require_all_case_kinds=False,
            ),
        )


def test_duplicate_control_case_cannot_inflate_bench() -> None:
    contract = _contract()
    case = _case(contract, HAPPY_PATH, 1)

    with pytest.raises(ControlWitnessError):
        evaluate_control_witness(
            contract,
            [case, case],
            policy=ControlWitnessPolicy(
                min_cases=2,
                min_distinct_inputs=1,
                require_all_case_kinds=False,
            ),
        )


def test_control_case_from_other_contract_is_rejected() -> None:
    one = _contract()
    two = _contract(actor_id="router:other")
    foreign = _case(two, HAPPY_PATH, 1)

    with pytest.raises(ControlWitnessError):
        evaluate_control_witness(
            one,
            [foreign],
            policy=ControlWitnessPolicy(
                min_cases=1,
                min_distinct_inputs=1,
                require_all_case_kinds=False,
            ),
        )


def test_failed_control_case_blocks_readiness() -> None:
    contract = _contract()
    cases = _ready_cases(contract)
    cases[0] = replace(cases[0], passed=False)

    report = evaluate_control_witness(
        contract,
        cases,
        evaluated_at=150.0,
    )

    assert report.status == NOT_READY
    assert report.pass_rate < 1.0


def test_decorative_control_mechanism_blocks_readiness() -> None:
    contract = _contract()
    cases = _ready_cases(contract)
    cases[0] = _case(
        contract,
        cases[0].case_kind,
        99,
        observable=False,
    )

    report = evaluate_control_witness(
        contract,
        cases,
        evaluated_at=150.0,
    )

    assert report.status == NOT_READY
    assert report.behavioral_reality_rate < 1.0


def test_replay_or_invariant_failure_blocks_control_readiness() -> None:
    contract = _contract()
    replay_cases = _ready_cases(contract)
    replay_cases[0] = replace(replay_cases[0], replay_verified=False)
    invariant_cases = _ready_cases(contract)
    invariant_cases[0] = replace(
        invariant_cases[0],
        invariants_preserved=False,
    )

    replay_report = evaluate_control_witness(
        contract,
        replay_cases,
        evaluated_at=150.0,
    )
    invariant_report = evaluate_control_witness(
        contract,
        invariant_cases,
        evaluated_at=150.0,
    )

    assert replay_report.status == NOT_READY
    assert invariant_report.status == NOT_READY


def test_repeated_control_input_does_not_satisfy_diversity() -> None:
    contract = _contract()
    cases = [
        _case(contract, kind, i, input_value={"same": True})
        for i, kind in enumerate(sorted(REQUIRED_CONTROL_CASE_KINDS), start=1)
    ]

    report = evaluate_control_witness(
        contract,
        cases,
        evaluated_at=150.0,
    )

    assert report.status == NOT_READY
    assert report.distinct_input_count == 1


def test_bounded_control_proposal_requires_advise_state() -> None:
    contract = _contract()
    report = _ready_report(contract)
    shadow = PromotionState.genesis()

    with pytest.raises(ControlWitnessError):
        propose_bounded_control(
            shadow,
            contract,
            report,
            proposer_id="runtime:control-promotion",
            reason="fixture",
        )


def test_not_ready_control_report_cannot_propose_control() -> None:
    contract = _contract()
    report = evaluate_control_witness(
        contract,
        [_case(contract, HAPPY_PATH, 1)],
        evaluated_at=150.0,
    )

    with pytest.raises(ControlWitnessError):
        propose_bounded_control(
            _advise_state(),
            contract,
            report,
            proposer_id="runtime:control-promotion",
            reason="fixture",
        )


def test_control_proposal_itself_grants_no_authority() -> None:
    contract = _contract()
    report = _ready_report(contract)
    proposal = _proposal(contract, report)

    assert proposal.base_mode == ADVISE
    assert proposal.target_mode == BOUNDED_CONTROL
    assert proposal.routing_mode_change == "PROPOSED_ONLY"
    assert proposal.authority_change == "NONE"
    assert proposal.steering_authority_change == "NONE"


def test_generic_human_seal_cannot_authorize_bounded_control() -> None:
    contract = _contract()
    report = _ready_report(contract)
    state = _advise_state()
    proposal = propose_bounded_control(
        state,
        contract,
        report,
        proposer_id="runtime:control-promotion",
        reason="fixture",
        created_at=200.0,
    )
    generic = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:human-control",
        issued_at=210.0,
    )

    with pytest.raises(ControlWitnessError):
        authorize_bounded_control(
            state,
            proposal,
            contract,
            report,
            generic,
            anchor_service=_TEST_ANCHOR,
        applied_at=220.0,
        )


def test_human_control_seal_must_bind_exact_contract_and_report() -> None:
    contract = _contract()
    report = _ready_report(contract)
    state = _advise_state()
    proposal = propose_bounded_control(
        state,
        contract,
        report,
        proposer_id="runtime:control-promotion",
        reason="fixture",
        created_at=200.0,
    )
    bad = _seal(
        contract,
        report,
        proposal,
        metadata_override={"control_contract_hash": "0" * 64},
    )

    with pytest.raises(ControlWitnessError):
        authorize_bounded_control(
            state,
            proposal,
            contract,
            report,
            bad,
            anchor_service=_TEST_ANCHOR,
        applied_at=220.0,
        )


def test_human_control_seal_may_not_predate_proposal() -> None:
    contract = _contract()
    report = _ready_report(contract)
    state = _advise_state()
    proposal = propose_bounded_control(
        state,
        contract,
        report,
        proposer_id="runtime:control-promotion",
        reason="fixture",
        created_at=200.0,
    )
    seal = _seal(contract, report, proposal, issued_at=190.0)

    with pytest.raises(ControlWitnessError):
        authorize_bounded_control(
            state,
            proposal,
            contract,
            report,
            seal,
            anchor_service=_TEST_ANCHOR,
        applied_at=220.0,
        )


def test_human_authorization_creates_exact_finite_bounded_lease() -> None:
    contract, report, bounded, grant, receipt, seal = _authorized()

    assert bounded.mode == BOUNDED_CONTROL
    assert bounded.may_steer is True
    assert bounded.authorized_by_seal_id == seal.seal_id
    assert grant.contract_hash == contract.contract_hash
    assert grant.warrant.scopes == contract.scopes
    assert {
        budget.kind: budget.limit for budget in grant.warrant.budgets
    } == {
        budget.kind: budget.limit for budget in contract.resource_limits
    }
    assert grant.warrant.expires_at == pytest.approx(
        grant.granted_at + contract.lifetime_seconds
    )
    assert receipt.authority_change == "HUMAN_AUTHORIZED_BOUNDED_LEASE"
    assert receipt.steering_authority_change == "HUMAN_AUTHORIZED_BOUNDED"
    assert receipt.constitutional_change == "NONE"


def test_control_session_requires_bounded_control_mode() -> None:
    contract, report, bounded, grant, receipt, seal = _authorized()

    with pytest.raises(ControlWitnessError):
        start_control_session(
            _advise_state(),
            grant,
            contract,
            started_at=221.0,
        )


def test_control_session_rejects_tampered_grant_warrant_scope() -> None:
    contract, report, bounded, grant, receipt, seal = _authorized()
    tampered_warrant = replace(
        grant.warrant,
        scopes=("execute:tool/other",),
    )
    tampered_grant = replace(grant, warrant=tampered_warrant)

    with pytest.raises(ControlWitnessError):
        start_control_session(
            bounded,
            tampered_grant,
            contract,
            started_at=221.0,
        )


def test_happy_control_action_consumes_bounded_resources_and_waits_for_receipt() -> None:
    contract, bounded, grant, session = _session()
    request = _request(session)

    updated, receipt, evaluation = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    assert receipt.allowed is True
    assert receipt.terminal is False
    assert evaluation.verdict.allowed is True
    assert updated.actions_used == 1
    assert updated.clock_ticks_used == pytest.approx(5.0)
    assert updated.pending_action_id == request.action_id
    assert updated.warrant.remaining("compute_ms") == pytest.approx(90.0)
    assert grant.warrant.remaining("compute_ms") == pytest.approx(100.0)
    assert receipt.authority_change == "NONE"
    assert receipt.steering_authority_change == "NONE"


def test_missing_post_action_receipt_makes_next_action_terminal() -> None:
    contract, bounded, grant, session = _session()
    first = _request(session, rollback_ref="rollback:first")
    pending, first_receipt, _ = license_control_action(
        session,
        contract,
        first,
        now=223.0,
    )
    second = _request(
        pending,
        rollback_ref="rollback:second",
        requested_at=224.0,
    )

    stopped, receipt, evaluation = license_control_action(
        pending,
        contract,
        second,
        now=224.0,
    )

    assert first_receipt.allowed is True
    assert receipt.allowed is False
    assert receipt.terminal is True
    assert "post-action receipt missing" in receipt.reason
    assert stopped.stopped is True
    assert stopped.warrant.revoked is True


def test_success_outcome_receipt_reopens_session_for_next_bounded_action() -> None:
    contract, bounded, grant, session = _session()
    request = _request(session)
    pending, _, _ = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    active, receipt = record_control_outcome(
        pending,
        contract,
        action_id=request.action_id,
        success=True,
        result_ref="result:success:1",
        recorded_at=224.0,
    )

    assert receipt.success is True
    assert receipt.rollback_satisfied is True
    assert receipt.terminal is False
    assert active.stopped is False
    assert active.pending_action_id is None
    assert active.warrant.revoked is False


def test_failed_action_with_verified_rollback_is_still_terminal() -> None:
    contract, bounded, grant, session = _session()
    request = _request(session, rollback_ref="rollback:known-good")
    pending, _, _ = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    stopped, receipt = record_control_outcome(
        pending,
        contract,
        action_id=request.action_id,
        success=False,
        result_ref="result:failed:1",
        rollback_performed=True,
        rollback_ref="rollback:known-good",
        recorded_at=224.0,
    )

    assert receipt.rollback_satisfied is True
    assert receipt.terminal is True
    assert stopped.stopped is True
    assert stopped.warrant.revoked is True


def test_failed_action_without_valid_rollback_is_terminal_and_recorded() -> None:
    contract, bounded, grant, session = _session()
    request = _request(session, rollback_ref="rollback:expected")
    pending, _, _ = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    stopped, receipt = record_control_outcome(
        pending,
        contract,
        action_id=request.action_id,
        success=False,
        result_ref="result:failed:2",
        rollback_performed=True,
        rollback_ref="rollback:wrong",
        recorded_at=224.0,
    )

    assert receipt.rollback_satisfied is False
    assert receipt.terminal is True
    assert stopped.stopped is True
    assert stopped.warrant.revoked is True


def test_out_of_scope_action_is_terminal_not_merely_expensive() -> None:
    contract, bounded, grant, session = _session()
    request = _request(
        session,
        operation="delete",
        target="sandbox/build/output.json",
    )

    stopped, receipt, evaluation = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    assert receipt.allowed is False
    assert receipt.terminal is True
    assert "outside exact contract scope" in receipt.reason
    assert evaluation is None
    assert stopped.stopped is True


def test_missing_required_evidence_is_terminal() -> None:
    contract, bounded, grant, session = _session()
    request = _request(session, evidence_refs=())

    stopped, receipt, _ = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    assert receipt.allowed is False
    assert "missing required evidence" in receipt.reason
    assert stopped.warrant.revoked is True


def test_resource_budget_overrun_is_terminal_via_governed_transition() -> None:
    contract, bounded, grant, session = _session()
    request = _request(session, compute=101.0)

    stopped, receipt, evaluation = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    assert receipt.allowed is False
    assert evaluation is not None
    assert evaluation.verdict.allowed is False
    assert "budget" in receipt.reason
    assert stopped.stopped is True


def test_clock_budget_overrun_is_terminal() -> None:
    contract = _contract(max_clock_ticks=5.0)
    contract, bounded, grant, session = _session(contract)
    request = _request(session, ticks=6.0)

    stopped, receipt, _ = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    assert receipt.allowed is False
    assert "clock budget exhausted" in receipt.reason
    assert stopped.stopped is True


def test_per_rule_action_count_limit_is_terminal() -> None:
    contract = _contract(execute_count=1, max_total_actions=2)
    contract, bounded, grant, session = _session(contract)

    first = _request(session, rollback_ref="rollback:first")
    pending, _, _ = license_control_action(
        session,
        contract,
        first,
        now=223.0,
    )
    active, _ = record_control_outcome(
        pending,
        contract,
        action_id=first.action_id,
        success=True,
        result_ref="result:first",
        recorded_at=224.0,
    )
    second = _request(
        active,
        rollback_ref="rollback:second",
        requested_at=225.0,
    )

    stopped, receipt, _ = license_control_action(
        active,
        contract,
        second,
        now=225.0,
    )

    assert receipt.allowed is False
    assert "count exhausted" in receipt.reason
    assert stopped.stopped is True


def test_total_action_count_limit_is_terminal() -> None:
    contract = _contract(
        execute_count=2,
        write_count=2,
        max_total_actions=1,
    )
    contract, bounded, grant, session = _session(contract)

    first = _request(session)
    pending, _, _ = license_control_action(
        session,
        contract,
        first,
        now=223.0,
    )
    active, _ = record_control_outcome(
        pending,
        contract,
        action_id=first.action_id,
        success=True,
        result_ref="result:first",
        recorded_at=224.0,
    )
    second = _request(
        active,
        operation="write",
        target="sandbox/build/output.json",
        requested_at=225.0,
    )

    stopped, receipt, _ = license_control_action(
        active,
        contract,
        second,
        now=225.0,
    )

    assert receipt.allowed is False
    assert "total action limit exhausted" in receipt.reason
    assert stopped.stopped is True


def test_expired_control_warrant_is_terminal() -> None:
    contract = _contract(lifetime_seconds=2.0)
    contract, bounded, grant, session = _session(contract)
    request = _request(session, requested_at=223.0)

    stopped, receipt, _ = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    assert receipt.allowed is False
    assert "expired" in receipt.reason
    assert stopped.stopped is True


def test_human_veto_stops_session_and_revokes_warrant() -> None:
    contract, bounded, grant, session = _session()
    veto = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:emergency-veto",
        issued_at=223.0,
    )

    stopped, receipt = apply_human_veto(
        session,
        veto,
        reason="operator emergency stop",
        vetoed_at=224.0,
    )

    assert stopped.stopped is True
    assert stopped.warrant.revoked is True
    assert receipt.stop_kind == "VETO_STOP"
    assert receipt.human_seal_id == veto.seal_id
    assert receipt.authority_change == "REDUCED_ONLY"
    assert receipt.steering_authority_change == "REDUCED_ONLY"


def test_veto_during_pending_action_can_close_outcome_without_reviving_session() -> None:
    contract, bounded, grant, session = _session()
    request = _request(session)
    pending, _, _ = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )
    veto = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:emergency-veto",
        issued_at=224.0,
    )
    stopped, _ = apply_human_veto(
        pending,
        veto,
        reason="stop while action pending",
        vetoed_at=225.0,
    )

    closed, outcome = record_control_outcome(
        stopped,
        contract,
        action_id=request.action_id,
        success=True,
        result_ref="result:completed-before-veto",
        recorded_at=226.0,
    )

    assert closed.pending_action_id is None
    assert closed.stopped is True
    assert closed.warrant.revoked is True
    assert outcome.terminal is True


def test_successful_outcome_cannot_falsely_claim_failure_rollback() -> None:
    contract, bounded, grant, session = _session()
    request = _request(session)
    pending, _, _ = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    with pytest.raises(ControlWitnessError):
        record_control_outcome(
            pending,
            contract,
            action_id=request.action_id,
            success=True,
            result_ref="result:success",
            rollback_performed=True,
            rollback_ref=request.rollback_ref,
            recorded_at=224.0,
        )


def test_stopped_control_session_can_collapse_global_privilege_to_shadow() -> None:
    contract, bounded, grant, session = _session()
    stopped, stop_receipt = terminate_control_session(
        session,
        source_ref="runtime:health-check",
        reason="control health degraded",
        stopped_at=223.0,
    )

    collapsed, receipt = collapse_control_to_shadow(
        bounded,
        stopped,
        source_ref=stop_receipt.receipt_id,
        reason="bounded-control session terminated",
        collapsed_at=224.0,
    )

    assert collapsed.mode == "SHADOW"
    assert collapsed.may_steer is False
    assert collapsed.revision == bounded.revision + 1
    assert receipt.steering_authority_change == "NONE"


def test_control_privilege_cannot_collapse_while_session_is_still_active() -> None:
    contract, bounded, grant, session = _session()

    with pytest.raises(ControlWitnessError):
        collapse_control_to_shadow(
            bounded,
            session,
            source_ref="fixture",
            reason="attempted unsafe collapse sequencing",
        )


def test_bounded_control_state_without_matching_grant_cannot_start_session() -> None:
    contract, report, bounded, grant, receipt, seal = _authorized()
    forged_state = PromotionState(
        mode=BOUNDED_CONTROL,
        revision=bounded.revision,
        authorized_by_seal_id="seal:someone-else",
    )

    with pytest.raises(ControlWitnessError):
        start_control_session(
            forged_state,
            grant,
            contract,
            started_at=221.0,
        )


def test_control_action_does_not_expand_original_grant_or_contract() -> None:
    contract, bounded, grant, session = _session()
    original_scopes = grant.warrant.scopes
    original_limits = {
        budget.kind: budget.limit for budget in grant.warrant.budgets
    }
    request = _request(session)

    updated, receipt, evaluation = license_control_action(
        session,
        contract,
        request,
        now=223.0,
    )

    assert updated.warrant.scopes == original_scopes
    assert {
        budget.kind: budget.limit for budget in updated.warrant.budgets
    } == original_limits
    assert contract.scopes == original_scopes
    assert receipt.authority_change == "NONE"
    assert receipt.steering_authority_change == "NONE"
