from dataclasses import replace
from types import SimpleNamespace

import pytest

from phikernel.mutability import HumanAuthoritySeal
from phikernel.routing_shadow import (
    AGREE,
    DIVERGE,
    LEGACY_UNMAPPED,
    VNEXT_BLOCKS_LEGACY,
    VNEXT_ERROR,
    VNEXT_NO_ROUTE,
    ShadowComparisonReceipt,
)
from phikernel.witness_bench import (
    ADVISE,
    BOUNDED_CONTROL,
    NOT_READY,
    READY_FOR_HUMAN_REVIEW,
    SHADOW,
    BehavioralWitnessCase,
    PromotionState,
    WitnessBenchError,
    WitnessBenchPolicy,
    authorize_promotion,
    collapse_to_shadow,
    evaluate_witness_bench,
    propose_promotion,
    witness_hash,
)


class _TestManifest:
    anchor_id = "anchor:test-witness"

    def manifest_hash(self):
        return "b" * 64


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


def _shadow_receipt(
    *,
    receipt_id: str,
    bundle_id: str,
    state: str = AGREE,
    legacy_admissible: bool | None = True,
    legacy_safe: bool = True,
):
    return ShadowComparisonReceipt(
        receipt_id=receipt_id,
        legacy_coach="Flow",
        legacy_route_key="route:flow",
        legacy_safe_to_proceed=legacy_safe,
        legacy_authoritative=True,
        vnext_selected_route_key=(
            None if state in {VNEXT_NO_ROUTE, VNEXT_ERROR} else "route:flow"
        ),
        comparison_state=state,
        legacy_candidate_admissible_in_vnext=legacy_admissible,
        legacy_candidate_block_reasons=(
            ("warrant denied",)
            if state == VNEXT_BLOCKS_LEGACY
            else ()
        ),
        vnext_error=(
            "RelationalRoutingError: fixture"
            if state == VNEXT_ERROR
            else None
        ),
        think_bundle_hash=witness_hash({"bundle": bundle_id}),
        evaluated_at=100.0,
    )


def _case(
    i: int,
    *,
    state: str = AGREE,
    bundle_id: str | None = None,
    legacy_admissible: bool | None = True,
    replay_verified: bool = True,
    invariants_preserved: bool = True,
    observable: bool = True,
    reviewed: bool = True,
):
    receipt = _shadow_receipt(
        receipt_id=f"shadow:{i}",
        bundle_id=bundle_id or f"bundle:{i}",
        state=state,
        legacy_admissible=legacy_admissible,
    )
    return BehavioralWitnessCase.create(
        receipt,
        input_value={"request": i},
        state_before={"state": "before", "i": i},
        trigger_ref=f"trigger:{i}",
        state_after={"state": "after", "i": i},
        mechanism_enabled_effect={"route": "vnext", "i": i},
        mechanism_removed_effect=(
            {"route": "legacy", "i": i}
            if observable
            else {"route": "vnext", "i": i}
        ),
        failure_case={"failure": f"case:{i}"},
        replay_verified=replay_verified,
        invariants_preserved=invariants_preserved,
        conflict_review_ref=(
            f"review:{i}"
            if reviewed and state in {DIVERGE, VNEXT_BLOCKS_LEGACY}
            else None
        ),
        observed_at=110.0 + i,
    )


def _policy(**kwargs):
    values = {
        "min_receipts": 3,
        "min_distinct_bundles": 3,
        "max_vnext_error_rate": 0.0,
        "min_candidate_coverage_rate": 1.0,
        "min_replay_verified_rate": 1.0,
        "min_invariant_preservation_rate": 1.0,
        "min_behavioral_reality_rate": 1.0,
        "min_conflict_review_rate": 1.0,
    }
    values.update(kwargs)
    return WitnessBenchPolicy(**values)


def _ready_report():
    return evaluate_witness_bench(
        [_case(1), _case(2), _case(3)],
        policy=_policy(),
        evaluated_at=200.0,
    )


def test_behavioral_effect_is_derived_from_enabled_vs_removed_observation() -> None:
    observed = _case(1, observable=True)
    decorative = _case(2, observable=False)

    assert observed.behavioral_effect_observed is True
    assert decorative.behavioral_effect_observed is False
    assert observed.authority_change == "NONE"
    assert observed.warrant_change == "NONE"
    assert observed.citation_law_change == "NONE"
    assert observed.constitutional_change == "NONE"


