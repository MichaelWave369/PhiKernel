from dataclasses import replace

import pytest

from phikernel.carriage import CitationDecision
from phikernel.clockskin import (
    WALL_RATE,
    ClockSkinContract,
    issue_clock_lease,
)
from phikernel.coalition import (
    CoalitionCharter,
    MemberCapability,
    activate_coalition,
    degrade_coalition,
)
from phikernel.contradiction import ContradictionObject
from phikernel.mutability import RoutingWeatherState, apply_routing_weather_update
from phikernel.premise import (
    ACTIVE as PREMISE_ACTIVE,
    CHALLENGED as PREMISE_CHALLENGED,
    QUARANTINED as PREMISE_QUARANTINED,
    PremiseObject,
)
from phikernel.relational_router import (
    BLOCK,
    PENALTY,
    HumanRouteConstraint,
    RelationalRoutingPolicy,
    RelationalRoutingRequest,
    RouteCandidate,
    route_relational,
)
from phikernel.saturation import SuccessObservation
from phikernel.scar import REFUSED, FailureScar
from phikernel.transition import ResourceSpend
from phikernel.warrant import ResourceBudget, Warrant


def _warrant(
    actor_id: str,
    *,
    compute_limit: float = 100.0,
    token_limit: float = 1000.0,
    issued_at: float = 100.0,
    lifetime: float = 1000.0,
):
    return Warrant.issue(
        issuer="human:mikey",
        bearer=actor_id,
        scopes=("execute:tool/python",),
        budgets=(
            ResourceBudget("compute_ms", compute_limit),
            ResourceBudget("tokens", token_limit),
        ),
        issued_at=issued_at,
        lifetime_seconds=lifetime,
    )


def _candidate(
    route_key: str,
    actor_id: str,
    *,
    base_cost: float = 1.0,
    warrant: Warrant | None = None,
    capabilities=("code",),
    resource_spends=(),
    citation_decisions=(),
    contradictions=(),
    premises=(),
    weather_keys=(),
    clock_lease=None,
    expected_ticks=0.0,
    coalition=None,
):
    return RouteCandidate(
        route_key=route_key,
        actor_id=actor_id,
        operation="execute",
        target="tool/python",
        warrant=warrant or _warrant(actor_id),
        base_cost=base_cost,
        capability_tags=tuple(capabilities),
        resource_spends=tuple(resource_spends),
        citation_decisions=tuple(citation_decisions),
        contradictions=tuple(contradictions),
        premises=tuple(premises),
        weather_keys=tuple(weather_keys),
        clock_lease=clock_lease,
        expected_ticks=expected_ticks,
        coalition=coalition,
    )


def _request(*capabilities, requested_at=120.0):
    return RelationalRoutingRequest.create(
        required_capabilities=capabilities,
        requested_at=requested_at,
    )


def _premise(route_key: str, state: str):
    base = PremiseObject.create(
        statement=f"Premise governing {route_key}",
        claim_key=f"premise:{route_key}",
        dependent_route_keys=(route_key,),
        created_at=100.0,
    )
    if state == PREMISE_ACTIVE:
        return base
    return replace(
        base,
        state=state,
        state_reason=f"fixture {state}",
        state_changed_at=110.0,
    )


def _coalition(*, created_at=100.0):
    members = (
        MemberCapability(
            member_id="member:planner",
            capability_tags=("plan",),
            source_ref="capability:planner",
        ),
        MemberCapability(
            member_id="member:coder",
            capability_tags=("code",),
            source_ref="capability:coder",
        ),
        MemberCapability(
            member_id="member:tester",
            capability_tags=("test",),
            source_ref="capability:tester",
        ),
    )
    draft = CoalitionCharter.draft(
        purpose="bounded build coalition",
        members=members,
        lifetime_seconds=100.0,
        created_at=created_at,
    )
    return activate_coalition(draft, activated_at=created_at + 1.0)[0]


def _evaluation(receipt, route_key):
    return next(item for item in receipt.evaluations if item.route_key == route_key)


def test_lower_cost_admissible_route_wins() -> None:
    request = _request("code")
    a = _candidate("route:a", "node:a", base_cost=1.0)
    b = _candidate("route:b", "node:b", base_cost=2.0)

    receipt = route_relational(request, [b, a], now=120.0)

    assert receipt.selected_route_key == "route:a"
    assert _evaluation(receipt, "route:a").admissible is True
    assert _evaluation(receipt, "route:a").conductance > _evaluation(
        receipt, "route:b"
    ).conductance


