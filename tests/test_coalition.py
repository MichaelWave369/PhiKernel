import pytest

from phikernel.coalition import (
    ACTIVE,
    DEGRADED,
    DISSOLVED,
    CoalitionCharter,
    CoalitionError,
    MemberCapability,
    activate_coalition,
    degrade_coalition,
    disband_coalition,
    issue_coalition_warrant,
    record_coalition_outcome,
)
from phikernel.transition import ResourceSpend, TransitionProposal, evaluate_transition
from phikernel.warrant import ResourceBudget, Warrant


def _members():
    return (
        MemberCapability(
            member_id="member:planner",
            capability_tags=("plan",),
            source_ref="capability:planner:v1",
        ),
        MemberCapability(
            member_id="member:coder",
            capability_tags=("code",),
            source_ref="capability:coder:v1",
        ),
        MemberCapability(
            member_id="member:tester",
            capability_tags=("test",),
            source_ref="capability:tester:v1",
        ),
    )


def _active_charter(*, created_at=100.0, lifetime=100.0):
    draft = CoalitionCharter.draft(
        purpose="build-and-verify bounded candidate",
        members=_members(),
        lifetime_seconds=lifetime,
        created_at=created_at,
    )
    return activate_coalition(draft, activated_at=created_at + 1.0)[0]


def _sponsor(*, issued_at=100.0, lifetime=200.0):
    return Warrant.issue(
        issuer="human:mikey",
        bearer="runtime:coalition-factory",
        scopes=(
            "read:memory/*",
            "write:workspace/candidate/*",
            "execute:tool/python",
        ),
        budgets=(
            ResourceBudget("compute_ms", 1000.0),
            ResourceBudget("tokens", 5000.0),
        ),
        issued_at=issued_at,
        lifetime_seconds=lifetime,
    )


def _coalition_warrant(charter=None, sponsor=None, *, issued_at=102.0):
    charter = charter or _active_charter()
    sponsor = sponsor or _sponsor()
    return issue_coalition_warrant(
        charter,
        sponsor,
        scopes=(
            "write:workspace/candidate/*",
            "execute:tool/python",
        ),
        budgets=(
            ResourceBudget("compute_ms", 400.0),
            ResourceBudget("tokens", 1200.0),
        ),
        issued_at=issued_at,
    )


def test_weak_specialists_form_one_distinct_coalition_identity() -> None:
    charter = _active_charter()

    assert charter.state == ACTIVE
    assert charter.coalition_id.startswith("coalition:")
    assert set(charter.member_ids) == {
        "member:planner",
        "member:coder",
        "member:tester",
    }
    assert set(charter.capability_union) == {"plan", "code", "test"}
    assert charter.authority_change == "NONE"
    assert charter.constitutional_change == "NONE"


def test_coalition_requires_multiple_distinct_members() -> None:
    one = (
        MemberCapability(
            member_id="member:solo",
            capability_tags=("code",),
            source_ref="capability:solo",
        ),
    )

    with pytest.raises(CoalitionError):
        CoalitionCharter.draft(
            purpose="invalid solo coalition",
            members=one,
            lifetime_seconds=10.0,
            created_at=100.0,
        )

    duplicate = (
        MemberCapability(
            member_id="member:x",
            capability_tags=("plan",),
            source_ref="capability:x1",
        ),
        MemberCapability(
            member_id="member:x",
            capability_tags=("code",),
            source_ref="capability:x2",
        ),
    )

    with pytest.raises(CoalitionError):
        CoalitionCharter.draft(
            purpose="duplicate identity coalition",
            members=duplicate,
            lifetime_seconds=10.0,
            created_at=100.0,
        )


def test_activation_cannot_add_unknown_member() -> None:
    draft = CoalitionCharter.draft(
        purpose="fixture",
        members=_members(),
        lifetime_seconds=100.0,
        created_at=100.0,
    )

    with pytest.raises(CoalitionError):
        activate_coalition(
            draft,
            active_member_ids=(
                "member:planner",
                "member:coder",
                "member:intruder",
            ),
            activated_at=101.0,
        )


def test_expired_charter_cannot_activate() -> None:
    draft = CoalitionCharter.draft(
        purpose="fixture",
        members=_members(),
        lifetime_seconds=10.0,
        created_at=100.0,
    )

    with pytest.raises(CoalitionError):
        activate_coalition(draft, activated_at=110.0)


def test_coalition_warrant_is_borne_by_coalition_not_members() -> None:
    charter = _active_charter()
    warrant = _coalition_warrant(charter=charter)

    assert warrant.bearer == charter.coalition_id
    assert warrant.parent_warrant_id is not None
    assert warrant.metadata["coalition_id"] == charter.coalition_id
    assert set(warrant.metadata["member_ids"]) == set(charter.active_member_ids)

    member_check = warrant.authorize(
        actor_id="member:coder",
        operation="execute",
        target="tool/python",
        now=103.0,
    )
    coalition_check = warrant.authorize(
        actor_id=charter.coalition_id,
        operation="execute",
        target="tool/python",
        now=103.0,
    )

    assert member_check.allowed is False
    assert "non-transferable" in member_check.reason
    assert coalition_check.allowed is True


def test_coalition_scope_cannot_exceed_sponsor_scope() -> None:
    charter = _active_charter()
    sponsor = _sponsor()

    with pytest.raises(CoalitionError):
        issue_coalition_warrant(
            charter,
            sponsor,
            scopes=("write:runtime/live/*",),
            issued_at=102.0,
        )


