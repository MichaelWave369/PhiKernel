from __future__ import annotations

"""PhiKernel-native trust runtime primitives.

This module adapts ANC enforcement outcomes into substrate-native semantics.
Quarantine and seal are logical/runtime metadata outcomes in this pass; they do
not yet mutate persistent branch/snapshot systems.
"""

from dataclasses import dataclass, field
from typing import Any

from phikernel.anc_bridge import (
    ANC_ACTION_QUARANTINE,
    ANC_ACTION_REFUSE,
    ANC_ACTION_SANDBOX,
    ANC_ACTION_SEAL,
    ANC_ACTION_SHADOW,
    ANC_ACTION_WARN,
    RuntimeEnforcementResult,
    TrustObservatorySnapshot,
    build_trust_snapshot,
)
from phikernel.control_state import RuntimeControlState
from phikernel.tiekat_v69_runtime import RuntimeFaceState, build_runtime_face_state


@dataclass(frozen=True)
class KernelGuardOutcome:
    allowed: bool
    blocked_stage: str | None
    requires_review: bool
    quarantined: bool
    sealed: bool
    deny_memory_write: bool
    deny_output_commit: bool
    next_step: str
    operator_message: str
    observability_snapshot: dict[str, Any]
    incident_summary: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "blocked_stage": self.blocked_stage,
            "requires_review": self.requires_review,
            "quarantined": self.quarantined,
            "sealed": self.sealed,
            "deny_memory_write": self.deny_memory_write,
            "deny_output_commit": self.deny_output_commit,
            "next_step": self.next_step,
            "operator_message": self.operator_message,
            "observability_snapshot": dict(self.observability_snapshot),
            "incident_summary": self.incident_summary,
            "metadata": dict(self.metadata),
        }


def trust_snapshot_from_result(
    enforcement: RuntimeEnforcementResult,
    *,
    face_state: RuntimeFaceState | None = None,
) -> TrustObservatorySnapshot:
    field_state = face_state or build_runtime_face_state({})
    return build_trust_snapshot(
        enforcement,
        weakest_face=field_state.weakest_face,
        extra_flags=[f"field_avg:{field_state.field_average:.3f}"],
    )


def map_enforcement_to_guard_outcome(
    enforcement: RuntimeEnforcementResult,
    *,
    face_state: RuntimeFaceState | None = None,
) -> KernelGuardOutcome:
    snapshot = trust_snapshot_from_result(enforcement, face_state=face_state)
    action = enforcement.action

    blocked = action in {ANC_ACTION_REFUSE, ANC_ACTION_QUARANTINE, ANC_ACTION_SEAL}
    quarantined = action == ANC_ACTION_QUARANTINE
    sealed = action == ANC_ACTION_SEAL

    deny_memory_write = bool(enforcement.metadata.get("deny_memory_write", False)) or (
        blocked and enforcement.stage == "memory_write"
    )
    deny_output_commit = bool(enforcement.metadata.get("deny_output_commit", False)) or (
        blocked and enforcement.stage == "output_commit"
    )

    execution_mode = None
    if action == ANC_ACTION_SHADOW:
        execution_mode = "shadow"
    elif action == ANC_ACTION_SANDBOX:
        execution_mode = "sandbox"

    metadata = dict(enforcement.metadata)
    if execution_mode:
        metadata.setdefault("execution_mode", execution_mode)

    next_step = "proceed"
    if blocked:
        next_step = "deny"
    elif action in {ANC_ACTION_WARN, ANC_ACTION_SHADOW, ANC_ACTION_SANDBOX}:
        next_step = "review"

    operator_message = _operator_message(action=action, enforcement=enforcement)

    return KernelGuardOutcome(
        allowed=not blocked,
        blocked_stage=enforcement.stage if blocked else None,
        requires_review=enforcement.requires_review or action in {ANC_ACTION_WARN, ANC_ACTION_SHADOW, ANC_ACTION_SANDBOX},
        quarantined=quarantined,
        sealed=sealed,
        deny_memory_write=deny_memory_write,
        deny_output_commit=deny_output_commit,
        next_step=next_step,
        operator_message=operator_message,
        observability_snapshot=snapshot.to_record(),
        incident_summary=enforcement.reason if blocked else None,
        metadata=metadata,
    )


def build_operator_trust_state(
    *,
    enforcement: RuntimeEnforcementResult,
    runtime_state: dict[str, Any] | None = None,
    control_state: RuntimeControlState | None = None,
) -> dict[str, Any]:
    field_state = build_runtime_face_state(runtime_state or {})
    outcome = map_enforcement_to_guard_outcome(enforcement, face_state=field_state)
    state = control_state or RuntimeControlState()
    return {
        "trust_posture": state.trust_posture or outcome.observability_snapshot.get("trust_posture"),
        "weakest_face": field_state.weakest_face,
        "field_average": field_state.field_average,
        "field_variance": field_state.field_variance,
        "edge_flow_score": field_state.edge_flow_score,
        "recovery_vertex_score": field_state.recovery_vertex_score,
        "contamination_load": field_state.contamination_load,
        "incidents": list(outcome.observability_snapshot.get("incidents") or []),
        "enforcement_action": enforcement.action,
        "requires_review": outcome.requires_review or state.review_required,
        "review_required": state.review_required,
        "quarantined": state.quarantined,
        "sealed": state.sealed,
        "last_operator_action": state.last_operator_action,
        "last_operator_note": state.last_operator_note,
        "last_action_timestamp": state.last_action_timestamp,
        "action_history_count": state.action_history_count,
        "next_step": state.next_step or outcome.next_step,
        "recovery_required": state.recovery_required,
        "recovery_state": state.recovery_state,
        "recovery_message": state.recovery_message,
        "last_recovery_action": state.last_recovery_action,
        "last_recovery_note": state.last_recovery_note,
        "last_recovery_timestamp": state.last_recovery_timestamp,
        "recovery_history_count": state.recovery_history_count,
        "metadata": {**outcome.metadata, "runtime_control_metadata": dict(state.metadata)},
    }


def _operator_message(action: str, enforcement: RuntimeEnforcementResult) -> str:
    if action == ANC_ACTION_WARN:
        return "Allowed with warning; operator review advised."
    if action == ANC_ACTION_SHADOW:
        return "Allowed in shadow mode; compare outputs before commit."
    if action == ANC_ACTION_SANDBOX:
        return "Allowed in sandbox mode; constrained runtime semantics applied."
    if action == ANC_ACTION_REFUSE:
        return "Operation refused by trust governance policy."
    if action == ANC_ACTION_QUARANTINE:
        return "Operation blocked and session marked quarantined."
    if action == ANC_ACTION_SEAL:
        return "Operation blocked and logical seal requested for recovery governance."
    return enforcement.reason
