import pytest

from phikernel.relational_router import (
    HumanRouteConstraint,
    RelationalRoutingRequest,
    RouteCandidate,
)
from phikernel.router import CoachRouter
from phikernel.routing_shadow import (
    AGREE,
    DIVERGE,
    LEGACY_UNMAPPED,
    VNEXT_BLOCKS_LEGACY,
    VNEXT_ERROR,
    VNEXT_NO_ROUTE,
    CoachRouteBinding,
    ShadowRoutingError,
    run_shadow_routing,
)
from phikernel.transition import ResourceSpend
from phikernel.warrant import ResourceBudget, Warrant


def _bundle(
    *,
    prompt: str = "",
    anchor_valid: bool = True,
    field_action: str = "observe",
    field_band: str = "stable",
    heart_running: bool = True,
    latest_capsule: dict | None = None,
) -> dict:
    return {
        "shell_version": "0.1.1",
        "prompt": prompt,
        "anchor": {
            "anchor_id": "anchor-test",
            "sovereign_name": "Tal-Aren-Vox",
            "user_label": "Ori",
            "verification": {
                "valid": anchor_valid,
                "reason": (
                    "Anchor manifest verified successfully"
                    if anchor_valid
                    else "Signature verification failed"
                ),
            },
        },
        "heart": {
            "running": heart_running,
            "registered_jobs": 3,
        },
        "field": {
            "anchor_id": "anchor-test",
            "recommended_action": field_action,
            "drift_band": field_band,
            "C_current": 0.801,
            "distance_to_C_star": 0.008,
        },
        "latest_capsule": latest_capsule,
        "generated_at": 123.456,
        "next_hint": "Field is stable.",
    }


def _warrant(actor_id: str, *, execute: bool = True) -> Warrant:
    return Warrant.issue(
        issuer="human:mikey",
        bearer=actor_id,
        scopes=(
            ("execute:tool/python",)
            if execute
            else ("read:memory/*",)
        ),
        budgets=(ResourceBudget("compute_ms", 100.0),),
        issued_at=100.0,
        lifetime_seconds=1000.0,
    )


def _candidate(
    route_key: str,
    actor_id: str,
    *,
    base_cost: float,
    execute: bool = True,
) -> RouteCandidate:
    return RouteCandidate(
        route_key=route_key,
        actor_id=actor_id,
        operation="execute",
        target="tool/python",
        warrant=_warrant(actor_id, execute=execute),
        base_cost=base_cost,
        capability_tags=("coach",),
        resource_spends=(ResourceSpend("compute_ms", 1.0),),
    )


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


def test_shadow_agreement_preserves_legacy_reply() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    direct = CoachRouter().route(bundle)

    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate("route:titan", "node:titan", base_cost=3.0),
            _candidate("route:flow", "node:flow", base_cost=1.0),
            _candidate("route:sage", "node:sage", base_cost=2.0),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert direct.coach == "Flow"
    assert result.legacy_reply.coach == direct.coach
    assert result.legacy_reply.route_reason == direct.route_reason
    assert result.legacy_reply.safe_to_proceed == direct.safe_to_proceed
    assert result.legacy_reply.field_action == direct.field_action
    assert result.legacy_reply.field_band == direct.field_band
    assert result.comparison.comparison_state == AGREE
    assert result.comparison.legacy_route_key == "route:flow"
    assert result.comparison.vnext_selected_route_key == "route:flow"
    assert result.comparison.legacy_authoritative is True
    assert result.comparison.shadow_steering_authority == "NONE"


def test_shadow_divergence_does_not_change_legacy_selection() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )

    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate("route:titan", "node:titan", base_cost=3.0),
            _candidate("route:flow", "node:flow", base_cost=2.0),
            _candidate("route:sage", "node:sage", base_cost=0.5),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.legacy_reply.coach == "Flow"
    assert result.comparison.comparison_state == DIVERGE
    assert result.comparison.vnext_selected_route_key == "route:sage"
    assert result.comparison.legacy_authoritative is True


