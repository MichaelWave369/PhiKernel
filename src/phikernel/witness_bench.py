from __future__ import annotations

"""PhiKernel Witness Bench and Crane Fly promotion gate.

The Witness Bench evaluates shadow-runtime evidence without granting authority.
It answers only whether the evidence is sufficient for HUMAN REVIEW.

Promotion is a separate act. In v0.2 the only supported promotion is:

    SHADOW -> ADVISE

and it requires a HumanAuthoritySeal bound to the exact promotion proposal and
Witness Bench report.

ADVISE still has zero steering authority. BOUNDED_CONTROL is reserved for a
future control-witness contract and cannot be reached by this module.

Privilege may collapse automatically back to SHADOW because reducing privilege
is safe; privilege may never expand automatically because success is not
authority.
"""

from dataclasses import dataclass, field, replace
from typing import Any
import hashlib
import json
import time
import uuid

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


WITNESS_VERSION = "0.2.0"

NOT_READY = "NOT_READY"
READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"

SHADOW = "SHADOW"
ADVISE = "ADVISE"
BOUNDED_CONTROL = "BOUNDED_CONTROL"
VALID_PROMOTION_MODES = {SHADOW, ADVISE, BOUNDED_CONTROL}

PROPOSED_ONLY = "PROPOSED_ONLY"
HUMAN_AUTHORIZED_APPLIED = "HUMAN_AUTHORIZED_APPLIED"
COLLAPSED_TO_SHADOW = "COLLAPSED_TO_SHADOW"

CONFLICT_STATES = {DIVERGE, VNEXT_BLOCKS_LEGACY}


class WitnessBenchError(Exception):
    """Base exception for Witness Bench and promotion failures."""


