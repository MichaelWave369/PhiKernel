import pytest

from phikernel.contradiction import (
    HUMAN_SEAL,
    TEST_WARRANT,
    ContradictionObject,
)
from phikernel.mutability import (
    ConstitutionClause,
    ConstitutionSnapshot,
    PolicyParameter,
    RoutingWeatherState,
)
from phikernel.premise import (
    ACTIVE,
    CHALLENGED,
    CONTRADICTION,
    QUARANTINED,
    RETIRED,
    PremiseError,
    PremiseObject,
    PremisePolicy,
    apply_premise_evaluation,
    challenge_from_contradiction,
    challenge_from_scar,
    evaluate_dependent_carriage,
    evaluate_dependent_route,
    evaluate_premise,
    petition_constitution_from_premise,
    propose_policy_change_from_premise,
    record_unverified_challenge,
    resolve_premise,
    write_premise_routing_weather,
)
from phikernel.scar import REFUSED, FailureScar


def _premise():
    return PremiseObject.create(
        statement="The primary sensor clock is stable enough for direct fusion.",
        claim_key="premise:sensor-clock-stable",
        provenance_refs=("prov:sensor-doc:1",),
        evidence_refs=("evidence:calibration:1",),
        dependent_route_keys=(
            "route:sensor-fusion",
            "route:simulation-input",
        ),
        dependent_carriage_ids=(
            "carriage:fusion-result",
            "carriage:simulation-seed",
        ),
        created_at=100.0,
    )


def _scar(*, severity=0.7, source="transition:v1"):
    return FailureScar.create(
        subject_id="sensor:fusion",
        route_key="route:sensor-fusion",
        failure_kind=REFUSED,
        severity=severity,
        source_ref=source,
        observed_at=110.0,
        half_life_seconds=1000.0,
    )


def _constitution():
    return ConstitutionSnapshot.genesis(
        [
            ConstitutionClause.create(
                "routing.failure_may_change_routing_not_law",
                True,
            ),
            ConstitutionClause.create(
                "authority.human_final",
                True,
            ),
        ],
        created_at=100.0,
    )


def test_premise_is_explicit_first_class_state() -> None:
    premise = _premise()

    assert premise.state == ACTIVE
    assert premise.statement.startswith("The primary sensor clock")
    assert premise.claim_key == "premise:sensor-clock-stable"
    assert premise.authority_change == "NONE"
    assert premise.policy_change == "NONE"
    assert premise.constitutional_change == "NONE"
    assert premise.blocks_dependents is False


def test_verified_scar_can_challenge_premise() -> None:
    premise = _premise()
    scar = _scar(severity=0.7)

    challenge = challenge_from_scar(
        premise,
        scar,
        challenger_id="runtime:scar-engine",
        reason="fusion route repeatedly failed under this assumption",
        created_at=120.0,
    )
    evaluation = evaluate_premise(
        premise,
        [challenge],
        now=121.0,
    )
    updated, receipt = apply_premise_evaluation(
        premise,
        evaluation,
        [challenge],
    )

    assert challenge.verified is True
    assert updated.state == CHALLENGED
    assert challenge.challenge_id in updated.challenge_refs
    assert receipt.prior_state == ACTIVE
    assert receipt.resulting_state == CHALLENGED
    assert receipt.authority_change == "NONE"
    assert receipt.policy_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_unverified_challenge_does_not_change_premise_state() -> None:
    premise = _premise()

    challenge = record_unverified_challenge(
        premise,
        challenge_kind=CONTRADICTION,
        source_ref="model:opinion:1",
        severity=1.0,
        challenger_id="model:alpha",
        reason="I strongly disagree",
        created_at=120.0,
    )
    evaluation = evaluate_premise(
        premise,
        [challenge],
        now=121.0,
    )
    updated, receipt = apply_premise_evaluation(
        premise,
        evaluation,
        [challenge],
    )

    assert challenge.verified is False
    assert evaluation.verified_challenge_count == 0
    assert evaluation.challenge_score == pytest.approx(0.0)
    assert updated.state == ACTIVE
    assert receipt.resulting_state == ACTIVE