def test_vnext_hard_block_of_legacy_route_is_recorded_not_enforced() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )

    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate("route:titan", "node:titan", base_cost=3.0),
            _candidate(
                "route:flow",
                "node:flow",
                base_cost=0.0,
                execute=False,
            ),
            _candidate("route:sage", "node:sage", base_cost=1.0),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.legacy_reply.coach == "Flow"
    assert result.comparison.comparison_state == VNEXT_BLOCKS_LEGACY
    assert result.comparison.legacy_candidate_admissible_in_vnext is False
    assert any(
        "warrant denied" in reason
        for reason in result.comparison.legacy_candidate_block_reasons
    )
    assert result.comparison.vnext_selected_route_key == "route:sage"
    assert result.comparison.shadow_steering_authority == "NONE"


def test_human_block_of_legacy_route_remains_shadow_only() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    block = HumanRouteConstraint.block(
        route_key="route:flow",
        authority_ref="human-seal:test",
        reason="shadow experiment block",
        created_at=100.0,
    )

    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate("route:flow", "node:flow", base_cost=0.0),
            _candidate("route:sage", "node:sage", base_cost=1.0),
        ),
        bindings=_bindings(),
        human_constraints=(block,),
        now=120.0,
    )

    assert result.legacy_reply.coach == "Flow"
    assert result.comparison.comparison_state == VNEXT_BLOCKS_LEGACY
    assert result.comparison.legacy_authoritative is True


def test_vnext_no_route_does_not_delete_legacy_reply() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )

    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate(
                "route:flow",
                "node:flow",
                base_cost=0.0,
                execute=False,
            ),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.legacy_reply.coach == "Flow"
    assert result.comparison.comparison_state == VNEXT_BLOCKS_LEGACY
    assert result.comparison.vnext_selected_route_key is None


def test_vnext_no_route_when_legacy_mapping_is_not_in_candidate_set() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )

    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate(
                "route:sage",
                "node:sage",
                base_cost=0.0,
                execute=False,
            ),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.legacy_reply.coach == "Flow"
    assert result.comparison.comparison_state == VNEXT_NO_ROUTE
    assert result.comparison.legacy_route_key == "route:flow"
    assert result.comparison.legacy_candidate_admissible_in_vnext is None
    assert result.comparison.vnext_selected_route_key is None


def test_explicitly_unmapped_legacy_coach_is_observable() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    bindings = (
        CoachRouteBinding("Titan", "route:titan"),
        CoachRouteBinding("Sage", "route:sage"),
    )

    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate("route:sage", "node:sage", base_cost=1.0),
        ),
        bindings=bindings,
        now=120.0,
    )

    assert result.legacy_reply.coach == "Flow"
    assert result.comparison.comparison_state == LEGACY_UNMAPPED
    assert result.comparison.legacy_route_key is None


def test_vnext_error_is_fail_isolated_from_legacy_router() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    direct = CoachRouter().route(bundle)

    # Duplicate route keys intentionally cause relational routing to reject
    # the candidate set after the legacy decision has already been produced.
    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate("route:duplicate", "node:a", base_cost=1.0),
            _candidate("route:duplicate", "node:b", base_cost=2.0),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.legacy_reply.coach == direct.coach
    assert result.legacy_reply.route_reason == direct.route_reason
    assert result.legacy_reply.safe_to_proceed == direct.safe_to_proceed
    assert result.legacy_reply.field_action == direct.field_action
    assert result.legacy_reply.field_band == direct.field_band
    assert result.comparison.comparison_state == VNEXT_ERROR
    assert result.vnext_receipt is None
    assert "RelationalRoutingError" in result.comparison.vnext_error
    assert result.comparison.legacy_authoritative is True