def witness_hash(value: Any) -> str:
    """Hash deterministic JSON witness material."""
    try:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WitnessBenchError(
            "witness material must be deterministically JSON-serializable"
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_hash(name: str, value: str) -> None:
    if len(value) != 64:
        raise WitnessBenchError(f"{name} must be SHA-256 hex")
    try:
        int(value, 16)
    except ValueError as exc:
        raise WitnessBenchError(f"{name} must be hexadecimal") from exc


@dataclass(frozen=True)
class BehavioralWitnessCase:
    """One behavioral-reality observation tied to a shadow routing receipt.

    The case captures the required behavioral-reality surfaces:
    INPUT, STATE_BEFORE, TRIGGER, STATE_AFTER, OBSERVABLE_EFFECT,
    FAILURE_CASE, and RECEIPT.

    Behavioral effect is not self-declared. It is derived by comparing the
    observed effect with the mechanism enabled vs the control observation with
    that mechanism removed/disabled.
    """

    case_id: str
    shadow_receipt_id: str
    think_bundle_hash: str
    comparison_state: str
    legacy_safe_to_proceed: bool
    legacy_candidate_admissible_in_vnext: bool | None
    input_hash: str
    state_before_hash: str
    trigger_ref: str
    state_after_hash: str
    mechanism_enabled_effect_hash: str
    mechanism_removed_effect_hash: str
    failure_case_hash: str
    replay_verified: bool
    invariants_preserved: bool
    conflict_review_ref: str | None
    observed_at: float
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    citation_law_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = WITNESS_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("case_id", self.case_id),
            ("shadow_receipt_id", self.shadow_receipt_id),
            ("trigger_ref", self.trigger_ref),
        ):
            if not value.strip():
                raise WitnessBenchError(f"{name} must be non-empty")

        for name, value in (
            ("think_bundle_hash", self.think_bundle_hash),
            ("input_hash", self.input_hash),
            ("state_before_hash", self.state_before_hash),
            ("state_after_hash", self.state_after_hash),
            ("mechanism_enabled_effect_hash", self.mechanism_enabled_effect_hash),
            ("mechanism_removed_effect_hash", self.mechanism_removed_effect_hash),
            ("failure_case_hash", self.failure_case_hash),
        ):
            _require_hash(name, value)

        if self.comparison_state not in {
            AGREE,
            DIVERGE,
            VNEXT_BLOCKS_LEGACY,
            VNEXT_NO_ROUTE,
            VNEXT_ERROR,
            LEGACY_UNMAPPED,
        }:
            raise WitnessBenchError("invalid shadow comparison state")

        if (
            self.conflict_review_ref is not None
            and not self.conflict_review_ref.strip()
        ):
            raise WitnessBenchError(
                "conflict_review_ref must be non-empty when provided"
            )

        for name, value in (
            ("authority_change", self.authority_change),
            ("warrant_change", self.warrant_change),
            ("citation_law_change", self.citation_law_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise WitnessBenchError(f"{name} must remain NONE")

    @classmethod
    def create(
        cls,
        receipt: ShadowComparisonReceipt,
        *,
        input_value: Any,
        state_before: Any,
        trigger_ref: str,
        state_after: Any,
        mechanism_enabled_effect: Any,
        mechanism_removed_effect: Any,
        failure_case: Any,
        replay_verified: bool,
        invariants_preserved: bool,
        conflict_review_ref: str | None = None,
        observed_at: float | None = None,
    ) -> "BehavioralWitnessCase":
        return cls(
            case_id=str(uuid.uuid4()),
            shadow_receipt_id=receipt.receipt_id,
            think_bundle_hash=receipt.think_bundle_hash,
            comparison_state=receipt.comparison_state,
            legacy_safe_to_proceed=receipt.legacy_safe_to_proceed,
            legacy_candidate_admissible_in_vnext=(
                receipt.legacy_candidate_admissible_in_vnext
            ),
            input_hash=witness_hash(input_value),
            state_before_hash=witness_hash(state_before),
            trigger_ref=trigger_ref,
            state_after_hash=witness_hash(state_after),
            mechanism_enabled_effect_hash=witness_hash(
                mechanism_enabled_effect
            ),
            mechanism_removed_effect_hash=witness_hash(
                mechanism_removed_effect
            ),
            failure_case_hash=witness_hash(failure_case),
            replay_verified=bool(replay_verified),
            invariants_preserved=bool(invariants_preserved),
            conflict_review_ref=conflict_review_ref,
            observed_at=(
                time.time() if observed_at is None else float(observed_at)
            ),
        )

    @property
    def behavioral_effect_observed(self) -> bool:
        return (
            self.mechanism_enabled_effect_hash
            != self.mechanism_removed_effect_hash
        )

    @property
    def conflict_reviewed(self) -> bool:
        if self.comparison_state not in CONFLICT_STATES:
            return True
        return bool((self.conflict_review_ref or "").strip())

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "case_id": self.case_id,
            "shadow_receipt_id": self.shadow_receipt_id,
            "think_bundle_hash": self.think_bundle_hash,
            "comparison_state": self.comparison_state,
            "legacy_safe_to_proceed": self.legacy_safe_to_proceed,
            "legacy_candidate_admissible_in_vnext": (
                self.legacy_candidate_admissible_in_vnext
            ),
            "input_hash": self.input_hash,
            "state_before_hash": self.state_before_hash,
            "trigger_ref": self.trigger_ref,
            "state_after_hash": self.state_after_hash,
            "mechanism_enabled_effect_hash": (
                self.mechanism_enabled_effect_hash
            ),
            "mechanism_removed_effect_hash": (
                self.mechanism_removed_effect_hash
            ),
            "behavioral_effect_observed": self.behavioral_effect_observed,
            "failure_case_hash": self.failure_case_hash,
            "replay_verified": self.replay_verified,
            "invariants_preserved": self.invariants_preserved,
            "conflict_review_ref": self.conflict_review_ref,
            "conflict_reviewed": self.conflict_reviewed,
            "observed_at": self.observed_at,
            "authority_change": self.authority_change,
            "warrant_change": self.warrant_change,
            "citation_law_change": self.citation_law_change,
            "constitutional_change": self.constitutional_change,
        }


@dataclass(frozen=True)
class WitnessBenchPolicy:
    min_receipts: int = 20
    min_distinct_bundles: int = 10
    max_vnext_error_rate: float = 0.0
    min_candidate_coverage_rate: float = 0.95
    min_replay_verified_rate: float = 1.0
    min_invariant_preservation_rate: float = 1.0
    min_behavioral_reality_rate: float = 0.80
    min_conflict_review_rate: float = 1.0

    def __post_init__(self) -> None:
        if self.min_receipts <= 0:
            raise WitnessBenchError("min_receipts must be > 0")
        if self.min_distinct_bundles <= 0:
            raise WitnessBenchError("min_distinct_bundles must be > 0")
        if self.min_distinct_bundles > self.min_receipts:
            raise WitnessBenchError(
                "min_distinct_bundles may not exceed min_receipts"
            )

        for name, value in (
            ("max_vnext_error_rate", self.max_vnext_error_rate),
            ("min_candidate_coverage_rate", self.min_candidate_coverage_rate),
            ("min_replay_verified_rate", self.min_replay_verified_rate),
            (
                "min_invariant_preservation_rate",
                self.min_invariant_preservation_rate,
            ),
            ("min_behavioral_reality_rate", self.min_behavioral_reality_rate),
            ("min_conflict_review_rate", self.min_conflict_review_rate),
        ):
            if not (0.0 <= value <= 1.0):
                raise WitnessBenchError(f"{name} must be in [0, 1]")

    def to_record(self) -> dict[str, Any]:
        return {
            "min_receipts": self.min_receipts,
            "min_distinct_bundles": self.min_distinct_bundles,
            "max_vnext_error_rate": self.max_vnext_error_rate,
            "min_candidate_coverage_rate": self.min_candidate_coverage_rate,
            "min_replay_verified_rate": self.min_replay_verified_rate,
            "min_invariant_preservation_rate": (
                self.min_invariant_preservation_rate
            ),
            "min_behavioral_reality_rate": self.min_behavioral_reality_rate,
            "min_conflict_review_rate": self.min_conflict_review_rate,
        }


@dataclass(frozen=True)
class WitnessBenchReport:
    report_id: str
    status: str
    total_receipts: int
    distinct_bundle_count: int
    agreement_rate: float
    divergence_rate: float
    hard_block_discovery_rate: float
    no_route_rate: float
    vnext_error_rate: float
    candidate_coverage_rate: float
    replay_verified_rate: float
    invariant_preservation_rate: float
    behavioral_reality_rate: float
    conflict_review_rate: float
    blocker_reasons: tuple[str, ...]
    case_ids: tuple[str, ...]
    shadow_receipt_ids: tuple[str, ...]
    policy: WitnessBenchPolicy
    evaluated_at: float
    report_hash: str
    authority_change: str = "NONE"
    routing_mode_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = WITNESS_VERSION

    def __post_init__(self) -> None:
        if self.status not in {NOT_READY, READY_FOR_HUMAN_REVIEW}:
            raise WitnessBenchError("invalid witness report status")
        if self.total_receipts < 0 or self.distinct_bundle_count < 0:
            raise WitnessBenchError("witness counts must be >= 0")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise WitnessBenchError("case_ids must be unique")
        if len(self.shadow_receipt_ids) != len(set(self.shadow_receipt_ids)):
            raise WitnessBenchError("shadow_receipt_ids must be unique")
        if len(self.case_ids) != self.total_receipts:
            raise WitnessBenchError(
                "total_receipts must equal number of witness cases"
            )
        if len(self.shadow_receipt_ids) != self.total_receipts:
            raise WitnessBenchError(
                "each witness case must reference a unique shadow receipt"
            )

        for name, value in (
            ("agreement_rate", self.agreement_rate),
            ("divergence_rate", self.divergence_rate),
            ("hard_block_discovery_rate", self.hard_block_discovery_rate),
            ("no_route_rate", self.no_route_rate),
            ("vnext_error_rate", self.vnext_error_rate),
            ("candidate_coverage_rate", self.candidate_coverage_rate),
            ("replay_verified_rate", self.replay_verified_rate),
            ("invariant_preservation_rate", self.invariant_preservation_rate),
            ("behavioral_reality_rate", self.behavioral_reality_rate),
            ("conflict_review_rate", self.conflict_review_rate),
        ):
            if not (0.0 <= value <= 1.0):
                raise WitnessBenchError(f"{name} must be in [0, 1]")

        _require_hash("report_hash", self.report_hash)

        if self.status == READY_FOR_HUMAN_REVIEW and self.blocker_reasons:
            raise WitnessBenchError(
                "ready report may not contain blocker_reasons"
            )
        if self.status == NOT_READY and not self.blocker_reasons:
            raise WitnessBenchError(
                "not-ready report requires blocker_reasons"
            )

        for name, value in (
            ("authority_change", self.authority_change),
            ("routing_mode_change", self.routing_mode_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise WitnessBenchError(f"{name} must remain NONE")

    @property
    def ready_for_human_review(self) -> bool:
        return self.status == READY_FOR_HUMAN_REVIEW

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "report_id": self.report_id,
            "status": self.status,
            "total_receipts": self.total_receipts,
            "distinct_bundle_count": self.distinct_bundle_count,
            "agreement_rate": self.agreement_rate,
            "divergence_rate": self.divergence_rate,
            "hard_block_discovery_rate": self.hard_block_discovery_rate,
            "no_route_rate": self.no_route_rate,
            "vnext_error_rate": self.vnext_error_rate,
            "candidate_coverage_rate": self.candidate_coverage_rate,
            "replay_verified_rate": self.replay_verified_rate,
            "invariant_preservation_rate": self.invariant_preservation_rate,
            "behavioral_reality_rate": self.behavioral_reality_rate,
            "conflict_review_rate": self.conflict_review_rate,
            "blocker_reasons": list(self.blocker_reasons),
            "case_ids": list(self.case_ids),
            "shadow_receipt_ids": list(self.shadow_receipt_ids),
            "policy": self.policy.to_record(),
            "evaluated_at": self.evaluated_at,
            "report_hash": self.report_hash,
            "authority_change": self.authority_change,
            "routing_mode_change": self.routing_mode_change,
            "constitutional_change": self.constitutional_change,
        }


def evaluate_witness_bench(
    cases: tuple[BehavioralWitnessCase, ...] | list[BehavioralWitnessCase],
    *,
    policy: WitnessBenchPolicy | None = None,
    evaluated_at: float | None = None,
) -> WitnessBenchReport:
    active_policy = policy or WitnessBenchPolicy()
    case_tuple = tuple(cases)
    timestamp = time.time() if evaluated_at is None else float(evaluated_at)

    case_ids = tuple(case.case_id for case in case_tuple)
    receipt_ids = tuple(case.shadow_receipt_id for case in case_tuple)
    if len(case_ids) != len(set(case_ids)):
        raise WitnessBenchError(
            "same witness case may not be counted more than once"
        )
    if len(receipt_ids) != len(set(receipt_ids)):
        raise WitnessBenchError(
            "same shadow receipt may not be counted more than once"
        )

    total = len(case_tuple)
    distinct_bundles = len({case.think_bundle_hash for case in case_tuple})

    def rate(count: int) -> float:
        return 0.0 if total == 0 else count / total

    agreement_count = sum(
        case.comparison_state == AGREE for case in case_tuple
    )
    divergence_count = sum(
        case.comparison_state == DIVERGE for case in case_tuple
    )
    hard_block_count = sum(
        case.comparison_state == VNEXT_BLOCKS_LEGACY
        for case in case_tuple
    )
    no_route_count = sum(
        case.comparison_state == VNEXT_NO_ROUTE
        for case in case_tuple
    )
    error_count = sum(
        case.comparison_state == VNEXT_ERROR for case in case_tuple
    )
    covered_count = sum(
        case.legacy_candidate_admissible_in_vnext is not None
        for case in case_tuple
    )
    replay_count = sum(case.replay_verified for case in case_tuple)
    invariant_count = sum(case.invariants_preserved for case in case_tuple)
    behavioral_count = sum(
        case.behavioral_effect_observed for case in case_tuple
    )

    conflict_cases = tuple(
        case for case in case_tuple
        if case.comparison_state in CONFLICT_STATES
    )
    reviewed_conflicts = sum(
        case.conflict_reviewed for case in conflict_cases
    )
    conflict_review_rate = (
        1.0
        if not conflict_cases
        else reviewed_conflicts / len(conflict_cases)
    )

    metrics = {
        "agreement_rate": rate(agreement_count),
        "divergence_rate": rate(divergence_count),
        "hard_block_discovery_rate": rate(hard_block_count),
        "no_route_rate": rate(no_route_count),
        "vnext_error_rate": rate(error_count),
        "candidate_coverage_rate": rate(covered_count),
        "replay_verified_rate": rate(replay_count),
        "invariant_preservation_rate": rate(invariant_count),
        "behavioral_reality_rate": rate(behavioral_count),
        "conflict_review_rate": conflict_review_rate,
    }

    blockers: list[str] = []
    if total < active_policy.min_receipts:
        blockers.append(
            f"insufficient receipts: {total} < {active_policy.min_receipts}"
        )
    if distinct_bundles < active_policy.min_distinct_bundles:
        blockers.append(
            "insufficient distinct bundles: "
            f"{distinct_bundles} < {active_policy.min_distinct_bundles}"
        )
    if metrics["vnext_error_rate"] > active_policy.max_vnext_error_rate:
        blockers.append(
            "vNext error rate exceeds policy: "
            f"{metrics['vnext_error_rate']:.6f} > "
            f"{active_policy.max_vnext_error_rate:.6f}"
        )
    if (
        metrics["candidate_coverage_rate"]
        < active_policy.min_candidate_coverage_rate
    ):
        blockers.append(
            "candidate coverage below policy: "
            f"{metrics['candidate_coverage_rate']:.6f} < "
            f"{active_policy.min_candidate_coverage_rate:.6f}"
        )
    if (
        metrics["replay_verified_rate"]
        < active_policy.min_replay_verified_rate
    ):
        blockers.append(
            "deterministic replay rate below policy: "
            f"{metrics['replay_verified_rate']:.6f} < "
            f"{active_policy.min_replay_verified_rate:.6f}"
        )
    if (
        metrics["invariant_preservation_rate"]
        < active_policy.min_invariant_preservation_rate
    ):
        blockers.append(
            "invariant preservation below policy: "
            f"{metrics['invariant_preservation_rate']:.6f} < "
            f"{active_policy.min_invariant_preservation_rate:.6f}"
        )
    if (
        metrics["behavioral_reality_rate"]
        < active_policy.min_behavioral_reality_rate
    ):
        blockers.append(
            "behavioral reality rate below policy: "
            f"{metrics['behavioral_reality_rate']:.6f} < "
            f"{active_policy.min_behavioral_reality_rate:.6f}"
        )
    if (
        metrics["conflict_review_rate"]
        < active_policy.min_conflict_review_rate
    ):
        blockers.append(
            "conflict review rate below policy: "
            f"{metrics['conflict_review_rate']:.6f} < "
            f"{active_policy.min_conflict_review_rate:.6f}"
        )

    status = (
        READY_FOR_HUMAN_REVIEW if not blockers else NOT_READY
    )

    report_payload = {
        "status": status,
        "total_receipts": total,
        "distinct_bundle_count": distinct_bundles,
        **metrics,
        "blocker_reasons": blockers,
        "case_ids": list(case_ids),
        "shadow_receipt_ids": list(receipt_ids),
        "policy": active_policy.to_record(),
        "evaluated_at": timestamp,
    }
    digest = witness_hash(report_payload)

    return WitnessBenchReport(
        report_id=str(uuid.uuid4()),
        status=status,
        total_receipts=total,
        distinct_bundle_count=distinct_bundles,
        agreement_rate=metrics["agreement_rate"],
        divergence_rate=metrics["divergence_rate"],
        hard_block_discovery_rate=metrics["hard_block_discovery_rate"],
        no_route_rate=metrics["no_route_rate"],
        vnext_error_rate=metrics["vnext_error_rate"],
        candidate_coverage_rate=metrics["candidate_coverage_rate"],
        replay_verified_rate=metrics["replay_verified_rate"],
        invariant_preservation_rate=(
            metrics["invariant_preservation_rate"]
        ),
        behavioral_reality_rate=metrics["behavioral_reality_rate"],
        conflict_review_rate=metrics["conflict_review_rate"],
        blocker_reasons=tuple(blockers),
        case_ids=case_ids,
        shadow_receipt_ids=receipt_ids,
        policy=active_policy,
        evaluated_at=timestamp,
        report_hash=digest,
    )


@dataclass(frozen=True)
class PromotionState:
    mode: str
    revision: int
    last_receipt_id: str | None = None
    authorized_by_seal_id: str | None = None
    version: str = WITNESS_VERSION

    def __post_init__(self) -> None:
        if self.mode not in VALID_PROMOTION_MODES:
            raise WitnessBenchError("invalid promotion mode")
        if self.revision < 0:
            raise WitnessBenchError("promotion revision must be >= 0")
        if self.mode == SHADOW and self.revision == 0:
            if self.authorized_by_seal_id is not None:
                raise WitnessBenchError(
                    "genesis SHADOW state may not have an authorization seal"
                )
        if self.mode in {ADVISE, BOUNDED_CONTROL}:
            if not (self.authorized_by_seal_id or "").strip():
                raise WitnessBenchError(
                    "promoted routing mode requires human authorization seal"
                )

    @classmethod
    def genesis(cls) -> "PromotionState":
        return cls(mode=SHADOW, revision=0)

    @property
    def may_observe(self) -> bool:
        return True

    @property
    def may_advise(self) -> bool:
        return self.mode in {ADVISE, BOUNDED_CONTROL}

    @property
    def may_steer(self) -> bool:
        return self.mode == BOUNDED_CONTROL


@dataclass(frozen=True)
class PromotionProposal:
    proposal_id: str
    base_mode: str
    base_revision: int
    target_mode: str
    witness_report_id: str
    witness_report_hash: str
    proposer_id: str
    reason: str
    created_at: float
    authority_change: str = "NONE"
    routing_mode_change: str = PROPOSED_ONLY
    steering_authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = WITNESS_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("proposal_id", self.proposal_id),
            ("base_mode", self.base_mode),
            ("target_mode", self.target_mode),
            ("witness_report_id", self.witness_report_id),
            ("proposer_id", self.proposer_id),
            ("reason", self.reason),
        ):
            if not value.strip():
                raise WitnessBenchError(f"{name} must be non-empty")
        if self.base_mode not in VALID_PROMOTION_MODES:
            raise WitnessBenchError("invalid base_mode")
        if self.target_mode not in VALID_PROMOTION_MODES:
            raise WitnessBenchError("invalid target_mode")
        _require_hash("witness_report_hash", self.witness_report_hash)
        if self.base_revision < 0:
            raise WitnessBenchError("base_revision must be >= 0")
        if self.authority_change != "NONE":
            raise WitnessBenchError(
                "promotion proposal may not change authority"
            )
        if self.routing_mode_change != PROPOSED_ONLY:
            raise WitnessBenchError(
                "promotion proposal must remain PROPOSED_ONLY"
            )
        if self.steering_authority_change != "NONE":
            raise WitnessBenchError(
                "promotion proposal may not grant steering authority"
            )
        if self.constitutional_change != "NONE":
            raise WitnessBenchError(
                "promotion proposal may not change constitution"
            )


@dataclass(frozen=True)
class PromotionAuthorizationReceipt:
    receipt_id: str
    proposal_id: str
    witness_report_id: str
    witness_report_hash: str
    prior_mode: str
    resulting_mode: str
    prior_revision: int
    resulting_revision: int
    human_seal_id: str
    human_actor_id: str
    authority_ref: str
    applied_at: float
    human_seal_record_json: str = ""
    authority_change: str = "NONE"
    routing_mode_change: str = HUMAN_AUTHORIZED_APPLIED
    steering_authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = WITNESS_VERSION

    def __post_init__(self) -> None:
        if self.resulting_revision != self.prior_revision + 1:
            raise WitnessBenchError(
                "promotion authorization must advance revision exactly once"
            )
        _require_hash("witness_report_hash", self.witness_report_hash)
        if not self.human_seal_record_json.strip():
            raise WitnessBenchError(
                "promotion authorization receipt requires signed human seal proof"
            )
        try:
            seal_record = json.loads(self.human_seal_record_json)
        except json.JSONDecodeError as exc:
            raise WitnessBenchError(
                "promotion authorization human seal proof must be valid JSON"
            ) from exc
        if not isinstance(seal_record, dict):
            raise WitnessBenchError(
                "promotion authorization human seal proof must be a JSON object"
            )
        if seal_record.get("seal_id") != self.human_seal_id:
            raise WitnessBenchError(
                "promotion authorization seal proof id mismatch"
            )
        if seal_record.get("actor_id") != self.human_actor_id:
            raise WitnessBenchError(
                "promotion authorization seal proof actor mismatch"
            )
        if seal_record.get("authority_ref") != self.authority_ref:
            raise WitnessBenchError(
                "promotion authorization seal proof authority_ref mismatch"
            )
        if not seal_record.get("signature"):
            raise WitnessBenchError(
                "promotion authorization seal proof is unsigned"
            )
        if self.authority_change != "NONE":
            raise WitnessBenchError(
                "ADVISE promotion does not grant execution authority"
            )
        if self.routing_mode_change != HUMAN_AUTHORIZED_APPLIED:
            raise WitnessBenchError(
                "routing mode promotion must be human-authorized"
            )
        if self.steering_authority_change != "NONE":
            raise WitnessBenchError(
                "ADVISE promotion may not grant steering authority"
            )
        if self.constitutional_change != "NONE":
            raise WitnessBenchError(
                "promotion may not change constitution"
            )


@dataclass(frozen=True)
class PromotionCollapseReceipt:
    receipt_id: str
    prior_mode: str
    resulting_mode: str
    prior_revision: int
    resulting_revision: int
    source_ref: str
    reason: str
    collapsed_at: float
    authority_change: str = "NONE"
    routing_mode_change: str = COLLAPSED_TO_SHADOW
    steering_authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = WITNESS_VERSION


def propose_promotion(
    state: PromotionState,
    report: WitnessBenchReport,
    *,
    target_mode: str,
    proposer_id: str,
    reason: str,
    created_at: float | None = None,
) -> PromotionProposal:
    if not report.ready_for_human_review:
        raise WitnessBenchError(
            "witness report is not ready for human review"
        )
    if state.mode != SHADOW or target_mode != ADVISE:
        raise WitnessBenchError(
            "v0.2 supports only SHADOW -> ADVISE promotion; "
            "BOUNDED_CONTROL requires a future control-witness contract"
        )
    if not proposer_id.strip() or not reason.strip():
        raise WitnessBenchError(
            "proposer_id and reason must be non-empty"
        )

    return PromotionProposal(
        proposal_id=str(uuid.uuid4()),
        base_mode=state.mode,
        base_revision=state.revision,
        target_mode=target_mode,
        witness_report_id=report.report_id,
        witness_report_hash=report.report_hash,
        proposer_id=proposer_id,
        reason=reason,
        created_at=time.time() if created_at is None else float(created_at),
    )


def authorize_promotion(
    state: PromotionState,
    proposal: PromotionProposal,
    report: WitnessBenchReport,
    human_seal: HumanAuthoritySeal,
    *,
    anchor_service: Any,
    applied_at: float | None = None,
) -> tuple[PromotionState, PromotionAuthorizationReceipt]:
    if state.mode != proposal.base_mode:
        raise WitnessBenchError(
            "promotion proposal base_mode does not match current state"
        )
    if state.revision != proposal.base_revision:
        raise WitnessBenchError(
            "promotion proposal base_revision is stale"
        )
    if proposal.target_mode != ADVISE:
        raise WitnessBenchError(
            "v0.2 authorization may promote only to ADVISE"
        )
    if state.mode != SHADOW:
        raise WitnessBenchError(
            "v0.2 authorization requires current SHADOW mode"
        )
    if not report.ready_for_human_review:
        raise WitnessBenchError(
            "witness report is no longer ready for human review"
        )
    if proposal.witness_report_id != report.report_id:
        raise WitnessBenchError(
            "promotion proposal references different witness report"
        )
    if proposal.witness_report_hash != report.report_hash:
        raise WitnessBenchError(
            "promotion proposal witness report hash mismatch"
        )
    if human_seal.issued_at < proposal.created_at:
        raise WitnessBenchError(
            "human authorization seal may not predate promotion proposal"
        )

    valid_signature, signature_reason = human_seal.verify_anchor(
        anchor_service
    )
    if not valid_signature:
        raise WitnessBenchError(
            "promotion requires Anchor-signed human authority seal: "
            f"{signature_reason}"
        )

    metadata = human_seal.metadata
    if metadata.get("promotion_proposal_id") != proposal.proposal_id:
        raise WitnessBenchError(
            "human seal is not bound to this promotion proposal"
        )
    if metadata.get("witness_report_hash") != report.report_hash:
        raise WitnessBenchError(
            "human seal is not bound to this witness report"
        )
    if metadata.get("target_mode") != proposal.target_mode:
        raise WitnessBenchError(
            "human seal target_mode does not match proposal"
        )

    timestamp = time.time() if applied_at is None else float(applied_at)
    receipt_id = str(uuid.uuid4())
    updated = PromotionState(
        mode=ADVISE,
        revision=state.revision + 1,
        last_receipt_id=receipt_id,
        authorized_by_seal_id=human_seal.seal_id,
    )
    receipt = PromotionAuthorizationReceipt(
        receipt_id=receipt_id,
        proposal_id=proposal.proposal_id,
        witness_report_id=report.report_id,
        witness_report_hash=report.report_hash,
        prior_mode=state.mode,
        resulting_mode=updated.mode,
        prior_revision=state.revision,
        resulting_revision=updated.revision,
        human_seal_id=human_seal.seal_id,
        human_actor_id=human_seal.actor_id,
        authority_ref=human_seal.authority_ref,
        applied_at=timestamp,
        human_seal_record_json=json.dumps(
            human_seal.to_record(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    )
    return updated, receipt


def collapse_to_shadow(
    state: PromotionState,
    *,
    source_ref: str,
    reason: str,
    collapsed_at: float | None = None,
) -> tuple[PromotionState, PromotionCollapseReceipt]:
    """Reduce routing privilege without requiring a promotion grant.

    Automatic privilege reduction is permitted. Automatic privilege expansion
    is not.
    """
    if state.mode == SHADOW:
        raise WitnessBenchError("routing mode is already SHADOW")
    if not source_ref.strip() or not reason.strip():
        raise WitnessBenchError(
            "source_ref and reason must be non-empty"
        )

    timestamp = time.time() if collapsed_at is None else float(collapsed_at)
    receipt_id = str(uuid.uuid4())
    updated = PromotionState(
        mode=SHADOW,
        revision=state.revision + 1,
        last_receipt_id=receipt_id,
        authorized_by_seal_id=None,
    )
    receipt = PromotionCollapseReceipt(
        receipt_id=receipt_id,
        prior_mode=state.mode,
        resulting_mode=SHADOW,
        prior_revision=state.revision,
        resulting_revision=updated.revision,
        source_ref=source_ref,
        reason=reason,
        collapsed_at=timestamp,
    )
    return updated, receipt
