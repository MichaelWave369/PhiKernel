import pytest

from phikernel.constitutional_runtime import (
    BOUNDED_LICENSED,
    BOUNDED_REFUSED,
    BOUNDED_REVIEW_HOLD,
    BOUNDED_UNAVAILABLE,
    LEGACY_SHADOW,
    LEGACY_WITH_ADVICE,
    RUNTIME_BLOCKED,
    orchestrate_runtime,
)
from phikernel.control_state import RuntimeControlState
from phikernel.control_witness import (
    ActionUsage,
    ControlActionRequest,
    ControlActionRule,
    ControlContract,
    ControlSession,
)
from phikernel.relational_router import (
    RelationalRoutingRequest,
    RouteCandidate,
)
from phikernel.routing_shadow import CoachRouteBinding
from phikernel.transition import ResourceSpend
from phikernel.warrant import ResourceBudget, Warrant
from phikernel.witness_bench import (
    ADVISE,
    BOUNDED_CONTROL,
    SHADOW,
    PromotionState,
)


def _bundle(prompt="I need momentum to create and start this draft"):
    return {
        "shell_version": "0.1.1",
        "prompt": prompt,
        "anchor": {
            "anchor_id": "anchor-test",
            "sovereign_name": "Tal-Aren-Vox",
            "user_label": "Ori",
            "verification": {
                "valid": True,
                "reason": "Anchor manifest verified successfully",
            },
        },
        "heart": {"running": True, "registered_jobs": 3},
        "field": {
            "anchor_id": "anchor-test",
            "recommended_action": "observe",
            "drift_band": "stable",
            "C_current": 0.801,
            "distance_to_C_star": 0.008,
        },
        "latest_capsule": {"capsule_id": "cap-1"},
        "generated_at": 123.456,
        "next_hint": "Field is stable.",
    }


def _bindings():
    return (
        CoachRouteBinding("Titan", "route:titan"),
        CoachRouteBinding("Flow", "route:flow"),
        CoachRouteBinding("Sage", "route:sage"),
    )


def _request():
    return RelationalRoutingRequest.create(
        required_capabilities=("coach",),
        requested_at=120.0,
    )


def _ordinary_warrant(actor_id):
    return Warrant.issue(
        issuer="human:mikey",
        bearer=actor_id,
        scopes=("execute:tool/python",),
        budgets=(ResourceBudget("compute_ms", 100.0),),
        issued_at=100.0,
        lifetime_seconds=1000.0,
    )


def _candidate(
    route_key,
    actor_id,
    *,
    base_cost=1.0,
    warrant=None,
    operation="execute",
    target="tool/python",
    capabilities=("coach",),
):
    return RouteCandidate(
        route_key=route_key,
        actor_id=actor_id,
        operation=operation,
        target=target,
        warrant=warrant or _ordinary_warrant(actor_id),
        base_cost=base_cost,
        capability_tags=tuple(capabilities),
        resource_spends=(ResourceSpend("compute_ms", 1.0),),
    )


def _control_contract():
    return ControlContract.create(
        actor_id="node:flow",
        action_rules=(
            ControlActionRule(
                rule_id="rule:execute-python",
                operation="execute",
                target="tool/python",
                max_count=2,
            ),
        ),
        resource_limits=(ResourceBudget("compute_ms", 100.0),),
        max_total_actions=2,
        max_clock_ticks=20.0,
        lifetime_seconds=100.0,
        required_evidence_refs=("evidence:approved",),
        created_at=100.0,
    )


def _control_session(contract=None, *, warrant=None):
    contract = contract or _control_contract()
    warrant = warrant or Warrant.issue(
        issuer="human:mikey",
        bearer=contract.actor_id,
        scopes=contract.scopes,
        budgets=tuple(
            ResourceBudget(b.kind, b.limit) for b in contract.resource_limits
        ),
        issued_at=110.0,
        lifetime_seconds=100.0,
        metadata={"control_contract_hash": contract.contract_hash},
    )
    session = ControlSession(
        session_id="control-session:1",
        grant_id="control-grant:1",
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        started_at=110.0,
        actions_used=0,
        clock_ticks_used=0.0,
        action_usage=tuple(
            ActionUsage(rule_id=rule.rule_id, count=0)
            for rule in contract.action_rules
        ),
    )
    return contract, session