def test_unsafe_legacy_reply_remains_unsafe_even_if_vnext_prefers_route() -> None:
    bundle = _bundle(
        prompt="I need momentum",
        anchor_valid=False,
        latest_capsule={"capsule_id": "cap-1"},
    )
    direct = CoachRouter().route(bundle)

    result = run_shadow_routing(
        bundle,
        _request(),
        (
            _candidate("route:titan", "node:titan", base_cost=5.0),
            _candidate("route:flow", "node:flow", base_cost=0.0),
        ),
        bindings=_bindings(),
        now=120.0,
    )

    assert direct.coach == "Titan"
    assert direct.safe_to_proceed is False
    assert result.legacy_reply.coach == direct.coach
    assert result.legacy_reply.route_reason == direct.route_reason
    assert result.legacy_reply.safe_to_proceed == direct.safe_to_proceed
    assert result.legacy_reply.field_action == direct.field_action
    assert result.legacy_reply.field_band == direct.field_band
    assert result.legacy_reply.safe_to_proceed is False
    assert result.comparison.legacy_safe_to_proceed is False


def test_shadow_router_does_not_spend_candidate_warrant() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    warrant = _warrant("node:flow")
    candidate = RouteCandidate(
        route_key="route:flow",
        actor_id="node:flow",
        operation="execute",
        target="tool/python",
        warrant=warrant,
        base_cost=1.0,
        capability_tags=("coach",),
        resource_spends=(ResourceSpend("compute_ms", 50.0),),
    )

    result = run_shadow_routing(
        bundle,
        _request(),
        (candidate,),
        bindings=_bindings(),
        now=120.0,
    )

    assert result.legacy_reply.coach == "Flow"
    assert warrant.remaining("compute_ms") == pytest.approx(100.0)
    assert result.comparison.warrant_change == "NONE"


def test_shadow_receipt_cannot_mutate_governance_surfaces() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )

    result = run_shadow_routing(
        bundle,
        _request(),
        (_candidate("route:flow", "node:flow", base_cost=1.0),),
        bindings=_bindings(),
        now=120.0,
    )

    receipt = result.comparison
    assert receipt.shadow_steering_authority == "NONE"
    assert receipt.authority_change == "NONE"
    assert receipt.warrant_change == "NONE"
    assert receipt.citation_law_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_bundle_hash_is_stable_for_equivalent_dictionary_order() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    reordered = dict(reversed(list(bundle.items())))

    one = run_shadow_routing(
        bundle,
        _request(),
        (_candidate("route:flow", "node:flow", base_cost=1.0),),
        bindings=_bindings(),
        now=120.0,
    )
    two = run_shadow_routing(
        reordered,
        _request(),
        (_candidate("route:flow", "node:flow", base_cost=1.0),),
        bindings=_bindings(),
        now=120.0,
    )

    assert one.comparison.think_bundle_hash == two.comparison.think_bundle_hash


def test_bundle_hash_changes_when_authoritative_input_changes() -> None:
    one_bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    two_bundle = _bundle(
        prompt="Can you help me reflect on this pattern?",
        latest_capsule={"capsule_id": "cap-1"},
    )

    one = run_shadow_routing(
        one_bundle,
        _request(),
        (_candidate("route:flow", "node:flow", base_cost=1.0),),
        bindings=_bindings(),
        now=120.0,
    )
    two = run_shadow_routing(
        two_bundle,
        _request(),
        (_candidate("route:sage", "node:sage", base_cost=1.0),),
        bindings=_bindings(),
        now=120.0,
    )

    assert one.comparison.think_bundle_hash != two.comparison.think_bundle_hash


def test_duplicate_coach_binding_is_rejected_before_shadow_run() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    bindings = (
        CoachRouteBinding("Flow", "route:flow-a"),
        CoachRouteBinding("Flow", "route:flow-b"),
    )

    with pytest.raises(ShadowRoutingError):
        run_shadow_routing(
            bundle,
            _request(),
            (_candidate("route:flow-a", "node:flow", base_cost=1.0),),
            bindings=bindings,
            now=120.0,
        )


def test_duplicate_route_binding_is_rejected_before_shadow_run() -> None:
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        latest_capsule={"capsule_id": "cap-1"},
    )
    bindings = (
        CoachRouteBinding("Flow", "route:shared"),
        CoachRouteBinding("Sage", "route:shared"),
    )

    with pytest.raises(ShadowRoutingError):
        run_shadow_routing(
            bundle,
            _request(),
            (_candidate("route:shared", "node:shared", base_cost=1.0),),
            bindings=bindings,
            now=120.0,
        )