def test_same_shadow_receipt_cannot_be_counted_twice() -> None:
    case = _case(1)
    duplicate = replace(case, case_id="case:other")

    with pytest.raises(WitnessBenchError):
        evaluate_witness_bench(
            [case, duplicate],
            policy=_policy(min_receipts=2, min_distinct_bundles=1),
        )


def test_same_witness_case_cannot_be_counted_twice() -> None:
    case = _case(1)

    with pytest.raises(WitnessBenchError):
        evaluate_witness_bench(
            [case, case],
            policy=_policy(min_receipts=2, min_distinct_bundles=1),
        )


def test_repeated_identical_bundle_does_not_satisfy_diversity_threshold() -> None:
    cases = [
        _case(1, bundle_id="same"),
        _case(2, bundle_id="same"),
        _case(3, bundle_id="same"),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(min_distinct_bundles=2),
        evaluated_at=200.0,
    )

    assert report.status == NOT_READY
    assert report.distinct_bundle_count == 1
    assert any("distinct bundles" in reason for reason in report.blocker_reasons)


def test_agreement_rate_is_measured_but_not_a_readiness_requirement() -> None:
    cases = [
        _case(1, state=DIVERGE, reviewed=True),
        _case(2, state=DIVERGE, reviewed=True),
        _case(3, state=DIVERGE, reviewed=True),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(),
        evaluated_at=200.0,
    )

    assert report.agreement_rate == pytest.approx(0.0)
    assert report.divergence_rate == pytest.approx(1.0)
    assert report.status == READY_FOR_HUMAN_REVIEW


def test_unreviewed_conflicts_block_readiness() -> None:
    cases = [
        _case(1, state=DIVERGE, reviewed=False),
        _case(2),
        _case(3),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(),
        evaluated_at=200.0,
    )

    assert report.status == NOT_READY
    assert report.conflict_review_rate == pytest.approx(0.0)
    assert any("conflict review" in reason for reason in report.blocker_reasons)


def test_vnext_error_rate_blocks_readiness() -> None:
    cases = [
        _case(1, state=VNEXT_ERROR, legacy_admissible=None),
        _case(2),
        _case(3),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(min_candidate_coverage_rate=0.60),
        evaluated_at=200.0,
    )

    assert report.vnext_error_rate == pytest.approx(1 / 3)
    assert report.status == NOT_READY
    assert any("error rate" in reason for reason in report.blocker_reasons)


def test_candidate_coverage_gap_blocks_readiness() -> None:
    cases = [
        _case(1, state=LEGACY_UNMAPPED, legacy_admissible=None),
        _case(2),
        _case(3),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(),
        evaluated_at=200.0,
    )

    assert report.candidate_coverage_rate == pytest.approx(2 / 3)
    assert report.status == NOT_READY
    assert any("candidate coverage" in reason for reason in report.blocker_reasons)


def test_failed_replay_blocks_readiness() -> None:
    cases = [
        _case(1, replay_verified=False),
        _case(2),
        _case(3),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(),
        evaluated_at=200.0,
    )

    assert report.replay_verified_rate == pytest.approx(2 / 3)
    assert report.status == NOT_READY


def test_invariant_failure_blocks_readiness() -> None:
    cases = [
        _case(1, invariants_preserved=False),
        _case(2),
        _case(3),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(),
        evaluated_at=200.0,
    )

    assert report.invariant_preservation_rate == pytest.approx(2 / 3)
    assert report.status == NOT_READY


def test_decorative_mechanism_fails_behavioral_reality_requirement() -> None:
    cases = [
        _case(1, observable=False),
        _case(2),
        _case(3),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(),
        evaluated_at=200.0,
    )

    assert report.behavioral_reality_rate == pytest.approx(2 / 3)
    assert report.status == NOT_READY
    assert any("behavioral reality" in reason for reason in report.blocker_reasons)


def test_hard_block_discovery_is_evidence_not_automatic_failure() -> None:
    cases = [
        _case(1, state=VNEXT_BLOCKS_LEGACY, reviewed=True),
        _case(2),
        _case(3),
    ]

    report = evaluate_witness_bench(
        cases,
        policy=_policy(),
        evaluated_at=200.0,
    )

    assert report.hard_block_discovery_rate == pytest.approx(1 / 3)
    assert report.status == READY_FOR_HUMAN_REVIEW