def test_warrant_denial_is_hard_block_even_if_route_is_cheapest() -> None:
    request = _request("code")
    denied_warrant = Warrant.issue(
        issuer="human:mikey",
        bearer="node:cheap",
        scopes=("read:memory/*",),
        issued_at=100.0,
        lifetime_seconds=1000.0,
    )
    cheap = _candidate(
        "route:cheap",
        "node:cheap",
        base_cost=0.0,
        warrant=denied_warrant,
    )
    costly = _candidate("route:costly", "node:costly", base_cost=5.0)

    receipt = route_relational(request, [cheap, costly], now=120.0)

    blocked = _evaluation(receipt, "route:cheap")
    assert blocked.admissible is False
    assert blocked.total_cost is None
    assert blocked.conductance == 0.0
    assert any("warrant denied" in reason for reason in blocked.hard_block_reasons)
    assert receipt.selected_route_key == "route:costly"


def test_missing_capability_is_hard_block_not_score_penalty() -> None:
    request = _request("code", "test")
    weak = _candidate(
        "route:weak",
        "node:weak",
        base_cost=0.0,
        capabilities=("code",),
    )
    capable = _candidate(
        "route:capable",
        "node:capable",
        base_cost=10.0,
        capabilities=("code", "test"),
    )

    receipt = route_relational(request, [weak, capable], now=120.0)

    assert _evaluation(receipt, "route:weak").admissible is False
    assert receipt.selected_route_key == "route:capable"


def test_blocked_citation_is_hard_block() -> None:
    request = _request("code")
    citation = CitationDecision(
        carriage_id="carriage:bad",
        allowed=False,
        reason="citation blocked by open contradiction",
        blocking_contradiction_ids=("contradiction:1",),
        evaluated_at=119.0,
    )
    bad = _candidate(
        "route:bad-citation",
        "node:a",
        base_cost=0.0,
        citation_decisions=(citation,),
    )
    good = _candidate("route:good", "node:b", base_cost=3.0)

    receipt = route_relational(request, [bad, good], now=120.0)

    assert _evaluation(receipt, "route:bad-citation").admissible is False
    assert receipt.selected_route_key == "route:good"


def test_open_contradiction_is_hard_block() -> None:
    request = _request("code")
    contradiction = ContradictionObject.open(
        claim_key="claim:x",
        exhibit_carriage_ids=("carriage:a", "carriage:b"),
        reason="incompatible evidence",
        opened_at=110.0,
    )
    blocked = _candidate(
        "route:contradicted",
        "node:a",
        base_cost=0.0,
        contradictions=(contradiction,),
    )
    safe = _candidate("route:safe", "node:b", base_cost=4.0)

    receipt = route_relational(request, [blocked, safe], now=120.0)

    assert _evaluation(receipt, "route:contradicted").admissible is False
    assert receipt.selected_route_key == "route:safe"


def test_challenged_premise_adds_cost_but_does_not_block() -> None:
    request = _request("code")
    challenged = _premise("route:a", PREMISE_CHALLENGED)
    a = _candidate(
        "route:a",
        "node:a",
        base_cost=1.0,
        premises=(challenged,),
    )
    b = _candidate("route:b", "node:b", base_cost=1.2)

    receipt = route_relational(request, [a, b], now=120.0)

    eval_a = _evaluation(receipt, "route:a")
    assert eval_a.admissible is True
    assert eval_a.premise_penalty == pytest.approx(0.5)
    assert receipt.selected_route_key == "route:b"


def test_quarantined_premise_hard_blocks_route() -> None:
    request = _request("code")
    premise = _premise("route:a", PREMISE_QUARANTINED)
    a = _candidate(
        "route:a",
        "node:a",
        base_cost=0.0,
        premises=(premise,),
    )
    b = _candidate("route:b", "node:b", base_cost=9.0)

    receipt = route_relational(request, [a, b], now=120.0)

    assert _evaluation(receipt, "route:a").admissible is False
    assert receipt.selected_route_key == "route:b"


def test_failure_scar_changes_selection_without_changing_authority() -> None:
    request = _request("code")
    a = _candidate("route:a", "node:a", base_cost=1.0)
    b = _candidate("route:b", "node:b", base_cost=1.2)
    scar = FailureScar.create(
        subject_id="node:a",
        route_key="route:a",
        failure_kind=REFUSED,
        severity=0.8,
        source_ref="transition:failed",
        observed_at=120.0,
        half_life_seconds=1000.0,
    )

    receipt = route_relational(
        request,
        [a, b],
        scars=(scar,),
        now=120.0,
    )

    eval_a = _evaluation(receipt, "route:a")
    assert eval_a.scar_penalty == pytest.approx(0.8)
    assert eval_a.authority_change == "NONE"
    assert receipt.selected_route_key == "route:b"


