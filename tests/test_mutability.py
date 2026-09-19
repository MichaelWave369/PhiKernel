import pytest

from phikernel.mutability import (
    HUMAN,
    ConstitutionClause,
    ConstitutionSnapshot,
    HumanAuthoritySeal,
    MutabilityError,
    PolicyParameter,
    RoutingWeatherState,
    apply_constitution_petition,
    apply_policy_proposal,
    apply_routing_weather_update,
    issue_policy_mutation_grant,
    petition_constitution_change,
    propose_policy_change,
)


def _constitution() -> ConstitutionSnapshot:
    return ConstitutionSnapshot.genesis(
        [
            ConstitutionClause.create(
                "authority.capability_is_not_authority",
                True,
            ),
            ConstitutionClause.create(
                "authority.human_final",
                True,
            ),
            ConstitutionClause.create(
                "routing.failure_may_change_routing_not_law",
                True,
            ),
        ],
        created_at=100.0,
    )


def _human_seal(*, actor_id: str = "human:mikey") -> HumanAuthoritySeal:
    return HumanAuthoritySeal.create(
        actor_id=actor_id,
        authority_ref="operator-signature:test-001",
        issued_at=110.0,
    )


def test_constitution_petition_does_not_mutate_l0() -> None:
    constitution = _constitution()

    petition = petition_constitution_change(
        constitution,
        clause_id="routing.failure_may_change_routing_not_law",
        proposed_value=False,
        petitioner_id="runtime:scar-engine",
        reason="repeated failure pattern",
        evidence_refs=("scar:1", "scar:2"),
        created_at=120.0,
    )

    assert constitution.clause(
        "routing.failure_may_change_routing_not_law"
    ).value is True
    assert petition.proposed_value is False
    assert petition.constitutional_change == "PROPOSED_ONLY"
    assert petition.authority_change == "NONE"


def test_human_seal_can_apply_l0_petition_with_receipt() -> None:
    constitution = _constitution()
    petition = petition_constitution_change(
        constitution,
        clause_id="routing.failure_may_change_routing_not_law",
        proposed_value=False,
        petitioner_id="runtime:scar-engine",
        reason="operator-requested test amendment",
        evidence_refs=("scar:1",),
        created_at=120.0,
    )

    updated, receipt = apply_constitution_petition(
        constitution,
        petition,
        human_seal=_human_seal(),
        applied_at=130.0,
    )

    assert constitution.revision == 0
    assert updated.revision == 1
    assert updated.parent_snapshot_hash == constitution.snapshot_hash
    assert updated.snapshot_hash != constitution.snapshot_hash
    assert updated.clause(
        "routing.failure_may_change_routing_not_law"
    ).value is False
    assert receipt.prior_snapshot_hash == constitution.snapshot_hash
    assert receipt.resulting_snapshot_hash == updated.snapshot_hash
    assert receipt.constitutional_change == "HUMAN_AUTHORIZED_APPLIED"
    assert receipt.authority_change == "NONE"


def test_non_human_authority_seal_is_rejected_at_construction() -> None:
    with pytest.raises(MutabilityError):
        HumanAuthoritySeal(
            seal_id="seal:bad",
            actor_id="model:alpha",
            actor_kind="MODEL",
            authority_ref="self-asserted",
            issued_at=100.0,
        )


def test_stale_l0_petition_cannot_apply_to_newer_constitution() -> None:
    constitution = _constitution()
    first = petition_constitution_change(
        constitution,
        clause_id="authority.human_final",
        proposed_value=False,
        petitioner_id="runtime:test",
        reason="fixture",
        created_at=120.0,
    )
    newer, _ = apply_constitution_petition(
        constitution,
        first,
        human_seal=_human_seal(),
        applied_at=130.0,
    )

    stale = petition_constitution_change(
        constitution,
        clause_id="authority.capability_is_not_authority",
        proposed_value=False,
        petitioner_id="runtime:test",
        reason="fixture stale petition",
        created_at=121.0,
    )

    with pytest.raises(MutabilityError):
        apply_constitution_petition(
            newer,
            stale,
            human_seal=_human_seal(),
            applied_at=140.0,
        )


