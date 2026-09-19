from __future__ import annotations

"""PhiKernel Control Witness Contract.

This module introduces the first path from ADVISE to BOUNDED_CONTROL.

A bounded-control grant is never inferred from success. It requires:
1. an explicit immutable control contract,
2. rehearsal evidence covering required safety cases,
3. a ready Control Witness report,
4. an exact promotion proposal,
5. a HumanAuthoritySeal bound to the proposal, report, contract, and target mode.

Even after promotion, control is not general. A dedicated Warrant is issued for
only the contract's exact actions/resources/lifetime. Each action is licensed
through the normal governed-transition evaluator. Sessions enforce action count,
clock budget, resource budget, evidence, post-action receipt, rollback, human
veto, expiry, and terminal failure semantics.
"""

from dataclasses import dataclass, field, replace
from typing import Any
import time
import uuid

from phikernel.mutability import HumanAuthoritySeal
from phikernel.transition import (
    LICENSE,
    ResourceSpend,
    TransitionEvaluation,
    TransitionProposal,
    evaluate_transition,
)
from phikernel.warrant import ResourceBudget, Warrant
from phikernel.witness_bench import (
    ADVISE,
    BOUNDED_CONTROL,
    SHADOW,
    PromotionState,
    collapse_to_shadow,
    witness_hash,
)


CONTROL_WITNESS_VERSION = "0.2.0"

NOT_READY = "NOT_READY"
READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"

PROPOSED_ONLY = "PROPOSED_ONLY"
HUMAN_AUTHORIZED_APPLIED = "HUMAN_AUTHORIZED_APPLIED"
HUMAN_AUTHORIZED_BOUNDED = "HUMAN_AUTHORIZED_BOUNDED"
HUMAN_AUTHORIZED_BOUNDED_LEASE = "HUMAN_AUTHORIZED_BOUNDED_LEASE"
TERMINAL_STOP = "TERMINAL_STOP"
VETO_STOP = "VETO_STOP"
NORMAL_STOP = "NORMAL_STOP"

HAPPY_PATH = "HAPPY_PATH"
SCOPE_DENIAL = "SCOPE_DENIAL"
RESOURCE_DENIAL = "RESOURCE_DENIAL"
ACTION_LIMIT_DENIAL = "ACTION_LIMIT_DENIAL"
CLOCK_LIMIT_DENIAL = "CLOCK_LIMIT_DENIAL"
EXPIRY_DENIAL = "EXPIRY_DENIAL"
HUMAN_VETO = "HUMAN_VETO"
ROLLBACK = "ROLLBACK"
RECEIPT_GATE = "RECEIPT_GATE"
TERMINAL_FAILURE = "TERMINAL_FAILURE"

REQUIRED_CONTROL_CASE_KINDS = frozenset(
    {
        HAPPY_PATH,
        SCOPE_DENIAL,
        RESOURCE_DENIAL,
        ACTION_LIMIT_DENIAL,
        CLOCK_LIMIT_DENIAL,
        EXPIRY_DENIAL,
        HUMAN_VETO,
        ROLLBACK,
        RECEIPT_GATE,
        TERMINAL_FAILURE,
    }
)


class ControlWitnessError(Exception):
    """Base exception for bounded-control witness/governance failures."""


def _require_nonempty(name: str, value: str) -> None:
    if not value.strip():
        raise ControlWitnessError(f"{name} must be non-empty")


def _require_hash(name: str, value: str) -> None:
    if len(value) != 64:
        raise ControlWitnessError(f"{name} must be SHA-256 hex")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ControlWitnessError(f"{name} must be hexadecimal") from exc


@dataclass(frozen=True)
class ControlActionRule:
    rule_id: str
    operation: str
    target: str
    max_count: int
    rollback_required: bool = True

    def __post_init__(self) -> None:
        _require_nonempty("rule_id", self.rule_id)
        _require_nonempty("operation", self.operation)
        _require_nonempty("target", self.target)
        if self.max_count <= 0:
            raise ControlWitnessError("action rule max_count must be > 0")
        if any(char in self.scope for char in "*?[]"):
            raise ControlWitnessError(
                "v0.2 bounded-control scopes must be exact; glob metacharacters are forbidden"
            )
        if not self.rollback_required:
            raise ControlWitnessError(
                "v0.2 bounded-control actions require a declared rollback path"
            )

    @property
    def scope(self) -> str:
        return f"{self.operation}:{self.target}"

    def to_record(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "operation": self.operation,
            "target": self.target,
            "scope": self.scope,
            "max_count": self.max_count,
            "rollback_required": self.rollback_required,
        }