def _control_action(
    *,
    actor_id="node:flow",
    operation="execute",
    target="tool/python",
    compute=10.0,
    ticks=5.0,
    evidence=("evidence:approved",),
):
    return ControlActionRequest.create(
        actor_id=actor_id,
        operation=operation,
        target=target,
        resource_spends=(ResourceSpend("compute_ms", compute),),
        clock_ticks=ticks,
        evidence_refs=evidence,
        rollback_ref="rollback:known-good",
        requested_at=120.0,
    )


def _shadow_state():
    return PromotionState.genesis()


def _advise_state():
    return PromotionState(
        mode=ADVISE,
        revision=1,
        authorized_by_seal_id="seal:advise",
    )


def _bounded_state():
    return PromotionState(
        mode=BOUNDED_CONTROL,
        revision=2,
        authorized_by_seal_id="seal:control",
    )


def test_shadow_mode_keeps_legacy_authority_and_hides_advisory_route() -> None:
    result = orchestrate_runtime(
        _bundle(),
        _shadow_state(),
        _request(),
        (
            _candidate("route:flow", "node:flow", base_cost=2.0),
            _candidate("route:sage", "node:sage", base_cost=0.5),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.disposition == LEGACY_SHADOW
    assert result.legacy_reply.coach == "Flow"
    assert result.legacy_route_key == "route:flow"
    assert result.vnext_receipt.selected_route_key == "route:sage"
    assert result.advisory_route_key is None
    assert result.steering_route_key is None
    assert result.execution_licensed is False
    assert result.legacy_response_authoritative is True
    assert result.steering_authority_active is False


def test_advise_mode_surfaces_vnext_recommendation_without_steering() -> None:
    result = orchestrate_runtime(
        _bundle(),
        _advise_state(),
        _request(),
        (
            _candidate("route:flow", "node:flow", base_cost=2.0),
            _candidate("route:sage", "node:sage", base_cost=0.5),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.disposition == LEGACY_WITH_ADVICE
    assert result.legacy_reply.coach == "Flow"
    assert result.legacy_route_key == "route:flow"
    assert result.advisory_route_key == "route:sage"
    assert result.steering_route_key is None
    assert result.execution_licensed is False
    assert result.mode_after == ADVISE


def test_advise_mode_ignores_control_artifacts_for_steering() -> None:
    contract, session = _control_session()
    action = _control_action()
    candidate = _candidate(
        "route:flow",
        "node:flow",
        warrant=session.warrant,
    )

    result = orchestrate_runtime(
        _bundle(),
        _advise_state(),
        _request(),
        (candidate,),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == LEGACY_WITH_ADVICE
    assert result.execution_licensed is False
    assert result.control_license_receipt is None
    assert session.warrant.remaining("compute_ms") == pytest.approx(100.0)


def test_bounded_control_without_contract_session_or_action_fails_closed() -> None:
    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (_candidate("route:flow", "node:flow"),),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.disposition == BOUNDED_UNAVAILABLE
    assert result.execution_licensed is False
    assert result.steering_route_key is None
    assert result.mode_after == BOUNDED_CONTROL


def test_bounded_control_licenses_exact_vnext_selected_action() -> None:
    contract, session = _control_session()
    action = _control_action()
    flow = _candidate(
        "route:flow",
        "node:flow",
        base_cost=0.5,
        warrant=session.warrant,
    )
    sage = _candidate("route:sage", "node:sage", base_cost=3.0)

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (flow, sage),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == BOUNDED_LICENSED
    assert result.execution_licensed is True
    assert result.steering_route_key == "route:flow"
    assert result.advisory_route_key == "route:flow"
    assert result.control_license_receipt.allowed is True
    assert result.control_session_after.pending_action_id == action.action_id
    assert result.control_session_after.actions_used == 1
    assert result.control_session_after.clock_ticks_used == pytest.approx(5.0)
    assert result.control_session_after.warrant.remaining("compute_ms") == pytest.approx(90.0)
    assert session.warrant.remaining("compute_ms") == pytest.approx(100.0)
    assert result.mode_after == BOUNDED_CONTROL
    assert result.steering_authority_active is True


def test_bounded_control_requires_selected_route_actor_to_match_control_actor() -> None:
    contract, session = _control_session()
    action = _control_action()
    # vNext selects Sage, but the bounded control session belongs to node:flow.
    sage = _candidate("route:sage", "node:sage", base_cost=0.0)
    flow = _candidate(
        "route:flow",
        "node:flow",
        base_cost=5.0,
        warrant=session.warrant,
    )

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (sage, flow),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == BOUNDED_REFUSED
    assert result.execution_licensed is False
    assert result.mode_after == SHADOW
    assert result.promotion_state_after.mode == SHADOW
    assert result.control_session_after.stopped is True
    assert result.control_session_after.warrant.revoked is True
    assert "actor" in result.reason


def test_bounded_control_requires_selected_route_operation_and_target_match() -> None:
    contract, session = _control_session()
    action = _control_action()
    selected = _candidate(
        "route:flow",
        "node:flow",
        base_cost=0.0,
        warrant=session.warrant,
        operation="write",
        target="tool/python",
    )

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (selected,),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == BOUNDED_REFUSED
    assert result.mode_after == SHADOW
    assert result.execution_licensed is False
    assert "operation" in result.reason


def test_bounded_control_requires_relational_route_to_use_control_session_warrant() -> None:
    contract, session = _control_session()
    action = _control_action()
    selected = _candidate(
        "route:flow",
        "node:flow",
        base_cost=0.0,
        warrant=_ordinary_warrant("node:flow"),
    )

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (selected,),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == BOUNDED_REFUSED
    assert result.mode_after == SHADOW
    assert result.control_session_after.stopped is True
    assert "dedicated bounded-control" in result.reason


def test_control_license_refusal_collapses_to_shadow_without_legacy_fallback() -> None:
    contract, session = _control_session()
    action = _control_action(compute=101.0)
    selected = _candidate(
        "route:flow",
        "node:flow",
        base_cost=0.0,
        warrant=session.warrant,
    )

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (selected,),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == BOUNDED_REFUSED
    assert result.control_license_receipt.allowed is False
    assert result.control_license_receipt.terminal is True
    assert result.mode_after == SHADOW
    assert result.steering_route_key is None
    assert result.execution_licensed is False
    assert result.legacy_reply.coach == "Flow"
    assert result.legacy_response_authoritative is False
    assert result.control_session_after.warrant.revoked is True


def test_no_admissible_vnext_route_is_terminal_in_bounded_control() -> None:
    contract, session = _control_session()
    action = _control_action()
    denied = Warrant.issue(
        issuer="human:mikey",
        bearer="node:flow",
        scopes=("read:memory/*",),
        issued_at=110.0,
        lifetime_seconds=100.0,
    )
    candidate = _candidate(
        "route:flow",
        "node:flow",
        base_cost=0.0,
        warrant=denied,
    )

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (candidate,),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == BOUNDED_REFUSED
    assert result.vnext_receipt.selected_route_key is None
    assert result.mode_after == SHADOW
    assert result.control_session_after.stopped is True


def test_operator_review_holds_bounded_control_without_consuming_warrant() -> None:
    contract, session = _control_session()
    action = _control_action()
    candidate = _candidate(
        "route:flow",
        "node:flow",
        warrant=session.warrant,
    )

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (candidate,),
        bindings=_bindings(),
        runtime_control_state=RuntimeControlState(review_required=True),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == BOUNDED_REVIEW_HOLD
    assert result.mode_after == BOUNDED_CONTROL
    assert result.execution_licensed is False
    assert result.control_license_receipt is None
    assert result.control_session_after is session
    assert session.warrant.remaining("compute_ms") == pytest.approx(100.0)


def test_recovery_required_holds_bounded_control() -> None:
    contract, session = _control_session()
    candidate = _candidate(
        "route:flow",
        "node:flow",
        warrant=session.warrant,
    )

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (candidate,),
        bindings=_bindings(),
        runtime_control_state=RuntimeControlState(recovery_required=True),
        control_contract=contract,
        control_session=session,
        control_action=_control_action(),
        now=120.0,
    )

    assert result.disposition == BOUNDED_REVIEW_HOLD
    assert result.execution_licensed is False


def test_quarantine_overrides_routing_and_collapses_active_bounded_control() -> None:
    contract, session = _control_session()

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (),
        bindings=_bindings(),
        runtime_control_state=RuntimeControlState(quarantined=True),
        control_contract=contract,
        control_session=session,
        control_action=_control_action(),
        now=120.0,
    )

    assert result.disposition == RUNTIME_BLOCKED
    assert result.legacy_reply is None
    assert result.vnext_receipt is None
    assert result.mode_after == SHADOW
    assert result.control_session_after.stopped is True
    assert result.control_session_after.warrant.revoked is True
    assert result.control_stop_receipt is not None
    assert result.collapse_receipt is not None


def test_seal_collapses_bounded_state_even_when_session_object_is_missing() -> None:
    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (),
        bindings=_bindings(),
        runtime_control_state=RuntimeControlState(sealed=True),
        now=120.0,
    )

    assert result.disposition == RUNTIME_BLOCKED
    assert result.mode_after == SHADOW
    assert result.control_session_after is None
    assert result.collapse_receipt is not None


def test_quarantine_blocks_shadow_mode_without_promoting_or_collapsing_anything() -> None:
    state = _shadow_state()
    result = orchestrate_runtime(
        _bundle(),
        state,
        _request(),
        (),
        bindings=_bindings(),
        runtime_control_state=RuntimeControlState(quarantined=True),
        now=120.0,
    )

    assert result.disposition == RUNTIME_BLOCKED
    assert result.mode_before == SHADOW
    assert result.mode_after == SHADOW
    assert result.promotion_state_after == state
    assert result.execution_licensed is False


def test_bounded_control_does_not_license_action_if_relational_router_errors() -> None:
    contract, session = _control_session()
    action = _control_action()
    duplicate_a = _candidate(
        "route:duplicate",
        "node:flow",
        base_cost=0.0,
        warrant=session.warrant,
    )
    duplicate_b = _candidate(
        "route:duplicate",
        "node:other",
        base_cost=1.0,
    )

    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (duplicate_a, duplicate_b),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=action,
        now=120.0,
    )

    assert result.disposition == BOUNDED_REFUSED
    assert result.vnext_receipt is None
    assert result.control_license_receipt is None
    assert result.mode_after == SHADOW
    assert result.control_session_after.stopped is True


def test_orchestrator_never_creates_authority_or_constitution_changes() -> None:
    contract, session = _control_session()
    result = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (
            _candidate(
                "route:flow",
                "node:flow",
                warrant=session.warrant,
            ),
        ),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=_control_action(),
        now=120.0,
    )

    assert result.authority_change == "NONE"
    assert result.constitutional_change == "NONE"
    assert result.control_license_receipt.authority_change == "NONE"
    assert result.control_license_receipt.constitutional_change == "NONE"


def test_to_record_distinguishes_advice_from_licensed_steering() -> None:
    advise = orchestrate_runtime(
        _bundle(),
        _advise_state(),
        _request(),
        (
            _candidate("route:flow", "node:flow", base_cost=2.0),
            _candidate("route:sage", "node:sage", base_cost=0.5),
        ),
        bindings=_bindings(),
        now=120.0,
    ).to_record()

    contract, session = _control_session()
    bounded = orchestrate_runtime(
        _bundle(),
        _bounded_state(),
        _request(),
        (
            _candidate(
                "route:flow",
                "node:flow",
                warrant=session.warrant,
            ),
        ),
        bindings=_bindings(),
        control_contract=contract,
        control_session=session,
        control_action=_control_action(),
        now=120.0,
    ).to_record()

    assert advise["advisory_route_key"] == "route:sage"
    assert advise["steering_route_key"] is None
    assert advise["execution_licensed"] is False
    assert bounded["steering_route_key"] == "route:flow"
    assert bounded["execution_licensed"] is True
    assert bounded["steering_authority_active"] is True