def test_l1_runtime_proposal_does_not_mutate_policy() -> None:
    policy = PolicyParameter(
        parameter_id="routing.scar_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )

    proposal = propose_policy_change(
        policy,
        proposed_value=1.4,
        proposer_id="runtime:scar-engine",
        source_ref="scar-profile:abc",
        reason="repeated verified failures",
        created_at=120.0,
    )

    assert policy.value == 1.0
    assert policy.revision == 0
    assert proposal.proposed_value == 1.4
    assert proposal.policy_change == "PROPOSED_ONLY"
    assert proposal.authority_change == "NONE"


def test_human_bounded_grant_allows_l1_change_inside_bounds() -> None:
    policy = PolicyParameter(
        parameter_id="routing.scar_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )
    proposal = propose_policy_change(
        policy,
        proposed_value=1.4,
        proposer_id="runtime:scar-engine",
        source_ref="scar-profile:abc",
        reason="repeated verified failures",
        created_at=120.0,
    )
    grant = issue_policy_mutation_grant(
        policy,
        grantee_id="runtime:policy-engine",
        minimum=0.8,
        maximum=1.5,
        human_seal=_human_seal(),
        lifetime_seconds=100.0,
        issued_at=115.0,
    )

    updated, receipt = apply_policy_proposal(
        policy,
        proposal,
        actor_id="runtime:policy-engine",
        grant=grant,
        applied_at=125.0,
    )

    assert policy.value == 1.0
    assert updated.value == pytest.approx(1.4)
    assert updated.revision == 1
    assert receipt.prior_value == pytest.approx(1.0)
    assert receipt.resulting_value == pytest.approx(1.4)
    assert receipt.authority_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_l1_grant_cannot_exceed_policy_bounds() -> None:
    policy = PolicyParameter(
        parameter_id="routing.saturation_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )

    with pytest.raises(MutabilityError):
        issue_policy_mutation_grant(
            policy,
            grantee_id="runtime:policy-engine",
            minimum=-1.0,
            maximum=3.0,
            human_seal=_human_seal(),
        )


def test_l1_change_cannot_exceed_grant_even_if_inside_policy_bounds() -> None:
    policy = PolicyParameter(
        parameter_id="routing.scar_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )
    proposal = propose_policy_change(
        policy,
        proposed_value=1.8,
        proposer_id="runtime:scar-engine",
        source_ref="scar-profile:abc",
        reason="fixture",
        created_at=120.0,
    )
    grant = issue_policy_mutation_grant(
        policy,
        grantee_id="runtime:policy-engine",
        minimum=0.8,
        maximum=1.5,
        human_seal=_human_seal(),
        issued_at=115.0,
    )

    with pytest.raises(MutabilityError):
        apply_policy_proposal(
            policy,
            proposal,
            actor_id="runtime:policy-engine",
            grant=grant,
            applied_at=125.0,
        )

    assert policy.value == 1.0
    assert policy.revision == 0


def test_expired_l1_grant_fails_closed() -> None:
    policy = PolicyParameter(
        parameter_id="routing.scar_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )
    proposal = propose_policy_change(
        policy,
        proposed_value=1.2,
        proposer_id="runtime:scar-engine",
        source_ref="scar-profile:abc",
        reason="fixture",
        created_at=120.0,
    )
    grant = issue_policy_mutation_grant(
        policy,
        grantee_id="runtime:policy-engine",
        minimum=0.8,
        maximum=1.5,
        human_seal=_human_seal(),
        lifetime_seconds=5.0,
        issued_at=115.0,
    )

    with pytest.raises(MutabilityError):
        apply_policy_proposal(
            policy,
            proposal,
            actor_id="runtime:policy-engine",
            grant=grant,
            applied_at=121.0,
        )


def test_wrong_actor_cannot_use_someone_elses_l1_grant() -> None:
    policy = PolicyParameter(
        parameter_id="routing.scar_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )
    proposal = propose_policy_change(
        policy,
        proposed_value=1.2,
        proposer_id="runtime:scar-engine",
        source_ref="scar-profile:abc",
        reason="fixture",
        created_at=120.0,
    )
    grant = issue_policy_mutation_grant(
        policy,
        grantee_id="runtime:policy-engine",
        minimum=0.8,
        maximum=1.5,
        human_seal=_human_seal(),
        issued_at=115.0,
    )

    with pytest.raises(MutabilityError):
        apply_policy_proposal(
            policy,
            proposal,
            actor_id="runtime:other-engine",
            grant=grant,
            applied_at=120.0,
        )


def test_l2_runtime_can_write_decaying_weather_without_authority_change() -> None:
    state = RoutingWeatherState()

    updated, receipt = apply_routing_weather_update(
        state,
        key="route:code/node-a:scar-pressure",
        value=0.8,
        actor_id="runtime:scar-engine",
        source_ref="scar-profile:abc",
        half_life_seconds=100.0,
        applied_at=100.0,
    )

    entry = updated.entry("route:code/node-a:scar-pressure")
    assert state.revision == 0
    assert updated.revision == 1
    assert entry is not None
    assert entry.value_at(100.0) == pytest.approx(0.8)
    assert entry.value_at(200.0) == pytest.approx(0.4)
    assert receipt.authority_change == "NONE"
    assert receipt.policy_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_l2_update_replaces_same_key_but_preserves_other_weather() -> None:
    state = RoutingWeatherState()
    one, _ = apply_routing_weather_update(
        state,
        key="route:a",
        value=1.0,
        actor_id="runtime:router",
        source_ref="receipt:1",
        half_life_seconds=100.0,
        applied_at=100.0,
    )
    two, _ = apply_routing_weather_update(
        one,
        key="route:b",
        value=2.0,
        actor_id="runtime:router",
        source_ref="receipt:2",
        half_life_seconds=100.0,
        applied_at=101.0,
    )
    three, _ = apply_routing_weather_update(
        two,
        key="route:a",
        value=0.5,
        actor_id="runtime:router",
        source_ref="receipt:3",
        half_life_seconds=100.0,
        applied_at=102.0,
    )

    assert len(three.entries) == 2
    assert three.entry("route:a").value == pytest.approx(0.5)
    assert three.entry("route:b").value == pytest.approx(2.0)
    assert three.revision == 3


def test_l0_l1_l2_are_independent_state_surfaces() -> None:
    constitution = _constitution()
    policy = PolicyParameter(
        parameter_id="routing.scar_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )
    weather = RoutingWeatherState()

    weather2, weather_receipt = apply_routing_weather_update(
        weather,
        key="route:a",
        value=0.9,
        actor_id="runtime:scar-engine",
        source_ref="scar:abc",
        half_life_seconds=100.0,
        applied_at=120.0,
    )

    assert weather2.revision == 1
    assert policy.revision == 0
    assert policy.value == 1.0
    assert constitution.revision == 0
    assert weather_receipt.policy_change == "NONE"
    assert weather_receipt.constitutional_change == "NONE"