@dataclass(frozen=True)
class ControlContract:
    contract_id: str
    actor_id: str
    action_rules: tuple[ControlActionRule, ...]
    resource_limits: tuple[ResourceBudget, ...]
    max_total_actions: int
    max_clock_ticks: float
    lifetime_seconds: float
    required_evidence_refs: tuple[str, ...]
    rollback_required: bool = True
    interruptible: bool = True
    human_veto_required: bool = True
    post_action_receipt_required: bool = True
    terminal_failure_on_violation: bool = True
    terminal_failure_on_action_failure: bool = True
    created_at: float = field(default_factory=time.time)
    version: str = CONTROL_WITNESS_VERSION

    def __post_init__(self) -> None:
        _require_nonempty("contract_id", self.contract_id)
        _require_nonempty("actor_id", self.actor_id)
        if not self.action_rules:
            raise ControlWitnessError(
                "control contract requires at least one action rule"
            )

        rule_ids = [rule.rule_id for rule in self.action_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ControlWitnessError("control action rule ids must be unique")

        scopes = [rule.scope for rule in self.action_rules]
        if len(scopes) != len(set(scopes)):
            raise ControlWitnessError(
                "v0.2 control contract allows one rule per exact scope"
            )

        budget_kinds = [budget.kind for budget in self.resource_limits]
        if len(budget_kinds) != len(set(budget_kinds)):
            raise ControlWitnessError(
                "control resource limit kinds must be unique"
            )
        if any(budget.spent != 0 for budget in self.resource_limits):
            raise ControlWitnessError(
                "control resource limits must begin with spent=0"
            )

        if self.max_total_actions <= 0:
            raise ControlWitnessError("max_total_actions must be > 0")
        if self.max_total_actions > sum(
            rule.max_count for rule in self.action_rules
        ):
            raise ControlWitnessError(
                "max_total_actions may not exceed sum of rule max_count values"
            )
        if self.max_clock_ticks <= 0:
            raise ControlWitnessError("max_clock_ticks must be > 0")
        if self.lifetime_seconds <= 0:
            raise ControlWitnessError("lifetime_seconds must be > 0")

        if any(not ref.strip() for ref in self.required_evidence_refs):
            raise ControlWitnessError(
                "required_evidence_refs may not contain empty values"
            )
        if len(self.required_evidence_refs) != len(
            set(self.required_evidence_refs)
        ):
            raise ControlWitnessError(
                "required_evidence_refs may not contain duplicates"
            )

        for name, value in (
            ("rollback_required", self.rollback_required),
            ("interruptible", self.interruptible),
            ("human_veto_required", self.human_veto_required),
            (
                "post_action_receipt_required",
                self.post_action_receipt_required,
            ),
            (
                "terminal_failure_on_violation",
                self.terminal_failure_on_violation,
            ),
            (
                "terminal_failure_on_action_failure",
                self.terminal_failure_on_action_failure,
            ),
        ):
            if not value:
                raise ControlWitnessError(
                    f"v0.2 safe profile requires {name}=True"
                )

    @classmethod
    def create(
        cls,
        *,
        actor_id: str,
        action_rules: tuple[ControlActionRule, ...] | list[ControlActionRule],
        resource_limits: tuple[ResourceBudget, ...] | list[ResourceBudget],
        max_total_actions: int,
        max_clock_ticks: float,
        lifetime_seconds: float,
        required_evidence_refs: tuple[str, ...] | list[str] = (),
        created_at: float | None = None,
    ) -> "ControlContract":
        return cls(
            contract_id=f"control-contract:{uuid.uuid4()}",
            actor_id=actor_id,
            action_rules=tuple(action_rules),
            resource_limits=tuple(resource_limits),
            max_total_actions=int(max_total_actions),
            max_clock_ticks=float(max_clock_ticks),
            lifetime_seconds=float(lifetime_seconds),
            required_evidence_refs=tuple(required_evidence_refs),
            created_at=(
                time.time() if created_at is None else float(created_at)
            ),
        )

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(rule.scope for rule in self.action_rules)

    @property
    def contract_hash(self) -> str:
        return witness_hash(self.to_record())

    def rule_for(self, operation: str, target: str) -> ControlActionRule | None:
        scope = f"{operation}:{target}"
        return next(
            (rule for rule in self.action_rules if rule.scope == scope),
            None,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "contract_id": self.contract_id,
            "actor_id": self.actor_id,
            "action_rules": [
                rule.to_record() for rule in self.action_rules
            ],
            "resource_limits": [
                {
                    "kind": budget.kind,
                    "limit": budget.limit,
                    "spent": budget.spent,
                }
                for budget in self.resource_limits
            ],
            "max_total_actions": self.max_total_actions,
            "max_clock_ticks": self.max_clock_ticks,
            "lifetime_seconds": self.lifetime_seconds,
            "required_evidence_refs": list(self.required_evidence_refs),
            "rollback_required": self.rollback_required,
            "interruptible": self.interruptible,
            "human_veto_required": self.human_veto_required,
            "post_action_receipt_required": (
                self.post_action_receipt_required
            ),
            "terminal_failure_on_violation": (
                self.terminal_failure_on_violation
            ),
            "terminal_failure_on_action_failure": (
                self.terminal_failure_on_action_failure
            ),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ControlWitnessCase:
    case_id: str
    contract_hash: str
    case_kind: str
    input_hash: str
    mechanism_enabled_effect_hash: str
    mechanism_removed_effect_hash: str
    evidence_ref: str
    passed: bool
    replay_verified: bool
    invariants_preserved: bool
    observed_at: float
    authority_change: str = "NONE"
    routing_mode_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = CONTROL_WITNESS_VERSION

    def __post_init__(self) -> None:
        _require_nonempty("case_id", self.case_id)
        _require_nonempty("evidence_ref", self.evidence_ref)
        _require_hash("contract_hash", self.contract_hash)
        _require_hash("input_hash", self.input_hash)
        _require_hash(
            "mechanism_enabled_effect_hash",
            self.mechanism_enabled_effect_hash,
        )
        _require_hash(
            "mechanism_removed_effect_hash",
            self.mechanism_removed_effect_hash,
        )
        if self.case_kind not in REQUIRED_CONTROL_CASE_KINDS:
            raise ControlWitnessError("invalid control witness case_kind")

        for name, value in (
            ("authority_change", self.authority_change),
            ("routing_mode_change", self.routing_mode_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise ControlWitnessError(f"{name} must remain NONE")

    @classmethod
    def create(
        cls,
        contract: ControlContract,
        *,
        case_kind: str,
        input_value: Any,
        mechanism_enabled_effect: Any,
        mechanism_removed_effect: Any,
        evidence_ref: str,
        passed: bool,
        replay_verified: bool,
        invariants_preserved: bool,
        observed_at: float | None = None,
    ) -> "ControlWitnessCase":
        return cls(
            case_id=str(uuid.uuid4()),
            contract_hash=contract.contract_hash,
            case_kind=case_kind,
            input_hash=witness_hash(input_value),
            mechanism_enabled_effect_hash=witness_hash(
                mechanism_enabled_effect
            ),
            mechanism_removed_effect_hash=witness_hash(
                mechanism_removed_effect
            ),
            evidence_ref=evidence_ref,
            passed=bool(passed),
            replay_verified=bool(replay_verified),
            invariants_preserved=bool(invariants_preserved),
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
    def qualifies(self) -> bool:
        return (
            self.passed
            and self.replay_verified
            and self.invariants_preserved
            and self.behavioral_effect_observed
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "case_id": self.case_id,
            "contract_hash": self.contract_hash,
            "case_kind": self.case_kind,
            "input_hash": self.input_hash,
            "mechanism_enabled_effect_hash": (
                self.mechanism_enabled_effect_hash
            ),
            "mechanism_removed_effect_hash": (
                self.mechanism_removed_effect_hash
            ),
            "behavioral_effect_observed": self.behavioral_effect_observed,
            "evidence_ref": self.evidence_ref,
            "passed": self.passed,
            "replay_verified": self.replay_verified,
            "invariants_preserved": self.invariants_preserved,
            "qualifies": self.qualifies,
            "observed_at": self.observed_at,
            "authority_change": self.authority_change,
            "routing_mode_change": self.routing_mode_change,
            "constitutional_change": self.constitutional_change,
        }


@dataclass(frozen=True)
class ControlWitnessPolicy:
    min_cases: int = len(REQUIRED_CONTROL_CASE_KINDS)
    min_distinct_inputs: int = len(REQUIRED_CONTROL_CASE_KINDS)
    require_all_case_kinds: bool = True
    require_all_cases_pass: bool = True
    min_replay_verified_rate: float = 1.0
    min_invariant_preservation_rate: float = 1.0
    min_behavioral_reality_rate: float = 1.0

    def __post_init__(self) -> None:
        if self.min_cases <= 0:
            raise ControlWitnessError("min_cases must be > 0")
        if self.min_distinct_inputs <= 0:
            raise ControlWitnessError("min_distinct_inputs must be > 0")
        if self.min_distinct_inputs > self.min_cases:
            raise ControlWitnessError(
                "min_distinct_inputs may not exceed min_cases"
            )
        for name, value in (
            ("min_replay_verified_rate", self.min_replay_verified_rate),
            (
                "min_invariant_preservation_rate",
                self.min_invariant_preservation_rate,
            ),
            ("min_behavioral_reality_rate", self.min_behavioral_reality_rate),
        ):
            if not (0.0 <= value <= 1.0):
                raise ControlWitnessError(f"{name} must be in [0, 1]")

    def to_record(self) -> dict[str, Any]:
        return {
            "min_cases": self.min_cases,
            "min_distinct_inputs": self.min_distinct_inputs,
            "require_all_case_kinds": self.require_all_case_kinds,
            "require_all_cases_pass": self.require_all_cases_pass,
            "min_replay_verified_rate": self.min_replay_verified_rate,
            "min_invariant_preservation_rate": (
                self.min_invariant_preservation_rate
            ),
            "min_behavioral_reality_rate": self.min_behavioral_reality_rate,
        }


@dataclass(frozen=True)
class ControlWitnessReport:
    report_id: str
    contract_id: str
    contract_hash: str
    status: str
    total_cases: int
    distinct_input_count: int
    covered_case_kinds: tuple[str, ...]
    missing_case_kinds: tuple[str, ...]
    pass_rate: float
    replay_verified_rate: float
    invariant_preservation_rate: float
    behavioral_reality_rate: float
    blocker_reasons: tuple[str, ...]
    case_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    policy: ControlWitnessPolicy
    evaluated_at: float
    report_hash: str
    authority_change: str = "NONE"
    routing_mode_change: str = "NONE"
    steering_authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = CONTROL_WITNESS_VERSION

    def __post_init__(self) -> None:
        _require_nonempty("report_id", self.report_id)
        _require_nonempty("contract_id", self.contract_id)
        _require_hash("contract_hash", self.contract_hash)
        _require_hash("report_hash", self.report_hash)
        if self.status not in {NOT_READY, READY_FOR_HUMAN_REVIEW}:
            raise ControlWitnessError("invalid control witness report status")
        if self.total_cases < 0 or self.distinct_input_count < 0:
            raise ControlWitnessError("control witness counts must be >= 0")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ControlWitnessError("case_ids must be unique")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ControlWitnessError("evidence_refs must be unique")
        if len(self.case_ids) != self.total_cases:
            raise ControlWitnessError(
                "total_cases must equal number of case_ids"
            )
        if len(self.evidence_refs) != self.total_cases:
            raise ControlWitnessError(
                "each control witness case requires unique evidence_ref"
            )

        for name, value in (
            ("pass_rate", self.pass_rate),
            ("replay_verified_rate", self.replay_verified_rate),
            (
                "invariant_preservation_rate",
                self.invariant_preservation_rate,
            ),
            ("behavioral_reality_rate", self.behavioral_reality_rate),
        ):
            if not (0.0 <= value <= 1.0):
                raise ControlWitnessError(f"{name} must be in [0, 1]")

        if self.status == READY_FOR_HUMAN_REVIEW and self.blocker_reasons:
            raise ControlWitnessError(
                "ready report may not contain blocker_reasons"
            )
        if self.status == NOT_READY and not self.blocker_reasons:
            raise ControlWitnessError(
                "not-ready report requires blocker_reasons"
            )

        for name, value in (
            ("authority_change", self.authority_change),
            ("routing_mode_change", self.routing_mode_change),
            (
                "steering_authority_change",
                self.steering_authority_change,
            ),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise ControlWitnessError(f"{name} must remain NONE")

    @property
    def ready_for_human_review(self) -> bool:
        return self.status == READY_FOR_HUMAN_REVIEW


def evaluate_control_witness(
    contract: ControlContract,
    cases: tuple[ControlWitnessCase, ...] | list[ControlWitnessCase],
    *,
    policy: ControlWitnessPolicy | None = None,
    evaluated_at: float | None = None,
) -> ControlWitnessReport:
    active_policy = policy or ControlWitnessPolicy()
    case_tuple = tuple(cases)
    timestamp = time.time() if evaluated_at is None else float(evaluated_at)

    case_ids = tuple(case.case_id for case in case_tuple)
    evidence_refs = tuple(case.evidence_ref for case in case_tuple)
    if len(case_ids) != len(set(case_ids)):
        raise ControlWitnessError(
            "same control witness case may not be counted twice"
        )
    if len(evidence_refs) != len(set(evidence_refs)):
        raise ControlWitnessError(
            "same control evidence receipt may not be counted twice"
        )
    if any(case.contract_hash != contract.contract_hash for case in case_tuple):
        raise ControlWitnessError(
            "all control witness cases must bind to exact control contract"
        )

    total = len(case_tuple)
    distinct_inputs = len({case.input_hash for case in case_tuple})
    covered = frozenset(case.case_kind for case in case_tuple)
    missing = REQUIRED_CONTROL_CASE_KINDS.difference(covered)

    def rate(count: int) -> float:
        return 0.0 if total == 0 else count / total

    pass_rate = rate(sum(case.passed for case in case_tuple))
    replay_rate = rate(sum(case.replay_verified for case in case_tuple))
    invariant_rate = rate(
        sum(case.invariants_preserved for case in case_tuple)
    )
    behavioral_rate = rate(
        sum(case.behavioral_effect_observed for case in case_tuple)
    )

    blockers: list[str] = []
    if total < active_policy.min_cases:
        blockers.append(
            f"insufficient control cases: {total} < {active_policy.min_cases}"
        )
    if distinct_inputs < active_policy.min_distinct_inputs:
        blockers.append(
            "insufficient distinct control inputs: "
            f"{distinct_inputs} < {active_policy.min_distinct_inputs}"
        )
    if active_policy.require_all_case_kinds and missing:
        blockers.append(
            "missing required control case kinds: "
            + ",".join(sorted(missing))
        )
    if active_policy.require_all_cases_pass and any(
        not case.passed for case in case_tuple
    ):
        blockers.append("one or more control witness cases failed")
    if replay_rate < active_policy.min_replay_verified_rate:
        blockers.append(
            "control replay verification below policy: "
            f"{replay_rate:.6f} < "
            f"{active_policy.min_replay_verified_rate:.6f}"
        )
    if invariant_rate < active_policy.min_invariant_preservation_rate:
        blockers.append(
            "control invariant preservation below policy: "
            f"{invariant_rate:.6f} < "
            f"{active_policy.min_invariant_preservation_rate:.6f}"
        )
    if behavioral_rate < active_policy.min_behavioral_reality_rate:
        blockers.append(
            "control behavioral reality below policy: "
            f"{behavioral_rate:.6f} < "
            f"{active_policy.min_behavioral_reality_rate:.6f}"
        )

    status = (
        READY_FOR_HUMAN_REVIEW if not blockers else NOT_READY
    )

    payload = {
        "contract_id": contract.contract_id,
        "contract_hash": contract.contract_hash,
        "status": status,
        "total_cases": total,
        "distinct_input_count": distinct_inputs,
        "covered_case_kinds": sorted(covered),
        "missing_case_kinds": sorted(missing),
        "pass_rate": pass_rate,
        "replay_verified_rate": replay_rate,
        "invariant_preservation_rate": invariant_rate,
        "behavioral_reality_rate": behavioral_rate,
        "blocker_reasons": blockers,
        "case_ids": list(case_ids),
        "evidence_refs": list(evidence_refs),
        "policy": active_policy.to_record(),
        "evaluated_at": timestamp,
    }

    return ControlWitnessReport(
        report_id=str(uuid.uuid4()),
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        status=status,
        total_cases=total,
        distinct_input_count=distinct_inputs,
        covered_case_kinds=tuple(sorted(covered)),
        missing_case_kinds=tuple(sorted(missing)),
        pass_rate=pass_rate,
        replay_verified_rate=replay_rate,
        invariant_preservation_rate=invariant_rate,
        behavioral_reality_rate=behavioral_rate,
        blocker_reasons=tuple(blockers),
        case_ids=case_ids,
        evidence_refs=evidence_refs,
        policy=active_policy,
        evaluated_at=timestamp,
        report_hash=witness_hash(payload),
    )


@dataclass(frozen=True)
class ControlPromotionProposal:
    proposal_id: str
    base_mode: str
    base_revision: int
    target_mode: str
    contract_id: str
    contract_hash: str
    witness_report_id: str
    witness_report_hash: str
    proposer_id: str
    reason: str
    created_at: float
    authority_change: str = "NONE"
    routing_mode_change: str = PROPOSED_ONLY
    steering_authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = CONTROL_WITNESS_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("proposal_id", self.proposal_id),
            ("contract_id", self.contract_id),
            ("witness_report_id", self.witness_report_id),
            ("proposer_id", self.proposer_id),
            ("reason", self.reason),
        ):
            _require_nonempty(name, value)
        _require_hash("contract_hash", self.contract_hash)
        _require_hash("witness_report_hash", self.witness_report_hash)
        if self.base_mode != ADVISE:
            raise ControlWitnessError(
                "control promotion proposal must begin from ADVISE"
            )
        if self.target_mode != BOUNDED_CONTROL:
            raise ControlWitnessError(
                "control promotion proposal target must be BOUNDED_CONTROL"
            )
        if self.base_revision < 0:
            raise ControlWitnessError("base_revision must be >= 0")
        if self.authority_change != "NONE":
            raise ControlWitnessError(
                "proposal may not itself grant authority"
            )
        if self.routing_mode_change != PROPOSED_ONLY:
            raise ControlWitnessError(
                "proposal routing mode change must be PROPOSED_ONLY"
            )
        if self.steering_authority_change != "NONE":
            raise ControlWitnessError(
                "proposal may not itself grant steering authority"
            )
        if self.constitutional_change != "NONE":
            raise ControlWitnessError(
                "proposal may not change constitution"
            )


def propose_bounded_control(
    state: PromotionState,
    contract: ControlContract,
    report: ControlWitnessReport,
    *,
    proposer_id: str,
    reason: str,
    created_at: float | None = None,
) -> ControlPromotionProposal:
    if state.mode != ADVISE:
        raise ControlWitnessError(
            "bounded-control proposal requires current ADVISE mode"
        )
    if not report.ready_for_human_review:
        raise ControlWitnessError(
            "control witness report is not ready for human review"
        )
    if report.contract_id != contract.contract_id:
        raise ControlWitnessError(
            "control witness report references different contract"
        )
    if report.contract_hash != contract.contract_hash:
        raise ControlWitnessError(
            "control witness report contract hash mismatch"
        )
    _require_nonempty("proposer_id", proposer_id)
    _require_nonempty("reason", reason)

    return ControlPromotionProposal(
        proposal_id=str(uuid.uuid4()),
        base_mode=state.mode,
        base_revision=state.revision,
        target_mode=BOUNDED_CONTROL,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        witness_report_id=report.report_id,
        witness_report_hash=report.report_hash,
        proposer_id=proposer_id,
        reason=reason,
        created_at=(
            time.time() if created_at is None else float(created_at)
        ),
    )


@dataclass(frozen=True)
class BoundedControlGrant:
    grant_id: str
    contract_id: str
    contract_hash: str
    promotion_proposal_id: str
    witness_report_id: str
    witness_report_hash: str
    actor_id: str
    warrant: Warrant
    human_seal_id: str
    human_actor_id: str
    authority_ref: str
    granted_at: float
    expires_at: float
    authority_change: str = HUMAN_AUTHORIZED_BOUNDED_LEASE
    routing_mode_change: str = HUMAN_AUTHORIZED_APPLIED
    steering_authority_change: str = HUMAN_AUTHORIZED_BOUNDED
    constitutional_change: str = "NONE"
    version: str = CONTROL_WITNESS_VERSION

    def __post_init__(self) -> None:
        _require_hash("contract_hash", self.contract_hash)
        _require_hash("witness_report_hash", self.witness_report_hash)
        if self.expires_at <= self.granted_at:
            raise ControlWitnessError(
                "bounded-control grant must expire after grant time"
            )
        if self.warrant.bearer != self.actor_id:
            raise ControlWitnessError(
                "bounded-control warrant bearer must match grant actor"
            )
        if self.warrant.expires_at != self.expires_at:
            raise ControlWitnessError(
                "bounded-control warrant lifetime must match grant"
            )
        if self.authority_change != HUMAN_AUTHORIZED_BOUNDED_LEASE:
            raise ControlWitnessError(
                "bounded-control authority must be an explicit human lease"
            )
        if self.routing_mode_change != HUMAN_AUTHORIZED_APPLIED:
            raise ControlWitnessError(
                "bounded-control mode change must be human-authorized"
            )
        if self.steering_authority_change != HUMAN_AUTHORIZED_BOUNDED:
            raise ControlWitnessError(
                "bounded steering authority must be human-authorized"
            )
        if self.constitutional_change != "NONE":
            raise ControlWitnessError(
                "bounded-control grant may not change constitution"
            )


@dataclass(frozen=True)
class ControlPromotionReceipt:
    receipt_id: str
    proposal_id: str
    contract_id: str
    contract_hash: str
    witness_report_id: str
    witness_report_hash: str
    prior_mode: str
    resulting_mode: str
    prior_revision: int
    resulting_revision: int
    grant_id: str
    warrant_id: str
    human_seal_id: str
    human_actor_id: str
    authority_ref: str
    applied_at: float
    authority_change: str = HUMAN_AUTHORIZED_BOUNDED_LEASE
    routing_mode_change: str = HUMAN_AUTHORIZED_APPLIED
    steering_authority_change: str = HUMAN_AUTHORIZED_BOUNDED
    constitutional_change: str = "NONE"
    version: str = CONTROL_WITNESS_VERSION


def authorize_bounded_control(
    state: PromotionState,
    proposal: ControlPromotionProposal,
    contract: ControlContract,
    report: ControlWitnessReport,
    human_seal: HumanAuthoritySeal,
    *,
    applied_at: float | None = None,
) -> tuple[PromotionState, BoundedControlGrant, ControlPromotionReceipt]:
    if state.mode != ADVISE:
        raise ControlWitnessError(
            "bounded-control authorization requires current ADVISE mode"
        )
    if state.mode != proposal.base_mode:
        raise ControlWitnessError(
            "control proposal base mode does not match current state"
        )
    if state.revision != proposal.base_revision:
        raise ControlWitnessError(
            "control promotion proposal base revision is stale"
        )
    if proposal.target_mode != BOUNDED_CONTROL:
        raise ControlWitnessError(
            "control proposal target must be BOUNDED_CONTROL"
        )
    if not report.ready_for_human_review:
        raise ControlWitnessError(
            "control witness report is not ready for human review"
        )

    if proposal.contract_id != contract.contract_id:
        raise ControlWitnessError("proposal references different contract")
    if proposal.contract_hash != contract.contract_hash:
        raise ControlWitnessError("proposal contract hash mismatch")
    if report.contract_id != contract.contract_id:
        raise ControlWitnessError("report references different contract")
    if report.contract_hash != contract.contract_hash:
        raise ControlWitnessError("report contract hash mismatch")
    if proposal.witness_report_id != report.report_id:
        raise ControlWitnessError(
            "proposal references different control witness report"
        )
    if proposal.witness_report_hash != report.report_hash:
        raise ControlWitnessError(
            "proposal control witness report hash mismatch"
        )

    if human_seal.issued_at < proposal.created_at:
        raise ControlWitnessError(
            "human authorization seal may not predate control proposal"
        )

    metadata = human_seal.metadata
    expected = {
        "control_promotion_proposal_id": proposal.proposal_id,
        "control_witness_report_hash": report.report_hash,
        "control_contract_hash": contract.contract_hash,
        "target_mode": BOUNDED_CONTROL,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ControlWitnessError(
                f"human seal is not bound to exact control field '{key}'"
            )

    timestamp = time.time() if applied_at is None else float(applied_at)
    if timestamp < human_seal.issued_at:
        raise ControlWitnessError(
            "control authorization may not predate human seal"
        )

    warrant = Warrant.issue(
        issuer=human_seal.actor_id,
        bearer=contract.actor_id,
        scopes=contract.scopes,
        budgets=tuple(
            ResourceBudget(kind=budget.kind, limit=budget.limit)
            for budget in contract.resource_limits
        ),
        lifetime_seconds=contract.lifetime_seconds,
        issued_at=timestamp,
        metadata={
            "control_contract_id": contract.contract_id,
            "control_contract_hash": contract.contract_hash,
            "control_promotion_proposal_id": proposal.proposal_id,
            "control_witness_report_hash": report.report_hash,
            "human_seal_id": human_seal.seal_id,
        },
    )
    if warrant.expires_at is None:
        raise ControlWitnessError(
            "bounded-control warrant must have finite lifetime"
        )

    grant_id = str(uuid.uuid4())
    grant = BoundedControlGrant(
        grant_id=grant_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        promotion_proposal_id=proposal.proposal_id,
        witness_report_id=report.report_id,
        witness_report_hash=report.report_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        human_seal_id=human_seal.seal_id,
        human_actor_id=human_seal.actor_id,
        authority_ref=human_seal.authority_ref,
        granted_at=timestamp,
        expires_at=warrant.expires_at,
    )

    receipt_id = str(uuid.uuid4())
    updated_state = PromotionState(
        mode=BOUNDED_CONTROL,
        revision=state.revision + 1,
        last_receipt_id=receipt_id,
        authorized_by_seal_id=human_seal.seal_id,
    )
    receipt = ControlPromotionReceipt(
        receipt_id=receipt_id,
        proposal_id=proposal.proposal_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        witness_report_id=report.report_id,
        witness_report_hash=report.report_hash,
        prior_mode=state.mode,
        resulting_mode=BOUNDED_CONTROL,
        prior_revision=state.revision,
        resulting_revision=updated_state.revision,
        grant_id=grant_id,
        warrant_id=warrant.warrant_id,
        human_seal_id=human_seal.seal_id,
        human_actor_id=human_seal.actor_id,
        authority_ref=human_seal.authority_ref,
        applied_at=timestamp,
    )
    return updated_state, grant, receipt


@dataclass(frozen=True)
class ActionUsage:
    rule_id: str
    count: int = 0

    def __post_init__(self) -> None:
        _require_nonempty("rule_id", self.rule_id)
        if self.count < 0:
            raise ControlWitnessError("action usage count must be >= 0")


@dataclass(frozen=True)
class ControlSession:
    session_id: str
    grant_id: str
    contract_id: str
    contract_hash: str
    actor_id: str
    warrant: Warrant
    started_at: float
    actions_used: int
    clock_ticks_used: float
    action_usage: tuple[ActionUsage, ...]
    pending_action_id: str | None = None
    pending_rollback_ref: str | None = None
    stopped: bool = False
    stop_reason: str | None = None
    last_receipt_id: str | None = None
    version: str = CONTROL_WITNESS_VERSION

    def __post_init__(self) -> None:
        _require_nonempty("session_id", self.session_id)
        _require_nonempty("grant_id", self.grant_id)
        _require_nonempty("contract_id", self.contract_id)
        _require_nonempty("actor_id", self.actor_id)
        _require_hash("contract_hash", self.contract_hash)
        if self.actions_used < 0:
            raise ControlWitnessError("actions_used must be >= 0")
        if self.clock_ticks_used < 0:
            raise ControlWitnessError("clock_ticks_used must be >= 0")
        if self.warrant.bearer != self.actor_id:
            raise ControlWitnessError(
                "control session warrant belongs to different actor"
            )
        usage_ids = [usage.rule_id for usage in self.action_usage]
        if len(usage_ids) != len(set(usage_ids)):
            raise ControlWitnessError("action_usage rule ids must be unique")
        if (self.pending_action_id is None) != (
            self.pending_rollback_ref is None
        ):
            raise ControlWitnessError(
                "pending action and rollback reference must travel together"
            )
        if self.stopped and not (self.stop_reason or "").strip():
            raise ControlWitnessError(
                "stopped control session requires stop_reason"
            )

    def usage_for(self, rule_id: str) -> int:
        usage = next(
            (item for item in self.action_usage if item.rule_id == rule_id),
            None,
        )
        return 0 if usage is None else usage.count


def start_control_session(
    state: PromotionState,
    grant: BoundedControlGrant,
    contract: ControlContract,
    *,
    started_at: float | None = None,
) -> ControlSession:
    timestamp = time.time() if started_at is None else float(started_at)
    if state.mode != BOUNDED_CONTROL or not state.may_steer:
        raise ControlWitnessError(
            "control session requires BOUNDED_CONTROL promotion state"
        )
    if state.authorized_by_seal_id != grant.human_seal_id:
        raise ControlWitnessError(
            "promotion state seal lineage does not match control grant"
        )
    if grant.contract_id != contract.contract_id:
        raise ControlWitnessError("grant references different contract")
    if grant.contract_hash != contract.contract_hash:
        raise ControlWitnessError("grant contract hash mismatch")
    if grant.actor_id != contract.actor_id:
        raise ControlWitnessError("grant actor does not match contract")
    if tuple(grant.warrant.scopes) != contract.scopes:
        raise ControlWitnessError(
            "bounded-control warrant scopes do not exactly match contract"
        )
    expected_budgets = {
        budget.kind: budget.limit for budget in contract.resource_limits
    }
    actual_budgets = {
        budget.kind: budget.limit for budget in grant.warrant.budgets
    }
    if actual_budgets != expected_budgets:
        raise ControlWitnessError(
            "bounded-control warrant budgets do not exactly match contract"
        )
    if any(budget.spent != 0 for budget in grant.warrant.budgets):
        raise ControlWitnessError(
            "control session must start from an unspent dedicated warrant"
        )
    if grant.warrant.metadata.get("control_contract_hash") != contract.contract_hash:
        raise ControlWitnessError(
            "bounded-control warrant metadata is not bound to contract"
        )
    if grant.warrant.revoked:
        raise ControlWitnessError("control grant warrant is revoked")
    if grant.warrant.is_expired(now=timestamp):
        raise ControlWitnessError("control grant warrant is expired")
    if timestamp < grant.granted_at:
        raise ControlWitnessError(
            "control session may not start before grant"
        )

    return ControlSession(
        session_id=str(uuid.uuid4()),
        grant_id=grant.grant_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        actor_id=contract.actor_id,
        warrant=grant.warrant,
        started_at=timestamp,
        actions_used=0,
        clock_ticks_used=0.0,
        action_usage=tuple(
            ActionUsage(rule_id=rule.rule_id, count=0)
            for rule in contract.action_rules
        ),
    )


@dataclass(frozen=True)
class ControlActionRequest:
    action_id: str
    actor_id: str
    operation: str
    target: str
    resource_spends: tuple[ResourceSpend, ...]
    clock_ticks: float
    evidence_refs: tuple[str, ...]
    rollback_ref: str
    requested_at: float
    version: str = CONTROL_WITNESS_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("action_id", self.action_id),
            ("actor_id", self.actor_id),
            ("operation", self.operation),
            ("target", self.target),
            ("rollback_ref", self.rollback_ref),
        ):
            _require_nonempty(name, value)
        if self.clock_ticks < 0:
            raise ControlWitnessError("clock_ticks must be >= 0")
        kinds = [spend.kind for spend in self.resource_spends]
        if len(kinds) != len(set(kinds)):
            raise ControlWitnessError(
                "resource_spends may contain each kind only once"
            )
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ControlWitnessError(
                "evidence_refs may not contain empty values"
            )
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ControlWitnessError(
                "evidence_refs may not contain duplicates"
            )

    @classmethod
    def create(
        cls,
        *,
        actor_id: str,
        operation: str,
        target: str,
        resource_spends: tuple[ResourceSpend, ...] | list[ResourceSpend] = (),
        clock_ticks: float = 0.0,
        evidence_refs: tuple[str, ...] | list[str] = (),
        rollback_ref: str,
        requested_at: float | None = None,
    ) -> "ControlActionRequest":
        return cls(
            action_id=str(uuid.uuid4()),
            actor_id=actor_id,
            operation=operation,
            target=target,
            resource_spends=tuple(resource_spends),
            clock_ticks=float(clock_ticks),
            evidence_refs=tuple(evidence_refs),
            rollback_ref=rollback_ref,
            requested_at=(
                time.time() if requested_at is None else float(requested_at)
            ),
        )


@dataclass(frozen=True)
class ControlActionLicenseReceipt:
    receipt_id: str
    session_id: str
    action_id: str
    rule_id: str | None
    decision: str
    allowed: bool
    reason: str
    transition_verdict_id: str | None
    warrant_id: str
    before_actions_used: int
    after_actions_used: int
    before_clock_ticks_used: float
    after_clock_ticks_used: float
    terminal: bool
    rollback_ref: str
    evaluated_at: float
    authority_change: str = "NONE"
    steering_authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = CONTROL_WITNESS_VERSION

    def __post_init__(self) -> None:
        if self.allowed and self.decision != LICENSE:
            raise ControlWitnessError(
                "allowed control action must carry LICENSE decision"
            )
        if self.allowed and self.terminal:
            raise ControlWitnessError(
                "licensed control action may not be terminal before outcome"
            )
        if not self.allowed and not self.terminal:
            raise ControlWitnessError(
                "v0.2 control refusal must fail terminally"
            )
        if self.after_actions_used < self.before_actions_used:
            raise ControlWitnessError("action count may not decrease")
        if self.after_clock_ticks_used < self.before_clock_ticks_used:
            raise ControlWitnessError("clock usage may not decrease")
        if self.authority_change != "NONE":
            raise ControlWitnessError(
                "action licensing may not expand authority"
            )
        if self.steering_authority_change != "NONE":
            raise ControlWitnessError(
                "action licensing may not expand steering authority"
            )
        if self.constitutional_change != "NONE":
            raise ControlWitnessError(
                "action licensing may not change constitution"
            )


def license_control_action(
    session: ControlSession,
    contract: ControlContract,
    request: ControlActionRequest,
    *,
    now: float | None = None,
) -> tuple[ControlSession, ControlActionLicenseReceipt, TransitionEvaluation | None]:
    timestamp = time.time() if now is None else float(now)
    _validate_session_contract(session, contract)

    if session.stopped:
        return _terminal_refusal(
            session,
            request,
            reason=f"control session already stopped: {session.stop_reason}",
            evaluated_at=timestamp,
        )
    if session.pending_action_id is not None:
        return _terminal_refusal(
            session,
            request,
            reason=(
                "post-action receipt missing for pending action "
                f"{session.pending_action_id}"
            ),
            evaluated_at=timestamp,
        )
    if session.warrant.is_expired(now=timestamp):
        return _terminal_refusal(
            session,
            request,
            reason="bounded-control warrant expired",
            evaluated_at=timestamp,
        )
    if request.actor_id != session.actor_id:
        return _terminal_refusal(
            session,
            request,
            reason="control action actor does not match session actor",
            evaluated_at=timestamp,
        )

    rule = contract.rule_for(request.operation, request.target)
    if rule is None:
        return _terminal_refusal(
            session,
            request,
            reason="control action is outside exact contract scope",
            evaluated_at=timestamp,
        )

    if session.actions_used >= contract.max_total_actions:
        return _terminal_refusal(
            session,
            request,
            reason="control contract total action limit exhausted",
            evaluated_at=timestamp,
            rule_id=rule.rule_id,
        )
    if session.usage_for(rule.rule_id) >= rule.max_count:
        return _terminal_refusal(
            session,
            request,
            reason=f"control action rule '{rule.rule_id}' count exhausted",
            evaluated_at=timestamp,
            rule_id=rule.rule_id,
        )

    if session.clock_ticks_used + request.clock_ticks > contract.max_clock_ticks:
        return _terminal_refusal(
            session,
            request,
            reason="control contract clock budget exhausted",
            evaluated_at=timestamp,
            rule_id=rule.rule_id,
        )

    missing_evidence = tuple(
        ref
        for ref in contract.required_evidence_refs
        if ref not in set(request.evidence_refs)
    )
    if missing_evidence:
        return _terminal_refusal(
            session,
            request,
            reason=(
                "control action missing required evidence refs: "
                + ",".join(missing_evidence)
            ),
            evaluated_at=timestamp,
            rule_id=rule.rule_id,
        )

    proposal = TransitionProposal.create(
        actor_id=request.actor_id,
        operation=request.operation,
        target=request.target,
        warrant_id=session.warrant.warrant_id,
        resource_spends=request.resource_spends,
        provenance_refs=request.evidence_refs,
        evidence_refs=request.evidence_refs,
        requested_at=request.requested_at,
        metadata={
            "control_session_id": session.session_id,
            "control_contract_id": contract.contract_id,
            "control_contract_hash": contract.contract_hash,
            "control_action_id": request.action_id,
            "rollback_ref": request.rollback_ref,
        },
    )
    evaluation = evaluate_transition(
        proposal,
        session.warrant,
        now=timestamp,
    )
    if not evaluation.verdict.allowed:
        return _terminal_refusal(
            session,
            request,
            reason=(
                "governed transition refused: "
                f"{evaluation.verdict.reason}"
            ),
            evaluated_at=timestamp,
            rule_id=rule.rule_id,
            transition=evaluation,
        )

    usage = tuple(
        replace(item, count=item.count + 1)
        if item.rule_id == rule.rule_id
        else item
        for item in session.action_usage
    )
    receipt_id = str(uuid.uuid4())
    updated = replace(
        session,
        warrant=evaluation.warrant_after,
        actions_used=session.actions_used + 1,
        clock_ticks_used=session.clock_ticks_used + request.clock_ticks,
        action_usage=usage,
        pending_action_id=request.action_id,
        pending_rollback_ref=request.rollback_ref,
        last_receipt_id=receipt_id,
    )
    receipt = ControlActionLicenseReceipt(
        receipt_id=receipt_id,
        session_id=session.session_id,
        action_id=request.action_id,
        rule_id=rule.rule_id,
        decision=LICENSE,
        allowed=True,
        reason="bounded control action licensed by contract and warrant",
        transition_verdict_id=evaluation.verdict.verdict_id,
        warrant_id=session.warrant.warrant_id,
        before_actions_used=session.actions_used,
        after_actions_used=updated.actions_used,
        before_clock_ticks_used=session.clock_ticks_used,
        after_clock_ticks_used=updated.clock_ticks_used,
        terminal=False,
        rollback_ref=request.rollback_ref,
        evaluated_at=timestamp,
    )
    return updated, receipt, evaluation


@dataclass(frozen=True)
class ControlOutcomeReceipt:
    receipt_id: str
    session_id: str
    action_id: str
    success: bool
    result_ref: str
    rollback_required: bool
    rollback_performed: bool
    rollback_ref: str | None
    rollback_satisfied: bool
    terminal: bool
    reason: str
    recorded_at: float
    authority_change: str = "NONE"
    steering_authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = CONTROL_WITNESS_VERSION


def record_control_outcome(
    session: ControlSession,
    contract: ControlContract,
    *,
    action_id: str,
    success: bool,
    result_ref: str,
    rollback_performed: bool = False,
    rollback_ref: str | None = None,
    recorded_at: float | None = None,
) -> tuple[ControlSession, ControlOutcomeReceipt]:
    timestamp = time.time() if recorded_at is None else float(recorded_at)
    _validate_session_contract(session, contract)
    _require_nonempty("action_id", action_id)
    _require_nonempty("result_ref", result_ref)

    if session.stopped and session.pending_action_id is None:
        raise ControlWitnessError(
            "stopped session has no pending action to close"
        )
    if session.pending_action_id is None:
        raise ControlWitnessError(
            "no pending action requires a post-action receipt"
        )
    if action_id != session.pending_action_id:
        raise ControlWitnessError(
            "post-action receipt action_id does not match pending action"
        )

    expected_rollback = session.pending_rollback_ref
    was_stopped = session.stopped
    prior_stop_reason = session.stop_reason
    if success:
        if rollback_performed:
            raise ControlWitnessError(
                "successful action may not claim failure rollback"
            )
        rollback_satisfied = True
        terminal = was_stopped
        reason = (
            f"outcome receipted after prior stop: {prior_stop_reason}"
            if was_stopped
            else "successful bounded-control action receipted"
        )
        updated_warrant = session.warrant
        stop_reason = prior_stop_reason if was_stopped else None
    else:
        rollback_satisfied = (
            rollback_performed
            and rollback_ref is not None
            and rollback_ref == expected_rollback
        )
        terminal = was_stopped or contract.terminal_failure_on_action_failure
        failure_reason = (
            "action failed; rollback verified; terminal control stop"
            if rollback_satisfied
            else "action failed; rollback missing/mismatched; terminal control stop"
        )
        reason = (
            f"{prior_stop_reason}; {failure_reason}"
            if was_stopped and prior_stop_reason
            else failure_reason
        )
        updated_warrant = (
            session.warrant
            if session.warrant.revoked
            else session.warrant.revoke(reason=reason)
        )
        stop_reason = prior_stop_reason or reason

    receipt_id = str(uuid.uuid4())
    updated = replace(
        session,
        warrant=updated_warrant,
        pending_action_id=None,
        pending_rollback_ref=None,
        stopped=terminal,
        stop_reason=stop_reason,
        last_receipt_id=receipt_id,
    )
    receipt = ControlOutcomeReceipt(
        receipt_id=receipt_id,
        session_id=session.session_id,
        action_id=action_id,
        success=bool(success),
        result_ref=result_ref,
        rollback_required=contract.rollback_required,
        rollback_performed=bool(rollback_performed),
        rollback_ref=rollback_ref,
        rollback_satisfied=rollback_satisfied,
        terminal=terminal,
        reason=reason,
        recorded_at=timestamp,
    )
    return updated, receipt


@dataclass(frozen=True)
class ControlStopReceipt:
    receipt_id: str
    session_id: str
    prior_stopped: bool
    resulting_stopped: bool
    stop_kind: str
    source_ref: str
    reason: str
    human_seal_id: str | None
    stopped_at: float
    warrant_revoked: bool
    authority_change: str = "REDUCED_ONLY"
    steering_authority_change: str = "REDUCED_ONLY"
    constitutional_change: str = "NONE"
    version: str = CONTROL_WITNESS_VERSION


def apply_human_veto(
    session: ControlSession,
    human_seal: HumanAuthoritySeal,
    *,
    reason: str,
    vetoed_at: float | None = None,
) -> tuple[ControlSession, ControlStopReceipt]:
    _require_nonempty("reason", reason)
    timestamp = time.time() if vetoed_at is None else float(vetoed_at)
    if timestamp < human_seal.issued_at:
        raise ControlWitnessError("human veto may not predate human seal")

    return _stop_session(
        session,
        stop_kind=VETO_STOP,
        source_ref=f"human-seal:{human_seal.seal_id}",
        reason=reason,
        stopped_at=timestamp,
        human_seal_id=human_seal.seal_id,
    )


def terminate_control_session(
    session: ControlSession,
    *,
    source_ref: str,
    reason: str,
    stopped_at: float | None = None,
) -> tuple[ControlSession, ControlStopReceipt]:
    _require_nonempty("source_ref", source_ref)
    _require_nonempty("reason", reason)
    timestamp = time.time() if stopped_at is None else float(stopped_at)
    return _stop_session(
        session,
        stop_kind=NORMAL_STOP,
        source_ref=source_ref,
        reason=reason,
        stopped_at=timestamp,
        human_seal_id=None,
    )


def collapse_control_to_shadow(
    state: PromotionState,
    session: ControlSession,
    *,
    source_ref: str,
    reason: str,
    collapsed_at: float | None = None,
):
    if state.mode != BOUNDED_CONTROL:
        raise ControlWitnessError(
            "control privilege collapse requires BOUNDED_CONTROL state"
        )
    if not session.stopped:
        raise ControlWitnessError(
            "control session must be stopped before privilege collapse"
        )
    return collapse_to_shadow(
        state,
        source_ref=source_ref,
        reason=reason,
        collapsed_at=collapsed_at,
    )


def _validate_session_contract(
    session: ControlSession,
    contract: ControlContract,
) -> None:
    if session.contract_id != contract.contract_id:
        raise ControlWitnessError(
            "control session references different contract"
        )
    if session.contract_hash != contract.contract_hash:
        raise ControlWitnessError("control session contract hash mismatch")
    if session.actor_id != contract.actor_id:
        raise ControlWitnessError(
            "control session actor does not match contract"
        )


def _terminal_refusal(
    session: ControlSession,
    request: ControlActionRequest,
    *,
    reason: str,
    evaluated_at: float,
    rule_id: str | None = None,
    transition: TransitionEvaluation | None = None,
) -> tuple[ControlSession, ControlActionLicenseReceipt, TransitionEvaluation | None]:
    receipt_id = str(uuid.uuid4())
    revoked = (
        session.warrant
        if session.warrant.revoked
        else session.warrant.revoke(reason=reason)
    )
    updated = replace(
        session,
        warrant=revoked,
        stopped=True,
        stop_reason=reason,
        last_receipt_id=receipt_id,
    )
    receipt = ControlActionLicenseReceipt(
        receipt_id=receipt_id,
        session_id=session.session_id,
        action_id=request.action_id,
        rule_id=rule_id,
        decision="REFUSE",
        allowed=False,
        reason=reason,
        transition_verdict_id=(
            None if transition is None else transition.verdict.verdict_id
        ),
        warrant_id=session.warrant.warrant_id,
        before_actions_used=session.actions_used,
        after_actions_used=session.actions_used,
        before_clock_ticks_used=session.clock_ticks_used,
        after_clock_ticks_used=session.clock_ticks_used,
        terminal=True,
        rollback_ref=request.rollback_ref,
        evaluated_at=evaluated_at,
    )
    return updated, receipt, transition


def _stop_session(
    session: ControlSession,
    *,
    stop_kind: str,
    source_ref: str,
    reason: str,
    stopped_at: float,
    human_seal_id: str | None,
) -> tuple[ControlSession, ControlStopReceipt]:
    if session.stopped:
        raise ControlWitnessError("control session is already stopped")
    if stop_kind not in {VETO_STOP, NORMAL_STOP, TERMINAL_STOP}:
        raise ControlWitnessError("invalid control stop kind")
    revoked = session.warrant.revoke(reason=reason)
    receipt_id = str(uuid.uuid4())
    updated = replace(
        session,
        warrant=revoked,
        stopped=True,
        stop_reason=reason,
        last_receipt_id=receipt_id,
    )
    receipt = ControlStopReceipt(
        receipt_id=receipt_id,
        session_id=session.session_id,
        prior_stopped=False,
        resulting_stopped=True,
        stop_kind=stop_kind,
        source_ref=source_ref,
        reason=reason,
        human_seal_id=human_seal_id,
        stopped_at=stopped_at,
        warrant_revoked=True,
    )
    return updated, receipt