def test_multiple_verified_challenges_can_quarantine_premise() -> None:
    premise = _premise()
    one = challenge_from_scar(
        premise,
        _scar(severity=0.7, source="transition:v1"),
        challenger_id="runtime:scar-engine",
        reason="first verified failure pattern",
        created_at=120.0,
    )
    two = challenge_from_scar(
        premise,
        _scar(severity=0.7, source="transition:v2"),
        challenger_id="runtime:scar-engine",
        reason="second verified failure pattern",
        created_at=121.0,
    )

    evaluation = evaluate_premise(
        premise,
        [one, two],
        now=122.0,
    )
    updated, receipt = apply_premise_evaluation(
        premise,
        evaluation,
        [one, two],
    )

    assert evaluation.challenge_score == pytest.approx(1.4)
    assert updated.state == QUARANTINED
    assert updated.blocks_dependents is True
    assert receipt.resulting_state == QUARANTINED


def test_open_contradiction_can_challenge_premise() -> None:
    premise = _premise()
    contradiction = ContradictionObject.open(
        claim_key="claim:sensor-clock",
        exhibit_carriage_ids=("carriage:a", "carriage:b"),
        reason="clock measurements disagree",
        opened_at=120.0,
    )

    challenge = challenge_from_contradiction(
        premise,
        contradiction,
        challenger_id="runtime:contradiction-engine",
        severity=1.0,
        created_at=121.0,
    )

    assert challenge.verified is True
    assert challenge.challenge_kind == CONTRADICTION
    assert challenge.source_ref == f"contradiction:{contradiction.contradiction_id}"


def test_resolved_contradiction_cannot_create_new_premise_challenge() -> None:
    premise = _premise()
    contradiction = ContradictionObject.open(
        claim_key="claim:sensor-clock",
        exhibit_carriage_ids=("carriage:a", "carriage:b"),
        reason="clock measurements disagree",
        opened_at=120.0,
    ).resolve(
        resolution_kind=HUMAN_SEAL,
        resolver_id="human:mikey",
        authority_ref="seal:human:1",
        resolution_ref="resolution:clock:1",
        resolved_at=125.0,
    )

    with pytest.raises(PremiseError):
        challenge_from_contradiction(
            premise,
            contradiction,
            challenger_id="runtime:contradiction-engine",
        )


def test_runtime_evaluation_cannot_auto_restore_quarantined_premise() -> None:
    premise = _premise()
    one = challenge_from_scar(
        premise,
        _scar(severity=0.7, source="transition:v1"),
        challenger_id="runtime:scar-engine",
        reason="first",
    )
    two = challenge_from_scar(
        premise,
        _scar(severity=0.7, source="transition:v2"),
        challenger_id="runtime:scar-engine",
        reason="second",
    )
    quarantined = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [one, two], now=130.0),
        [one, two],
    )[0]

    later = evaluate_premise(
        quarantined,
        [],
        now=10000.0,
    )
    unchanged, _ = apply_premise_evaluation(
        quarantined,
        later,
        [],
    )

    assert quarantined.state == QUARANTINED
    assert later.recommended_state == QUARANTINED
    assert unchanged.state == QUARANTINED


def test_challenged_route_is_restricted_but_not_blocked() -> None:
    premise = _premise()
    challenge = challenge_from_scar(
        premise,
        _scar(severity=0.7),
        challenger_id="runtime:scar-engine",
        reason="fixture",
    )
    challenged = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [challenge], now=120.0),
        [challenge],
    )[0]

    decision = evaluate_dependent_route(
        challenged,
        "route:sensor-fusion",
        now=121.0,
    )

    assert decision.allowed is True
    assert decision.restricted is True
    assert decision.premise_state == CHALLENGED


def test_quarantined_premise_blocks_dependent_route_and_carriage() -> None:
    premise = _premise()
    one = challenge_from_scar(
        premise,
        _scar(severity=0.8, source="transition:v1"),
        challenger_id="runtime:scar-engine",
        reason="first",
    )
    two = challenge_from_scar(
        premise,
        _scar(severity=0.8, source="transition:v2"),
        challenger_id="runtime:scar-engine",
        reason="second",
    )
    quarantined = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [one, two], now=120.0),
        [one, two],
    )[0]

    route = evaluate_dependent_route(
        quarantined,
        "route:sensor-fusion",
        now=121.0,
    )
    carriage = evaluate_dependent_carriage(
        quarantined,
        "carriage:fusion-result",
        now=121.0,
    )

    assert route.allowed is False
    assert route.restricted is True
    assert carriage.allowed is False
    assert carriage.restricted is True