def test_ready_report_has_no_authority_or_mode_change() -> None:
    report = _ready_report()

    assert report.status == READY_FOR_HUMAN_REVIEW
    assert report.ready_for_human_review is True
    assert report.blocker_reasons == ()
    assert report.authority_change == "NONE"
    assert report.routing_mode_change == "NONE"
    assert report.constitutional_change == "NONE"


def test_report_hash_is_bound_to_evidence() -> None:
    one = evaluate_witness_bench(
        [_case(1), _case(2), _case(3)],
        policy=_policy(),
        evaluated_at=200.0,
    )
    two = evaluate_witness_bench(
        [_case(1), _case(2), _case(4)],
        policy=_policy(),
        evaluated_at=200.0,
    )

    assert one.report_hash != two.report_hash


def test_not_ready_report_cannot_create_promotion_proposal() -> None:
    report = evaluate_witness_bench(
        [_case(1)],
        policy=_policy(),
        evaluated_at=200.0,
    )
    state = PromotionState.genesis()

    with pytest.raises(WitnessBenchError):
        propose_promotion(
            state,
            report,
            target_mode=ADVISE,
            proposer_id="runtime:promotion-engine",
            reason="fixture",
            created_at=210.0,
        )


def test_ready_report_may_only_propose_shadow_to_advise() -> None:
    report = _ready_report()
    state = PromotionState.genesis()

    proposal = propose_promotion(
        state,
        report,
        target_mode=ADVISE,
        proposer_id="runtime:promotion-engine",
        reason="witness thresholds satisfied",
        created_at=210.0,
    )

    assert proposal.base_mode == SHADOW
    assert proposal.target_mode == ADVISE
    assert proposal.witness_report_id == report.report_id
    assert proposal.witness_report_hash == report.report_hash
    assert proposal.routing_mode_change == "PROPOSED_ONLY"
    assert proposal.authority_change == "NONE"
    assert proposal.steering_authority_change == "NONE"


def test_shadow_cannot_skip_directly_to_bounded_control() -> None:
    report = _ready_report()
    state = PromotionState.genesis()

    with pytest.raises(WitnessBenchError):
        propose_promotion(
            state,
            report,
            target_mode=BOUNDED_CONTROL,
            proposer_id="runtime:promotion-engine",
            reason="skip the boring part",
            created_at=210.0,
        )


def test_advise_state_cannot_be_constructed_without_human_seal_lineage() -> None:
    with pytest.raises(WitnessBenchError):
        PromotionState(mode=ADVISE, revision=1)


def test_generic_human_seal_cannot_authorize_unbound_promotion() -> None:
    report = _ready_report()
    state = PromotionState.genesis()
    proposal = propose_promotion(
        state,
        report,
        target_mode=ADVISE,
        proposer_id="runtime:promotion-engine",
        reason="fixture",
        created_at=210.0,
    )
    seal = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=220.0,
    )

    with pytest.raises(WitnessBenchError):
        authorize_promotion(
            state,
            proposal,
            report,
            seal,
            applied_at=221.0,
        )


def test_human_seal_must_be_bound_to_exact_proposal_report_and_target() -> None:
    report = _ready_report()
    state = PromotionState.genesis()
    proposal = propose_promotion(
        state,
        report,
        target_mode=ADVISE,
        proposer_id="runtime:promotion-engine",
        reason="fixture",
        created_at=210.0,
    )
    seal = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=220.0,
        metadata={
            "promotion_proposal_id": proposal.proposal_id,
            "witness_report_hash": witness_hash({"wrong": "report"}),
            "target_mode": ADVISE,
        },
    )

    with pytest.raises(WitnessBenchError):
        authorize_promotion(
            state,
            proposal,
            report,
            seal,
            applied_at=221.0,
        )


def test_human_seal_may_not_predate_promotion_proposal() -> None:
    report = _ready_report()
    state = PromotionState.genesis()
    proposal = propose_promotion(
        state,
        report,
        target_mode=ADVISE,
        proposer_id="runtime:promotion-engine",
        reason="fixture",
        created_at=210.0,
    )
    seal = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=200.0,
        metadata={
            "promotion_proposal_id": proposal.proposal_id,
            "witness_report_hash": report.report_hash,
            "target_mode": ADVISE,
        },
    )

    with pytest.raises(WitnessBenchError):
        authorize_promotion(
            state,
            proposal,
            report,
            seal,
            applied_at=221.0,
        )