def test_success_saturation_can_probe_alternative_route() -> None:
    request = _request("code")
    a = _candidate("route:a", "node:a", base_cost=1.0)
    b = _candidate("route:b", "node:b", base_cost=1.1)
    successes = tuple(
        SuccessObservation.create(
            subject_id="node:a",
            route_key="route:a",
            source_ref=f"success:a:{i}",
            observed_at=120.0,
        )
        for i in range(5)
    )

    receipt = route_relational(
        request,
        [a, b],
        successes=successes,
        now=120.0,
    )

    eval_a = _evaluation(receipt, "route:a")
    assert eval_a.saturation_penalty == pytest.approx(0.5)
    assert eval_a.probe_recommended is True
    assert receipt.selected_route_key == "route:b"


def test_l2_weather_changes_route_cost_and_decays() -> None:
    request = _request("code")
    weather, _ = apply_routing_weather_update(
        RoutingWeatherState(),
        key="weather:route-a",
        value=1.0,
        actor_id="runtime:premise-engine",
        source_ref="premise:challenge",
        half_life_seconds=100.0,
        applied_at=100.0,
    )
    a = _candidate(
        "route:a",
        "node:a",
        base_cost=1.0,
        weather_keys=("weather:route-a",),
    )
    b = _candidate("route:b", "node:b", base_cost=1.3)

    early = route_relational(request, [a, b], weather=weather, now=100.0)
    late = route_relational(request, [a, b], weather=weather, now=300.0)

    assert _evaluation(early, "route:a").weather_penalty == pytest.approx(1.0)
    assert _evaluation(late, "route:a").weather_penalty == pytest.approx(0.25)
    assert early.selected_route_key == "route:b"
    assert late.selected_route_key == "route:a"


def test_negative_weather_cannot_subsidize_route_below_base_cost() -> None:
    request = _request("code")
    weather, _ = apply_routing_weather_update(
        RoutingWeatherState(),
        key="weather:negative",
        value=-100.0,
        actor_id="runtime:test",
        source_ref="fixture",
        half_life_seconds=100.0,
        applied_at=100.0,
    )
    a = _candidate(
        "route:a",
        "node:a",
        base_cost=1.0,
        weather_keys=("weather:negative",),
    )

    receipt = route_relational(request, [a], weather=weather, now=100.0)
    evaluation = _evaluation(receipt, "route:a")

    assert evaluation.weather_penalty == pytest.approx(0.0)
    assert evaluation.total_cost >= evaluation.base_cost


def test_clock_pressure_influences_cost_and_clock_exhaustion_blocks() -> None:
    request = _request("code")
    skin = ClockSkinContract(
        skin_id="skin:compute",
        mode=WALL_RATE,
        tick_rate_hz=1.0,
    )
    lease_a = issue_clock_lease(
        skin,
        subject_id="node:a",
        granted_ticks=100.0,
        opened_at=100.0,
    )
    lease_a = replace(lease_a, spent_ticks=90.0)
    lease_b = issue_clock_lease(
        skin,
        subject_id="node:b",
        granted_ticks=100.0,
        opened_at=100.0,
    )

    pressured = _candidate(
        "route:a",
        "node:a",
        base_cost=1.0,
        clock_lease=lease_a,
        expected_ticks=5.0,
    )
    calm = _candidate(
        "route:b",
        "node:b",
        base_cost=1.2,
        clock_lease=lease_b,
        expected_ticks=5.0,
    )

    receipt = route_relational(request, [pressured, calm], now=120.0)

    assert _evaluation(receipt, "route:a").clock_penalty == pytest.approx(0.9)
    assert receipt.selected_route_key == "route:b"

    exhausted = replace(lease_a, spent_ticks=100.0)
    blocked = _candidate(
        "route:blocked",
        "node:a",
        base_cost=0.0,
        clock_lease=exhausted,
        expected_ticks=1.0,
    )
    receipt2 = route_relational(request, [blocked, calm], now=120.0)
    assert _evaluation(receipt2, "route:blocked").admissible is False