def test_coalition_budget_cannot_exceed_sponsor_remaining_budget() -> None:
    charter = _active_charter()
    sponsor = _sponsor()

    with pytest.raises(CoalitionError):
        issue_coalition_warrant(
            charter,
            sponsor,
            scopes=("execute:tool/python",),
            budgets=(ResourceBudget("compute_ms", 1001.0),),
            issued_at=102.0,
        )


def test_coalition_warrant_lifetime_is_capped_by_sponsor() -> None:
    charter = _active_charter(created_at=100.0, lifetime=500.0)
    sponsor = _sponsor(issued_at=100.0, lifetime=20.0)

    warrant = issue_coalition_warrant(
        charter,
        sponsor,
        scopes=("execute:tool/python",),
        issued_at=105.0,
    )

    assert warrant.expires_at == pytest.approx(120.0)
    assert warrant.expires_at < charter.expires_at


def test_coalition_can_perform_governed_transition_under_its_own_warrant() -> None:
    charter = _active_charter()
    warrant = _coalition_warrant(charter=charter)

    proposal = TransitionProposal.create(
        actor_id=charter.coalition_id,
        operation="execute",
        target="tool/python",
        warrant_id=warrant.warrant_id,
        resource_spends=(ResourceSpend("compute_ms", 25.0),),
        provenance_refs=("charter:fixture",),
        requested_at=103.0,
    )
    evaluation = evaluate_transition(proposal, warrant, now=103.0)

    assert evaluation.verdict.allowed is True
    assert evaluation.proposal.actor_id == charter.coalition_id
    assert evaluation.warrant_after.remaining("compute_ms") == pytest.approx(375.0)
    assert evaluation.verdict.authority_change == "NONE"


def test_member_cannot_replay_coalition_transition_with_coalition_warrant() -> None:
    charter = _active_charter()
    warrant = _coalition_warrant(charter=charter)

    proposal = TransitionProposal.create(
        actor_id="member:coder",
        operation="execute",
        target="tool/python",
        warrant_id=warrant.warrant_id,
        requested_at=103.0,
    )
    evaluation = evaluate_transition(proposal, warrant, now=103.0)

    assert evaluation.verdict.allowed is False
    assert "non-transferable" in evaluation.verdict.reason


def test_success_credit_stays_with_coalition_not_members() -> None:
    charter = _active_charter()
    warrant = _coalition_warrant(charter=charter)
    proposal = TransitionProposal.create(
        actor_id=charter.coalition_id,
        operation="execute",
        target="tool/python",
        warrant_id=warrant.warrant_id,
        requested_at=103.0,
    )
    evaluation = evaluate_transition(proposal, warrant, now=103.0)

    outcome = record_coalition_outcome(
        charter,
        evaluation,
        success=True,
        source_ref="test:coalition-success",
        recorded_at=104.0,
    )

    assert outcome.credit_subject_id == charter.coalition_id
    assert outcome.member_authority_inheritance == "NONE"
    assert outcome.authority_change == "NONE"
    assert outcome.constitutional_change == "NONE"


def test_degradation_reduces_capability_union_without_changing_authority_law() -> None:
    charter = _active_charter()
    warrant = _coalition_warrant(charter=charter)

    degraded, receipt = degrade_coalition(
        charter,
        unavailable_member_ids=("member:tester",),
        reason="tester resource unavailable",
        degraded_at=105.0,
    )

    assert degraded.state == DEGRADED
    assert set(degraded.active_member_ids) == {
        "member:planner",
        "member:coder",
    }
    assert set(degraded.capability_union) == {"plan", "code"}
    assert warrant.bearer == charter.coalition_id
    assert warrant.scopes == (
        "write:workspace/candidate/*",
        "execute:tool/python",
    )
    assert receipt.authority_change == "NONE"
    assert receipt.warrant_change == "NONE"
    assert receipt.constitutional_change == "NONE"


def test_degradation_cannot_collapse_below_two_members() -> None:
    charter = _active_charter()

    with pytest.raises(CoalitionError):
        degrade_coalition(
            charter,
            unavailable_member_ids=(
                "member:coder",
                "member:tester",
            ),
            reason="only planner remains",
            degraded_at=105.0,
        )


def test_disband_revokes_warrant_and_leaves_zero_temporary_grants() -> None:
    charter = _active_charter()
    warrant = _coalition_warrant(charter=charter)

    dissolved, revoked, receipt = disband_coalition(
        charter,
        warrant,
        reason="task complete",
        dissolved_at=110.0,
    )

    assert dissolved.state == DISSOLVED
    assert dissolved.active_member_ids == ()
    assert revoked.revoked is True
    assert receipt.warrant_revoked is True
    assert receipt.temporary_grants_remaining == 0
    assert receipt.member_authority_inheritance == "NONE"
    assert receipt.constitutional_change == "NONE"

    coalition_check = revoked.authorize(
        actor_id=charter.coalition_id,
        operation="execute",
        target="tool/python",
        now=111.0,
    )
    member_check = revoked.authorize(
        actor_id="member:coder",
        operation="execute",
        target="tool/python",
        now=111.0,
    )

    assert coalition_check.allowed is False
    assert "revoked" in coalition_check.reason
    assert member_check.allowed is False


def test_disband_does_not_modify_sponsor_warrant() -> None:
    charter = _active_charter()
    sponsor = _sponsor()
    coalition_warrant = _coalition_warrant(
        charter=charter,
        sponsor=sponsor,
    )

    _, revoked, _ = disband_coalition(
        charter,
        coalition_warrant,
        reason="task complete",
        dissolved_at=110.0,
    )

    assert revoked.revoked is True
    assert sponsor.revoked is False
    assert sponsor.bearer == "runtime:coalition-factory"
    assert sponsor.remaining("compute_ms") == pytest.approx(1000.0)
    assert sponsor.remaining("tokens") == pytest.approx(5000.0)