def test_premise_cannot_block_unregistered_dependency() -> None:
    premise = _premise()

    with pytest.raises(PremiseError):
        evaluate_dependent_route(
            premise,
            "route:unrelated",
        )

    with pytest.raises(PremiseError):
        evaluate_dependent_carriage(
            premise,
            "carriage:unrelated",
        )


def test_challenged_premise_can_write_only_l2_routing_weather() -> None:
    premise = _premise()
    challenge = challenge_from_scar(
        premise,
        _scar(severity=0.7),
        challenger_id="runtime:scar-engine",
        reason="fixture",
    )
    challenged = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [challenge], now=120.0),
        [challenge],
    )[0]
    weather = RoutingWeatherState()

    updated, receipt = write_premise_routing_weather(
        challenged,
        weather,
        route_key="route:sensor-fusion",
        actor_id="runtime:premise-engine",
        source_ref=f"premise:{challenged.premise_id}",
        applied_at=121.0,
    )

    entry = updated.entry(
        f"premise:{challenged.premise_id}:route:sensor-fusion"
    )
    assert updated.revision == 1
    assert entry is not None
    assert entry.value == pytest.approx(0.5)
    assert receipt.authority_change == "NONE"
    assert receipt.policy_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_quarantined_premise_writes_stronger_l2_pressure() -> None:
    premise = _premise()
    policy = PremisePolicy(
        challenge_threshold=0.5,
        quarantine_threshold=1.0,
        challenged_routing_pressure=0.4,
        quarantined_routing_pressure=1.2,
        routing_half_life_seconds=100.0,
    )
    challenge = challenge_from_scar(
        premise,
        _scar(severity=1.0),
        challenger_id="runtime:scar-engine",
        reason="fixture",
    )
    quarantined = apply_premise_evaluation(
        premise,
        evaluate_premise(
            premise,
            [challenge],
            policy=policy,
            now=120.0,
        ),
        [challenge],
    )[0]

    updated, _ = write_premise_routing_weather(
        quarantined,
        RoutingWeatherState(),
        route_key="route:sensor-fusion",
        actor_id="runtime:premise-engine",
        source_ref="premise-pressure:1",
        policy=policy,
        applied_at=121.0,
    )

    entry = updated.entry(
        f"premise:{quarantined.premise_id}:route:sensor-fusion"
    )
    assert entry.value == pytest.approx(1.2)
    assert entry.value_at(221.0) == pytest.approx(0.6)


def test_active_premise_cannot_emit_l1_policy_proposal() -> None:
    premise = _premise()
    policy = PolicyParameter(
        parameter_id="routing.scar_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )

    with pytest.raises(PremiseError):
        propose_policy_change_from_premise(
            premise,
            policy,
            proposed_value=1.2,
            proposer_id="runtime:premise-engine",
            source_ref="premise-analysis:1",
            reason="fixture",
        )


def test_challenged_premise_may_propose_l1_but_does_not_apply_it() -> None:
    premise = _premise()
    challenge = challenge_from_scar(
        premise,
        _scar(severity=0.7),
        challenger_id="runtime:scar-engine",
        reason="fixture",
    )
    challenged = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [challenge], now=120.0),
        [challenge],
    )[0]
    policy = PolicyParameter(
        parameter_id="routing.scar_penalty_scale",
        value=1.0,
        minimum=0.0,
        maximum=2.0,
    )

    proposal = propose_policy_change_from_premise(
        challenged,
        policy,
        proposed_value=1.2,
        proposer_id="runtime:premise-engine",
        source_ref="premise-analysis:1",
        reason="challenge pattern suggests stronger routing caution",
        created_at=121.0,
    )

    assert proposal.proposed_value == pytest.approx(1.2)
    assert proposal.policy_change == "PROPOSED_ONLY"
    assert proposal.authority_change == "NONE"
    assert policy.value == pytest.approx(1.0)
    assert policy.revision == 0