def test_resource_pressure_influences_cost_and_insufficient_resource_blocks() -> None:
    request = _request("code")
    a = _candidate(
        "route:a",
        "node:a",
        base_cost=1.0,
        resource_spends=(ResourceSpend("compute_ms", 90.0),),
    )
    b = _candidate(
        "route:b",
        "node:b",
        base_cost=1.2,
        resource_spends=(ResourceSpend("compute_ms", 10.0),),
    )

    receipt = route_relational(request, [a, b], now=120.0)

    assert _evaluation(receipt, "route:a").resource_penalty == pytest.approx(0.9)
    assert _evaluation(receipt, "route:b").resource_penalty == pytest.approx(0.1)
    assert receipt.selected_route_key == "route:b"

    too_much = _candidate(
        "route:too-much",
        "node:c",
        base_cost=0.0,
        resource_spends=(ResourceSpend("compute_ms", 101.0),),
    )
    receipt2 = route_relational(request, [too_much, b], now=120.0)
    assert _evaluation(receipt2, "route:too-much").admissible is False


def test_active_coalition_capability_union_satisfies_request() -> None:
    coalition = _coalition()
    warrant = _warrant(coalition.coalition_id)
    candidate = _candidate(
        "route:coalition",
        coalition.coalition_id,
        base_cost=1.0,
        warrant=warrant,
        capabilities=(),
        coalition=coalition,
    )
    request = _request("plan", "code", "test")

    receipt = route_relational(request, [candidate], now=120.0)

    evaluation = _evaluation(receipt, "route:coalition")
    assert evaluation.admissible is True
    assert set(evaluation.available_capabilities) == {"plan", "code", "test"}


def test_degraded_coalition_gets_cost_not_authority_loss() -> None:
    coalition = _coalition()
    degraded, _ = degrade_coalition(
        coalition,
        unavailable_member_ids=("member:tester",),
        reason="tester unavailable",
        degraded_at=110.0,
    )
    candidate = _candidate(
        "route:coalition",
        degraded.coalition_id,
        base_cost=1.0,
        warrant=_warrant(degraded.coalition_id),
        capabilities=("plan", "code"),
        coalition=degraded,
    )
    solo = _candidate(
        "route:solo",
        "node:solo",
        base_cost=1.1,
        capabilities=("plan", "code"),
    )
    request = _request("plan", "code")

    receipt = route_relational(request, [candidate, solo], now=120.0)

    coal_eval = _evaluation(receipt, "route:coalition")
    assert coal_eval.admissible is True
    assert coal_eval.coalition_penalty == pytest.approx(0.25)
    assert coal_eval.authority_change == "NONE"
    assert receipt.selected_route_key == "route:solo"


def test_inactive_or_unbound_coalition_is_hard_blocked() -> None:
    coalition = _coalition()
    expired_time = coalition.expires_at + 1.0
    bound = _candidate(
        "route:expired-coalition",
        coalition.coalition_id,
        base_cost=0.0,
        warrant=_warrant(
            coalition.coalition_id,
            issued_at=100.0,
            lifetime=1000.0,
        ),
        coalition=coalition,
    )
    unbound = _candidate(
        "route:unbound",
        "coalition:ghost",
        base_cost=0.0,
        warrant=_warrant("coalition:ghost"),
    )

    receipt = route_relational(
        _request("code", requested_at=expired_time),
        [bound, unbound],
        now=expired_time,
    )

    assert receipt.selected_route_key is None
    assert _evaluation(receipt, "route:expired-coalition").admissible is False
    assert _evaluation(receipt, "route:unbound").admissible is False


def test_human_block_overrides_cheapest_route() -> None:
    request = _request("code")
    cheap = _candidate("route:cheap", "node:a", base_cost=0.0)
    costly = _candidate("route:costly", "node:b", base_cost=10.0)
    constraint = HumanRouteConstraint.block(
        route_key="route:cheap",
        authority_ref="human-seal:1",
        reason="operator forbids this route for current task",
        created_at=100.0,
    )

    receipt = route_relational(
        request,
        [cheap, costly],
        human_constraints=(constraint,),
        now=120.0,
    )

    assert _evaluation(receipt, "route:cheap").admissible is False
    assert receipt.selected_route_key == "route:costly"


def test_expired_human_block_does_not_act_as_silent_permanent_authority() -> None:
    request = _request("code")
    cheap = _candidate("route:cheap", "node:a", base_cost=0.0)
    costly = _candidate("route:costly", "node:b", base_cost=10.0)
    constraint = HumanRouteConstraint.block(
        route_key="route:cheap",
        authority_ref="human-seal:1",
        reason="temporary block",
        created_at=100.0,
        expires_at=110.0,
    )

    receipt = route_relational(
        request,
        [cheap, costly],
        human_constraints=(constraint,),
        now=120.0,
    )

    assert _evaluation(receipt, "route:cheap").admissible is True
    assert receipt.selected_route_key == "route:cheap"


