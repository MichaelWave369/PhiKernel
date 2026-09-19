import pytest

from phikernel.carriage import CarriageCandidate, evaluate_carriage
from phikernel.contradiction import HUMAN_SEAL, ContradictionObject
from phikernel.saturation import (
    SaturationPolicy,
    SuccessObservation,
    compute_route_adjustment,
)
from phikernel.scar import (
    CONTRADICTION,
    PROVENANCE_BREAK,
    REFUSED,
    FailureScar,
    ScarError,
    build_scar_profile,
    scar_from_contradiction,
    scar_from_membrane,
    scar_from_transition,
)
from phikernel.transition import ResourceSpend, TransitionProposal, evaluate_transition
from phikernel.warrant import ResourceBudget, Warrant


def _licensed_transition(*, provenance_refs=("prov:1",)):
    warrant = Warrant.issue(
        issuer="human:mikey",
        bearer="organ:alpha",
        scopes=("write:workspace/candidate/*",),
        budgets=(ResourceBudget("compute_ms", 1000),),
        issued_at=100.0,
        lifetime_seconds=100.0,
    )
    proposal = TransitionProposal.create(
        actor_id="organ:alpha",
        operation="write",
        target="workspace/candidate/result.json",
        warrant_id=warrant.warrant_id,
        resource_spends=(ResourceSpend("compute_ms", 10),),
        provenance_refs=provenance_refs,
        requested_at=120.0,
    )
    return evaluate_transition(proposal, warrant, now=120.0)


def _refused_transition():
    warrant = Warrant.issue(
        issuer="human:mikey",
        bearer="organ:alpha",
        scopes=("read:memory/*",),
        issued_at=100.0,
        lifetime_seconds=100.0,
    )
    proposal = TransitionProposal.create(
        actor_id="organ:alpha",
        operation="write",
        target="workspace/candidate/result.json",
        warrant_id=warrant.warrant_id,
        requested_at=120.0,
    )
    return evaluate_transition(proposal, warrant, now=120.0)


def test_failure_scar_decays_by_half_life() -> None:
    scar = FailureScar.create(
        subject_id="organ:alpha",
        route_key="build:python",
        failure_kind=REFUSED,
        severity=1.0,
        source_ref="transition:v1",
        observed_at=100.0,
        half_life_seconds=100.0,
    )

    assert scar.weight_at(now=100.0) == pytest.approx(1.0)
    assert scar.weight_at(now=200.0) == pytest.approx(0.5)
    assert scar.weight_at(now=300.0) == pytest.approx(0.25)


def test_repeated_matching_scars_accumulate_but_other_routes_do_not() -> None:
    scars = [
        FailureScar.create(
            subject_id="organ:alpha",
            route_key="build:python",
            failure_kind=REFUSED,
            severity=0.7,
            source_ref="transition:v1",
            observed_at=100.0,
            half_life_seconds=1000.0,
        ),
        FailureScar.create(
            subject_id="organ:alpha",
            route_key="build:python",
            failure_kind=PROVENANCE_BREAK,
            severity=0.4,
            source_ref="membrane:m1",
            observed_at=100.0,
            half_life_seconds=1000.0,
        ),
        FailureScar.create(
            subject_id="organ:alpha",
            route_key="vision:image",
            failure_kind=REFUSED,
            severity=1.0,
            source_ref="transition:v2",
            observed_at=100.0,
            half_life_seconds=1000.0,
        ),
    ]

    profile = build_scar_profile(
        scars,
        subject_id="organ:alpha",
        route_key="build:python",
        now=100.0,
    )

    assert profile.scar_count == 2
    assert profile.active_weight == pytest.approx(1.1)
    assert set(profile.failure_kinds) == {REFUSED, PROVENANCE_BREAK}
    assert profile.authority_change == "NONE"
    assert profile.warrant_change == "NONE"
    assert profile.constitutional_change == "NONE"


def test_refused_transition_can_create_receipted_scar() -> None:
    transition = _refused_transition()

    scar = scar_from_transition(
        transition,
        route_key="build:python",
        severity=0.8,
        observed_at=130.0,
    )

    assert scar.subject_id == "organ:alpha"
    assert scar.failure_kind == REFUSED
    assert scar.source_ref == f"transition:{transition.verdict.verdict_id}"
    assert scar.authority_change == "NONE"


def test_successful_transition_does_not_create_failure_scar() -> None:
    transition = _licensed_transition()

    with pytest.raises(ScarError):
        scar_from_transition(
            transition,
            route_key="build:python",
            severity=0.8,
        )