def test_bound_human_seal_authorizes_advise_without_steering_authority() -> None:
    report = _ready_report()
    state = PromotionState.genesis()
    proposal = propose_promotion(
        state,
        report,
        target_mode=ADVISE,
        proposer_id="runtime:promotion-engine",
        reason="witness thresholds satisfied",
        created_at=210.0,
    )
    seal = HumanAuthoritySeal.create_signed(
        anchor_service=_TEST_ANCHOR,
        passphrase="fixture",
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=220.0,
        metadata={
            "promotion_proposal_id": proposal.proposal_id,
            "witness_report_hash": report.report_hash,
            "target_mode": ADVISE,
        },
    )

    updated, receipt = authorize_promotion(
        state,
        proposal,
        report,
        seal,
        anchor_service=_TEST_ANCHOR,
        applied_at=221.0,
    )

    assert updated.mode == ADVISE
    assert updated.revision == 1
    assert updated.authorized_by_seal_id == seal.seal_id
    assert updated.may_observe is True
    assert updated.may_advise is True
    assert updated.may_steer is False
    assert receipt.routing_mode_change == "HUMAN_AUTHORIZED_APPLIED"
    assert receipt.steering_authority_change == "NONE"
    assert receipt.authority_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_stale_promotion_proposal_fails_closed() -> None:
    report = _ready_report()
    state = PromotionState.genesis()
    proposal = propose_promotion(
        state,
        report,
        target_mode=ADVISE,
        proposer_id="runtime:promotion-engine",
        reason="fixture",
        created_at=210.0,
    )
    advanced_state = replace(state, revision=1)
    seal = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=220.0,
        metadata={
            "promotion_proposal_id": proposal.proposal_id,
            "witness_report_hash": report.report_hash,
            "target_mode": ADVISE,
        },
    )

    with pytest.raises(WitnessBenchError):
        authorize_promotion(
            advanced_state,
            proposal,
            report,
            seal,
            applied_at=221.0,
        )


def test_tampered_report_hash_fails_closed() -> None:
    report = _ready_report()
    state = PromotionState.genesis()
    proposal = propose_promotion(
        state,
        report,
        target_mode=ADVISE,
        proposer_id="runtime:promotion-engine",
        reason="fixture",
        created_at=210.0,
    )
    tampered = replace(
        report,
        report_hash=witness_hash({"tampered": True}),
    )
    seal = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=220.0,
        metadata={
            "promotion_proposal_id": proposal.proposal_id,
            "witness_report_hash": report.report_hash,
            "target_mode": ADVISE,
        },
    )

    with pytest.raises(WitnessBenchError):
        authorize_promotion(
            state,
            proposal,
            tampered,
            seal,
            applied_at=221.0,
        )


def test_bounded_control_is_reserved_for_future_control_witness_contract() -> None:
    report = _ready_report()
    advise = PromotionState(
        mode=ADVISE,
        revision=1,
        authorized_by_seal_id="seal:advise",
    )

    with pytest.raises(WitnessBenchError):
        propose_promotion(
            advise,
            report,
            target_mode=BOUNDED_CONTROL,
            proposer_id="runtime:promotion-engine",
            reason="not implemented yet",
            created_at=210.0,
        )


def test_privilege_may_collapse_to_shadow_without_new_grant() -> None:
    advise = PromotionState(
        mode=ADVISE,
        revision=1,
        authorized_by_seal_id="seal:advise",
    )

    collapsed, receipt = collapse_to_shadow(
        advise,
        source_ref="runtime:health-check",
        reason="new invariant failure observed",
        collapsed_at=300.0,
    )

    assert collapsed.mode == SHADOW
    assert collapsed.revision == 2
    assert collapsed.authorized_by_seal_id is None
    assert collapsed.may_advise is False
    assert collapsed.may_steer is False
    assert receipt.routing_mode_change == "COLLAPSED_TO_SHADOW"
    assert receipt.steering_authority_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_shadow_cannot_collapse_below_shadow() -> None:
    with pytest.raises(WitnessBenchError):
        collapse_to_shadow(
            PromotionState.genesis(),
            source_ref="fixture",
            reason="already least privilege",
        )


def test_successful_witness_report_does_not_self_promote_state() -> None:
    state = PromotionState.genesis()
    report = _ready_report()

    assert report.ready_for_human_review is True
    assert state.mode == SHADOW
    assert state.revision == 0
    assert state.may_advise is False
    assert state.may_steer is False
