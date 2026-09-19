from dataclasses import replace

import pytest

from phikernel.carriage import (
    ADMIT,
    QUARANTINE,
    REFUSE,
    CarriageCandidate,
    CarriageError,
    evaluate_carriage,
    evaluate_citation,
    payload_hash,
)
from phikernel.contradiction import (
    HUMAN_SEAL,
    OPEN,
    RESOLVED,
    ContradictionError,
    ContradictionObject,
)
from phikernel.transition import (
    ResourceSpend,
    TransitionProposal,
    evaluate_transition,
)
from phikernel.warrant import ResourceBudget, Warrant


def _licensed_transition(
    *,
    provenance_refs: tuple[str, ...] = ("prov:source:1",),
    evidence_refs: tuple[str, ...] = ("evidence:test:1",),
):
    warrant = Warrant.issue(
        issuer="human:mikey",
        bearer="organ:research-1",
        scopes=("write:workspace/candidate/*",),
        budgets=(ResourceBudget("compute_ms", 1000),),
        issued_at=100.0,
        lifetime_seconds=100.0,
    )
    proposal = TransitionProposal.create(
        actor_id="organ:research-1",
        operation="write",
        target="workspace/candidate/result.json",
        warrant_id=warrant.warrant_id,
        resource_spends=(ResourceSpend("compute_ms", 10),),
        provenance_refs=provenance_refs,
        evidence_refs=evidence_refs,
        requested_at=120.0,
    )
    return evaluate_transition(proposal, warrant, now=120.0)


def _refused_transition():
    warrant = Warrant.issue(
        issuer="human:mikey",
        bearer="organ:research-1",
        scopes=("read:memory/*",),
        issued_at=100.0,
        lifetime_seconds=100.0,
    )
    proposal = TransitionProposal.create(
        actor_id="organ:research-1",
        operation="write",
        target="workspace/candidate/result.json",
        warrant_id=warrant.warrant_id,
        requested_at=120.0,
    )
    return evaluate_transition(proposal, warrant, now=120.0)


def _candidate(
    *,
    claim_key: str = "claim:alpha",
    parent_carriage_ids: tuple[str, ...] = (),
    provenance_refs: tuple[str, ...] | None = None,
):
    transition = _licensed_transition()
    candidate = CarriageCandidate.create(
        payload={"answer": 42, "source": "fixture"},
        claim_key=claim_key,
        transition=transition,
        parent_carriage_ids=parent_carriage_ids,
        provenance_refs=provenance_refs,
        created_at=121.0,
    )
    return transition, candidate


def _admit(transition, candidate):
    return evaluate_carriage(
        candidate,
        transition,
        verified_provenance_refs=set(candidate.provenance_refs),
        admitted_parent_carriage_ids=set(candidate.parent_carriage_ids),
        now=122.0,
    )


def test_payload_hash_is_deterministic_for_equivalent_json() -> None:
    left = payload_hash({"b": 2, "a": 1})
    right = payload_hash({"a": 1, "b": 2})

    assert left == right
    assert len(left) == 64


def test_payload_hash_rejects_non_deterministic_non_json_payload() -> None:
    with pytest.raises(CarriageError):
        payload_hash({"bad": {1, 2, 3}})


def test_licensed_output_with_verified_lineage_is_admitted() -> None:
    transition, candidate = _candidate()

    verdict = _admit(transition, candidate)

    assert verdict.decision == ADMIT
    assert verdict.citation_allowed_at_admission is True
    assert verdict.authority_change == "NONE"
    assert verdict.claims_promoted == 0


def test_broken_provenance_is_quarantined_not_admitted() -> None:
    transition, candidate = _candidate(
        provenance_refs=("prov:source:1", "prov:missing:2")
    )

    verdict = evaluate_carriage(
        candidate,
        transition,
        verified_provenance_refs={"prov:source:1"},
        now=122.0,
    )

    assert verdict.decision == QUARANTINE
    assert verdict.citation_allowed_at_admission is False
    assert verdict.missing_provenance_refs == ("prov:missing:2",)
    assert candidate.payload_hash


def test_missing_parent_admission_quarantines_descendant() -> None:
    transition, candidate = _candidate(parent_carriage_ids=("carriage:parent",))

    verdict = evaluate_carriage(
        candidate,
        transition,
        verified_provenance_refs=set(candidate.provenance_refs),
        admitted_parent_carriage_ids=set(),
        now=122.0,
    )

    assert verdict.decision == QUARANTINE
    assert verdict.missing_parent_carriage_ids == ("carriage:parent",)


def test_output_from_refused_transition_is_refused() -> None:
    transition = _refused_transition()
    candidate = CarriageCandidate.create(
        payload={"answer": "pretty but unauthorized"},
        claim_key="claim:refused",
        transition=transition,
        created_at=121.0,
    )

    verdict = evaluate_carriage(candidate, transition, now=122.0)

    assert verdict.decision == REFUSE
    assert verdict.citation_allowed_at_admission is False
    assert "not licensed" in verdict.reason


def test_lineage_mismatch_is_refused() -> None:
    transition, candidate = _candidate()
    mismatched = replace(candidate, producer_verdict_id="verdict:wrong")

    verdict = evaluate_carriage(
        mismatched,
        transition,
        verified_provenance_refs=set(candidate.provenance_refs),
        now=122.0,
    )

    assert verdict.decision == REFUSE
    assert "lineage" in verdict.reason