def test_broken_provenance_membrane_creates_provenance_scar() -> None:
    transition = _licensed_transition(
        provenance_refs=("prov:1", "prov:missing")
    )
    candidate = CarriageCandidate.create(
        payload={"answer": 42},
        claim_key="claim:alpha",
        transition=transition,
        created_at=121.0,
    )
    membrane = evaluate_carriage(
        candidate,
        transition,
        verified_provenance_refs={"prov:1"},
        now=122.0,
    )

    scar = scar_from_membrane(
        candidate,
        membrane,
        subject_id="organ:alpha",
        route_key="build:python",
        severity=0.9,
        observed_at=123.0,
    )

    assert scar.failure_kind == PROVENANCE_BREAK
    assert scar.source_ref == f"membrane:{membrane.membrane_verdict_id}"


def test_open_contradiction_creates_navigation_scar_without_picking_winner() -> None:
    contradiction = ContradictionObject.open(
        claim_key="claim:temperature",
        exhibit_carriage_ids=("carriage:a", "carriage:b"),
        reason="incompatible admitted measurements",
        opened_at=120.0,
    )

    scar = scar_from_contradiction(
        contradiction,
        subject_id="route:temperature",
        route_key="sensor:fusion",
        severity=0.6,
        observed_at=121.0,
    )

    assert scar.failure_kind == CONTRADICTION
    assert scar.metadata["exhibit_carriage_ids"] == ["carriage:a", "carriage:b"]
    assert "winner" not in scar.metadata


def test_resolved_contradiction_does_not_create_new_scar() -> None:
    contradiction = ContradictionObject.open(
        claim_key="claim:x",
        exhibit_carriage_ids=("carriage:a", "carriage:b"),
        reason="conflict",
        opened_at=120.0,
    ).resolve(
        resolution_kind=HUMAN_SEAL,
        resolver_id="human:mikey",
        authority_ref="seal:1",
        resolution_ref="resolution:1",
        resolved_at=130.0,
    )

    with pytest.raises(ScarError):
        scar_from_contradiction(
            contradiction,
            subject_id="route:x",
            route_key="reasoning:x",
            severity=0.5,
        )


def test_balanced_success_share_has_no_saturation_penalty() -> None:
    successes = [
        SuccessObservation.create(
            subject_id="node:a",
            route_key="task:code",
            source_ref="receipt:a1",
            observed_at=100.0,
        ),
        SuccessObservation.create(
            subject_id="node:b",
            route_key="task:code",
            source_ref="receipt:b1",
            observed_at=100.0,
        ),
    ]

    receipt = compute_route_adjustment(
        subject_id="node:a",
        route_key="task:code",
        base_cost=1.0,
        successes=successes,
        viable_alternatives=1,
        now=100.0,
    )

    assert receipt.success_flow_share == pytest.approx(0.5)
    assert receipt.saturation_observed is False
    assert receipt.saturation_penalty == pytest.approx(0.0)
    assert receipt.adjusted_cost == pytest.approx(1.0)


def test_dominant_success_path_gets_saturation_cost_when_alternatives_exist() -> None:
    successes = [
        *[
            SuccessObservation.create(
                subject_id="node:a",
                route_key="task:code",
                source_ref=f"receipt:a{i}",
                observed_at=100.0,
            )
            for i in range(9)
        ],
        SuccessObservation.create(
            subject_id="node:b",
            route_key="task:code",
            source_ref="receipt:b1",
            observed_at=100.0,
        ),
    ]

    receipt = compute_route_adjustment(
        subject_id="node:a",
        route_key="task:code",
        base_cost=1.0,
        successes=successes,
        viable_alternatives=1,
        now=100.0,
    )

    assert receipt.success_flow_share == pytest.approx(0.9)
    assert receipt.saturation_observed is True
    assert receipt.saturation_penalty == pytest.approx(0.4)
    assert receipt.adjusted_cost == pytest.approx(1.4)
    assert receipt.probe_recommended is True


def test_dominant_only_viable_path_is_observed_but_not_throttled() -> None:
    successes = [
        SuccessObservation.create(
            subject_id="node:a",
            route_key="task:rare",
            source_ref=f"receipt:{i}",
            observed_at=100.0,
        )
        for i in range(5)
    ]

    receipt = compute_route_adjustment(
        subject_id="node:a",
        route_key="task:rare",
        base_cost=1.0,
        successes=successes,
        viable_alternatives=0,
        now=100.0,
    )

    assert receipt.success_flow_share == pytest.approx(1.0)
    assert receipt.saturation_observed is True
    assert receipt.saturation_penalty == pytest.approx(0.0)
    assert receipt.adjusted_cost == pytest.approx(1.0)
    assert receipt.probe_recommended is False


