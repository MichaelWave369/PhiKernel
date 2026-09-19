import pytest

from phikernel.transition import (
    LICENSE,
    REFUSE,
    ResourceSpend,
    TransitionProposal,
    evaluate_transition,
)
from phikernel.warrant import (
    ResourceBudget,
    Warrant,
    WarrantBudgetExceededError,
    WarrantDeniedError,
)


def _warrant(*, issued_at: float = 100.0, lifetime: float = 60.0) -> Warrant:
    return Warrant.issue(
        issuer="human:mikey",
        bearer="organ:builder-1",
        scopes=(
            "read:memory/*",
            "write:workspace/candidate/*",
            "execute:tool/python",
        ),
        budgets=(
            ResourceBudget("compute_ms", 1000),
            ResourceBudget("tokens", 5000),
        ),
        issued_at=issued_at,
        lifetime_seconds=lifetime,
        metadata={"authority_source": "operator"},
    )


def test_warrant_allows_bearer_inside_scope() -> None:
    warrant = _warrant()

    check = warrant.authorize(
        actor_id="organ:builder-1",
        operation="write",
        target="workspace/candidate/main.py",
        now=120.0,
    )

    assert check.allowed is True
    assert check.requested_scope == "write:workspace/candidate/main.py"


def test_warrant_is_non_transferable() -> None:
    warrant = _warrant()

    check = warrant.authorize(
        actor_id="organ:other",
        operation="write",
        target="workspace/candidate/main.py",
        now=120.0,
    )

    assert check.allowed is False
    assert "non-transferable" in check.reason


def test_warrant_denies_out_of_scope_operation() -> None:
    warrant = _warrant()

    check = warrant.authorize(
        actor_id="organ:builder-1",
        operation="write",
        target="runtime/live/main.py",
        now=120.0,
    )

    assert check.allowed is False
    assert "outside warrant scope" in check.reason


def test_warrant_expiry_fails_closed() -> None:
    warrant = _warrant(issued_at=100.0, lifetime=20.0)

    check = warrant.authorize(
        actor_id="organ:builder-1",
        operation="read",
        target="memory/context",
        now=120.0,
    )

    assert check.allowed is False
    assert check.reason == "warrant expired"


def test_revoked_warrant_fails_closed() -> None:
    warrant = _warrant().revoke(reason="operator revoked build organ")

    check = warrant.authorize(
        actor_id="organ:builder-1",
        operation="execute",
        target="tool/python",
        now=120.0,
    )

    assert check.allowed is False
    assert "operator revoked build organ" in check.reason


def test_spend_returns_new_warrant_and_receipt_without_authority_growth() -> None:
    warrant = _warrant()

    updated, receipt = warrant.spend(
        actor_id="organ:builder-1",
        resource_kind="compute_ms",
        amount=250,
        now=120.0,
    )

    assert warrant.remaining("compute_ms") == 1000
    assert updated.remaining("compute_ms") == 750
    assert updated.scopes == warrant.scopes
    assert updated.expires_at == warrant.expires_at
    assert updated.bearer == warrant.bearer
    assert receipt.before_remaining == 1000
    assert receipt.after_remaining == 750
    assert receipt.authority_change == "NONE"


def test_budget_overdraft_is_rejected() -> None:
    warrant = _warrant()

    with pytest.raises(WarrantBudgetExceededError):
        warrant.spend(
            actor_id="organ:builder-1",
            resource_kind="compute_ms",
            amount=1001,
            now=120.0,
        )


def test_non_bearer_cannot_spend_warrant() -> None:
    warrant = _warrant()

    with pytest.raises(WarrantDeniedError):
        warrant.spend(
            actor_id="organ:other",
            resource_kind="compute_ms",
            amount=1,
            now=120.0,
        )


def test_transition_licenses_valid_scoped_budgeted_change() -> None:
    warrant = _warrant()
    proposal = TransitionProposal.create(
        actor_id="organ:builder-1",
        operation="write",
        target="workspace/candidate/main.py",
        warrant_id=warrant.warrant_id,
        resource_spends=[
            ResourceSpend("compute_ms", 100),
            ResourceSpend("tokens", 400),
        ],
        provenance_refs=["source:translator:abc123"],
        requested_at=120.0,
    )

    evaluation = evaluate_transition(proposal, warrant, now=120.0)

    assert evaluation.verdict.decision == LICENSE
    assert evaluation.verdict.allowed is True
    assert evaluation.verdict.authority_change == "NONE"
    assert evaluation.verdict.claims_promoted == 0
    assert evaluation.warrant_after.remaining("compute_ms") == 900
    assert evaluation.warrant_after.remaining("tokens") == 4600
    assert len(evaluation.spend_receipts) == 2


def test_transition_refuses_wrong_warrant_identity() -> None:
    warrant = _warrant()
    proposal = TransitionProposal.create(
        actor_id="organ:builder-1",
        operation="write",
        target="workspace/candidate/main.py",
        warrant_id="wrong-warrant",
        requested_at=120.0,
    )

    evaluation = evaluate_transition(proposal, warrant, now=120.0)

    assert evaluation.verdict.decision == REFUSE
    assert evaluation.verdict.allowed is False
    assert evaluation.warrant_after == warrant
    assert evaluation.spend_receipts == ()


def test_transition_refuses_out_of_scope_without_spending() -> None:
    warrant = _warrant()
    proposal = TransitionProposal.create(
        actor_id="organ:builder-1",
        operation="write",
        target="runtime/live/main.py",
        warrant_id=warrant.warrant_id,
        resource_spends=[ResourceSpend("compute_ms", 100)],
        requested_at=120.0,
    )

    evaluation = evaluate_transition(proposal, warrant, now=120.0)

    assert evaluation.verdict.decision == REFUSE
    assert evaluation.warrant_after == warrant
    assert evaluation.warrant_after.remaining("compute_ms") == 1000


def test_transition_budget_failure_is_atomic_no_partial_spend() -> None:
    warrant = _warrant()
    proposal = TransitionProposal.create(
        actor_id="organ:builder-1",
        operation="write",
        target="workspace/candidate/main.py",
        warrant_id=warrant.warrant_id,
        resource_spends=[
            ResourceSpend("compute_ms", 100),
            ResourceSpend("tokens", 5001),
        ],
        requested_at=120.0,
    )

    evaluation = evaluate_transition(proposal, warrant, now=120.0)

    assert evaluation.verdict.decision == REFUSE
    assert "budget 'tokens' exhausted" in evaluation.verdict.reason
    assert evaluation.spend_receipts == ()
    assert evaluation.warrant_after.remaining("compute_ms") == 1000
    assert evaluation.warrant_after.remaining("tokens") == 5000


def test_success_never_expands_scope_or_lifetime() -> None:
    warrant = _warrant()
    proposal = TransitionProposal.create(
        actor_id="organ:builder-1",
        operation="execute",
        target="tool/python",
        warrant_id=warrant.warrant_id,
        resource_spends=[ResourceSpend("compute_ms", 10)],
        requested_at=120.0,
    )

    evaluation = evaluate_transition(proposal, warrant, now=120.0)

    assert evaluation.verdict.decision == LICENSE
    assert evaluation.warrant_after.scopes == warrant.scopes
    assert evaluation.warrant_after.expires_at == warrant.expires_at
    assert evaluation.warrant_after.issuer == warrant.issuer
    assert evaluation.warrant_after.bearer == warrant.bearer