def test_admitted_carriage_has_current_citation_right_without_contradiction() -> None:
    transition, candidate = _candidate()
    admission = _admit(transition, candidate)

    citation = evaluate_citation(candidate, admission, contradictions=(), now=123.0)

    assert citation.allowed is True
    assert citation.blocking_contradiction_ids == ()


def test_open_contradiction_revokes_current_citation_without_rewriting_admission() -> None:
    transition, candidate = _candidate(claim_key="claim:temperature")
    admission = _admit(transition, candidate)
    contradiction = ContradictionObject.open(
        claim_key="claim:temperature",
        exhibit_carriage_ids=(candidate.carriage_id, "carriage:other"),
        reason="two admitted carriages make incompatible claims",
        opened_at=123.0,
    )

    citation = evaluate_citation(
        candidate,
        admission,
        contradictions=(contradiction,),
        now=124.0,
    )

    assert admission.decision == ADMIT
    assert admission.citation_allowed_at_admission is True
    assert contradiction.status == OPEN
    assert citation.allowed is False
    assert citation.blocking_contradiction_ids == (contradiction.contradiction_id,)


def test_resolved_contradiction_restores_citation_without_mutating_old_receipts() -> None:
    transition, candidate = _candidate(claim_key="claim:temperature")
    admission = _admit(transition, candidate)
    contradiction = ContradictionObject.open(
        claim_key="claim:temperature",
        exhibit_carriage_ids=(candidate.carriage_id, "carriage:other"),
        reason="incompatible measurements",
        opened_at=123.0,
    )
    blocked = evaluate_citation(
        candidate,
        admission,
        contradictions=(contradiction,),
        now=124.0,
    )

    resolved = contradiction.resolve(
        resolution_kind=HUMAN_SEAL,
        resolver_id="human:mikey",
        authority_ref="seal:operator:123",
        resolution_ref="resolution:measurement:456",
        resolved_at=125.0,
    )
    restored = evaluate_citation(
        candidate,
        admission,
        contradictions=(resolved,),
        now=126.0,
    )

    assert blocked.allowed is False
    assert contradiction.status == OPEN
    assert resolved.status == RESOLVED
    assert admission.decision == ADMIT
    assert restored.allowed is True


def test_contradiction_requires_two_unique_exhibits() -> None:
    with pytest.raises(ContradictionError):
        ContradictionObject.open(
            claim_key="claim:x",
            exhibit_carriage_ids=("carriage:a",),
            reason="not enough exhibits",
        )

    with pytest.raises(ContradictionError):
        ContradictionObject.open(
            claim_key="claim:x",
            exhibit_carriage_ids=("carriage:a", "carriage:a"),
            reason="duplicate exhibits",
        )


def test_contradiction_cannot_auto_resolve_without_external_authority_reference() -> None:
    contradiction = ContradictionObject.open(
        claim_key="claim:x",
        exhibit_carriage_ids=("carriage:a", "carriage:b"),
        reason="conflict",
    )

    with pytest.raises(ContradictionError):
        contradiction.resolve(
            resolution_kind=HUMAN_SEAL,
            resolver_id="human:mikey",
            authority_ref="",
            resolution_ref="resolution:1",
        )

    assert contradiction.status == OPEN


def test_invalid_resolution_kind_is_rejected() -> None:
    contradiction = ContradictionObject.open(
        claim_key="claim:x",
        exhibit_carriage_ids=("carriage:a", "carriage:b"),
        reason="conflict",
    )

    with pytest.raises(ContradictionError):
        contradiction.resolve(
            resolution_kind="MODEL_CONFIDENCE",
            resolver_id="model:best",
            authority_ref="confidence:0.999",
            resolution_ref="resolution:auto",
        )


def test_open_parent_contradiction_quarantines_new_descendant() -> None:
    transition, candidate = _candidate(
        claim_key="claim:derived",
        parent_carriage_ids=("carriage:parent-a",),
    )
    contradiction = ContradictionObject.open(
        claim_key="claim:source",
        exhibit_carriage_ids=("carriage:parent-a", "carriage:parent-b"),
        reason="source parents conflict",
        opened_at=123.0,
    )

    verdict = evaluate_carriage(
        candidate,
        transition,
        verified_provenance_refs=set(candidate.provenance_refs),
        admitted_parent_carriage_ids={"carriage:parent-a"},
        contradictions=(contradiction,),
        now=124.0,
    )

    assert verdict.decision == QUARANTINE
    assert verdict.blocking_contradiction_ids == (contradiction.contradiction_id,)


def test_quarantined_carriage_cannot_be_cited() -> None:
    transition, candidate = _candidate(
        provenance_refs=("prov:source:1", "prov:broken")
    )
    admission = evaluate_carriage(
        candidate,
        transition,
        verified_provenance_refs={"prov:source:1"},
        now=122.0,
    )

    citation = evaluate_citation(candidate, admission, now=123.0)

    assert admission.decision == QUARANTINE
    assert citation.allowed is False
    assert candidate.to_record()["payload_hash"] == candidate.payload_hash