def test_human_penalty_affects_cost_without_blocking_route() -> None:
    request = _request("code")
    a = _candidate("route:a", "node:a", base_cost=1.0)
    b = _candidate("route:b", "node:b", base_cost=1.2)
    constraint = HumanRouteConstraint.penalty(
        route_key="route:a",
        authority_ref="human-seal:1",
        reason="prefer alternate when practical",
        value=0.5,
        created_at=100.0,
    )

    receipt = route_relational(
        request,
        [a, b],
        human_constraints=(constraint,),
        now=120.0,
    )

    eval_a = _evaluation(receipt, "route:a")
    assert eval_a.admissible is True
    assert eval_a.human_penalty == pytest.approx(0.5)
    assert receipt.selected_route_key == "route:b"


def test_ties_are_deterministic_by_route_key() -> None:
    request = _request("code")
    z = _candidate("route:z", "node:z", base_cost=1.0)
    a = _candidate("route:a", "node:a", base_cost=1.0)

    receipt = route_relational(request, [z, a], now=120.0)

    assert receipt.selected_route_key == "route:a"


def test_all_blocked_returns_no_selection() -> None:
    request = _request("code")
    one = _candidate(
        "route:one",
        "node:one",
        capabilities=(),
    )
    two = _candidate(
        "route:two",
        "node:two",
        capabilities=(),
    )

    receipt = route_relational(request, [one, two], now=120.0)

    assert receipt.selected_route_key is None
    assert receipt.selected_actor_id is None
    assert all(not item.admissible for item in receipt.evaluations)


def test_empty_candidate_set_returns_no_selection() -> None:
    receipt = route_relational(_request("code"), [], now=120.0)

    assert receipt.selected_route_key is None
    assert receipt.evaluations == ()


def test_routing_receipt_never_changes_authority_warrant_citation_or_constitution() -> None:
    request = _request("code")
    candidate = _candidate("route:a", "node:a", base_cost=1.0)

    receipt = route_relational(request, [candidate], now=120.0)
    evaluation = receipt.evaluations[0]

    assert receipt.authority_change == "NONE"
    assert receipt.warrant_change == "NONE"
    assert receipt.citation_law_change == "NONE"
    assert receipt.constitutional_change == "NONE"
    assert evaluation.authority_change == "NONE"
    assert evaluation.warrant_change == "NONE"
    assert evaluation.citation_law_change == "NONE"
    assert evaluation.constitutional_change == "NONE"


def test_router_does_not_spend_or_mutate_warrant() -> None:
    warrant = _warrant("node:a", compute_limit=100.0)
    candidate = _candidate(
        "route:a",
        "node:a",
        warrant=warrant,
        resource_spends=(ResourceSpend("compute_ms", 50.0),),
    )

    receipt = route_relational(_request("code"), [candidate], now=120.0)

    assert receipt.selected_route_key == "route:a"
    assert warrant.remaining("compute_ms") == pytest.approx(100.0)


def test_custom_policy_changes_route_math_not_admissibility_law() -> None:
    request = _request("code")
    premise = _premise("route:a", PREMISE_CHALLENGED)
    a = _candidate("route:a", "node:a", base_cost=1.0, premises=(premise,))
    b = _candidate("route:b", "node:b", base_cost=1.5)
    policy = RelationalRoutingPolicy(
        premise_challenge_penalty=2.0,
        clock_pressure_scale=0.0,
        resource_pressure_scale=0.0,
        degraded_coalition_penalty=0.0,
        weather_scale=0.0,
    )

    receipt = route_relational(
        request,
        [a, b],
        policy=policy,
        now=120.0,
    )

    assert _evaluation(receipt, "route:a").admissible is True
    assert _evaluation(receipt, "route:a").premise_penalty == pytest.approx(2.0)
    assert receipt.selected_route_key == "route:b"


def test_hard_blocked_candidate_never_receives_conductance_score() -> None:
    request = _request("code")
    blocked = _candidate(
        "route:blocked",
        "node:blocked",
        capabilities=(),
        base_cost=0.0,
    )

    receipt = route_relational(request, [blocked], now=120.0)
    evaluation = receipt.evaluations[0]

    assert evaluation.admissible is False
    assert evaluation.total_cost is None
    assert evaluation.conductance == 0.0
    assert evaluation.scar_penalty == 0.0
    assert evaluation.saturation_penalty == 0.0


def test_route_key_must_be_unique_within_one_decision() -> None:
    request = _request("code")
    a = _candidate("route:same", "node:a")
    b = _candidate("route:same", "node:b")

    with pytest.raises(Exception):
        route_relational(request, [a, b], now=120.0)