def test_failure_and_saturation_penalties_compose_without_authority_change() -> None:
    scars = [
        FailureScar.create(
            subject_id="node:a",
            route_key="task:code",
            failure_kind=REFUSED,
            severity=0.5,
            source_ref="transition:v1",
            observed_at=100.0,
            half_life_seconds=1000.0,
        )
    ]
    successes = [
        *[
            SuccessObservation.create(
                subject_id="node:a",
                route_key="task:code",
                source_ref=f"receipt:a{i}",
                observed_at=100.0,
            )
            for i in range(3)
        ],
        SuccessObservation.create(
            subject_id="node:b",
            route_key="task:code",
            source_ref="receipt:b1",
            observed_at=100.0,
        ),
    ]

    receipt = compute_route_adjustment(
        subject_id="node:a",
        route_key="task:code",
        base_cost=2.0,
        scars=scars,
        successes=successes,
        viable_alternatives=1,
        now=100.0,
    )

    assert receipt.scar_penalty == pytest.approx(0.5)
    assert receipt.success_flow_share == pytest.approx(0.75)
    assert receipt.saturation_penalty == pytest.approx(0.25)
    assert receipt.adjusted_cost == pytest.approx(2.75)
    assert receipt.authority_change == "NONE"
    assert receipt.warrant_change == "NONE"
    assert receipt.citation_law_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_old_failure_loses_routing_influence_over_time() -> None:
    scar = FailureScar.create(
        subject_id="node:a",
        route_key="task:code",
        failure_kind=REFUSED,
        severity=1.0,
        source_ref="transition:old",
        observed_at=0.0,
        half_life_seconds=100.0,
    )

    early = compute_route_adjustment(
        subject_id="node:a",
        route_key="task:code",
        base_cost=1.0,
        scars=(scar,),
        now=100.0,
    )
    late = compute_route_adjustment(
        subject_id="node:a",
        route_key="task:code",
        base_cost=1.0,
        scars=(scar,),
        now=300.0,
    )

    assert early.scar_penalty == pytest.approx(0.5)
    assert late.scar_penalty == pytest.approx(0.125)
    assert late.adjusted_cost < early.adjusted_cost


def test_success_history_is_decaying_not_permanent_monopoly_memory() -> None:
    successes = [
        SuccessObservation.create(
            subject_id="node:a",
            route_key="task:code",
            source_ref="receipt:a-old",
            observed_at=0.0,
            half_life_seconds=100.0,
            weight=8.0,
        ),
        SuccessObservation.create(
            subject_id="node:b",
            route_key="task:code",
            source_ref="receipt:b-new",
            observed_at=300.0,
            half_life_seconds=100.0,
            weight=1.0,
        ),
    ]

    receipt = compute_route_adjustment(
        subject_id="node:a",
        route_key="task:code",
        base_cost=1.0,
        successes=successes,
        viable_alternatives=1,
        now=300.0,
    )

    # node:a decays from weight 8 to 1 after three half-lives; node:b is 1 now.
    assert receipt.success_flow_share == pytest.approx(0.5)
    assert receipt.saturation_penalty == pytest.approx(0.0)


def test_custom_policy_changes_routing_cost_not_authority() -> None:
    successes = [
        SuccessObservation.create(
            subject_id="node:a",
            route_key="task:code",
            source_ref="receipt:a",
            observed_at=100.0,
        )
    ]
    policy = SaturationPolicy(
        flow_share_cap=0.25,
        scar_penalty_scale=2.0,
        saturation_penalty_scale=3.0,
        probe_threshold=0.2,
    )

    receipt = compute_route_adjustment(
        subject_id="node:a",
        route_key="task:code",
        base_cost=1.0,
        successes=successes,
        viable_alternatives=2,
        policy=policy,
        now=100.0,
    )

    assert receipt.saturation_penalty == pytest.approx(2.25)
    assert receipt.adjusted_cost == pytest.approx(3.25)
    assert receipt.authority_change == "NONE"
    assert receipt.warrant_change == "NONE"
    assert receipt.citation_law_change == "NONE"
    assert receipt.constitutional_change == "NONE"