def test_only_quarantined_or_retired_premise_may_petition_l0() -> None:
    premise = _premise()
    constitution = _constitution()

    with pytest.raises(PremiseError):
        petition_constitution_from_premise(
            premise,
            constitution,
            clause_id="routing.failure_may_change_routing_not_law",
            proposed_value=False,
            petitioner_id="runtime:premise-engine",
            reason="fixture",
        )


def test_quarantined_premise_may_petition_l0_but_cannot_apply_it() -> None:
    premise = _premise()
    one = challenge_from_scar(
        premise,
        _scar(severity=0.8, source="transition:v1"),
        challenger_id="runtime:scar-engine",
        reason="first",
    )
    two = challenge_from_scar(
        premise,
        _scar(severity=0.8, source="transition:v2"),
        challenger_id="runtime:scar-engine",
        reason="second",
    )
    quarantined = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [one, two], now=120.0),
        [one, two],
    )[0]
    constitution = _constitution()

    petition = petition_constitution_from_premise(
        quarantined,
        constitution,
        clause_id="routing.failure_may_change_routing_not_law",
        proposed_value=False,
        petitioner_id="runtime:premise-engine",
        reason="systemic premise failure requires human constitutional review",
        created_at=121.0,
    )

    assert petition.constitutional_change == "PROPOSED_ONLY"
    assert petition.authority_change == "NONE"
    assert f"premise:{quarantined.premise_id}" in petition.evidence_refs
    assert constitution.revision == 0
    assert constitution.clause(
        "routing.failure_may_change_routing_not_law"
    ).value is True


def test_explicit_resolution_can_restore_quarantined_premise() -> None:
    premise = _premise()
    one = challenge_from_scar(
        premise,
        _scar(severity=0.8, source="transition:v1"),
        challenger_id="runtime:scar-engine",
        reason="first",
    )
    two = challenge_from_scar(
        premise,
        _scar(severity=0.8, source="transition:v2"),
        challenger_id="runtime:scar-engine",
        reason="second",
    )
    quarantined = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [one, two], now=120.0),
        [one, two],
    )[0]

    restored, receipt = resolve_premise(
        quarantined,
        resulting_state=ACTIVE,
        resolution_kind=TEST_WARRANT,
        resolver_id="test-harness:clock-calibration",
        authority_ref="warrant:test:123",
        resolution_ref="test-result:clock-stable:456",
        resolved_at=130.0,
    )

    assert restored.state == ACTIVE
    assert restored.blocks_dependents is False
    assert receipt.prior_state == QUARANTINED
    assert receipt.resulting_state == ACTIVE
    assert receipt.authority_change == "NONE"
    assert receipt.policy_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_explicit_resolution_can_retire_bad_premise() -> None:
    premise = _premise()
    challenge = challenge_from_scar(
        premise,
        _scar(severity=0.7),
        challenger_id="runtime:scar-engine",
        reason="fixture",
    )
    challenged = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [challenge], now=120.0),
        [challenge],
    )[0]

    retired, receipt = resolve_premise(
        challenged,
        resulting_state=RETIRED,
        resolution_kind=HUMAN_SEAL,
        resolver_id="human:mikey",
        authority_ref="seal:human:retire-premise",
        resolution_ref="replacement-premise:2",
        resolved_at=130.0,
    )

    assert retired.state == RETIRED
    assert retired.retired_at == pytest.approx(130.0)
    assert retired.blocks_dependents is True
    assert receipt.resulting_state == RETIRED


def test_resolution_requires_external_authority_reference() -> None:
    premise = _premise()
    challenge = challenge_from_scar(
        premise,
        _scar(severity=0.7),
        challenger_id="runtime:scar-engine",
        reason="fixture",
    )
    challenged = apply_premise_evaluation(
        premise,
        evaluate_premise(premise, [challenge], now=120.0),
        [challenge],
    )[0]

    with pytest.raises(PremiseError):
        resolve_premise(
            challenged,
            resulting_state=ACTIVE,
            resolution_kind=TEST_WARRANT,
            resolver_id="test-harness:clock",
            authority_ref="",
            resolution_ref="test-result:1",
        )
