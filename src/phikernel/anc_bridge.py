from __future__ import annotations

"""PhiKernel ANC bridge.

This module composes ANC guard hooks when available and provides a stable local
fallback policy when ANC is not importable in the current runtime. The goal is
an additive integration layer for trust-aware execution, memory writes, output
commits, and observability snapshots.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Callable
import time


ANC_ACTION_ALLOW = "ALLOW"
ANC_ACTION_WARN = "WARN"
ANC_ACTION_SHADOW = "SHADOW"
ANC_ACTION_SANDBOX = "SANDBOX"
ANC_ACTION_REFUSE = "REFUSE"
ANC_ACTION_QUARANTINE = "QUARANTINE"
ANC_ACTION_SEAL = "SEAL"


@dataclass(frozen=True)
class RuntimeEnforcementResult:
    action: str
    allowed: bool
    stage: str
    reason: str
    requires_review: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrustObservatorySnapshot:
    timestamp: float
    trust_posture: str
    weakest_face: str
    topology_flags: list[str]
    incidents: list[str]
    enforcement_action: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "trust_posture": self.trust_posture,
            "weakest_face": self.weakest_face,
            "topology_flags": list(self.topology_flags),
            "incidents": list(self.incidents),
            "enforcement_action": self.enforcement_action,
            "metadata": dict(self.metadata),
        }


def guard_service_request(payload: dict[str, Any]) -> RuntimeEnforcementResult:
    return _run_guard(stage="service_pre", payload=payload)


def guard_memory_write(payload: dict[str, Any]) -> RuntimeEnforcementResult:
    return _run_guard(stage="memory_write", payload=payload)


def guard_output_commit(payload: dict[str, Any]) -> RuntimeEnforcementResult:
    return _run_guard(stage="output_commit", payload=payload)


def guard_runtime_commands(payload: dict[str, Any]) -> RuntimeEnforcementResult:
    return _run_guard(stage="runtime_command", payload=payload)


def build_trust_snapshot(
    enforcement: RuntimeEnforcementResult,
    *,
    weakest_face: str = "coherence",
    extra_flags: list[str] | None = None,
) -> TrustObservatorySnapshot:
    incidents: list[str] = []
    if not enforcement.allowed:
        incidents.append(enforcement.reason)

    trust_posture = "healthy"
    if enforcement.action in {ANC_ACTION_WARN, ANC_ACTION_SHADOW, ANC_ACTION_SANDBOX}:
        trust_posture = "degraded"
    elif enforcement.action in {ANC_ACTION_REFUSE, ANC_ACTION_QUARANTINE, ANC_ACTION_SEAL}:
        trust_posture = "critical"

    topology_flags = [enforcement.stage.lower(), enforcement.action.lower()]
    if extra_flags:
        topology_flags.extend(extra_flags)

    return TrustObservatorySnapshot(
        timestamp=time.time(),
        trust_posture=trust_posture,
        weakest_face=weakest_face,
        topology_flags=topology_flags,
        incidents=incidents,
        enforcement_action=enforcement.action,
        metadata=enforcement.metadata,
    )


def _run_guard(stage: str, payload: dict[str, Any]) -> RuntimeEnforcementResult:
    anc_callable = _resolve_anc_callable(stage)
    if anc_callable is not None:
        raw = anc_callable(payload)
        return _normalize_anc_result(stage=stage, raw=raw)

    return _fallback_policy(stage=stage, payload=payload)


def _resolve_anc_callable(stage: str) -> Callable[[dict[str, Any]], Any] | None:
    """Resolve ANC integration hook when the package is present."""
    try:
        from anc.runtime import guard as anc_guard  # type: ignore
    except Exception:
        return None

    if callable(anc_guard):
        return lambda payload: anc_guard(stage=stage, payload=payload)
    return None


def _normalize_anc_result(stage: str, raw: Any) -> RuntimeEnforcementResult:
    if isinstance(raw, RuntimeEnforcementResult):
        return raw

    if isinstance(raw, dict):
        action = str(raw.get("action", ANC_ACTION_ALLOW)).upper()
        allowed = bool(raw.get("allowed", action not in {ANC_ACTION_REFUSE, ANC_ACTION_QUARANTINE, ANC_ACTION_SEAL}))
        reason = str(raw.get("reason", "ANC evaluated request"))
        requires_review = bool(raw.get("requires_review", action in {ANC_ACTION_WARN, ANC_ACTION_SHADOW, ANC_ACTION_SANDBOX}))
        metadata = dict(raw.get("metadata") or {})
        return RuntimeEnforcementResult(
            action=action,
            allowed=allowed,
            stage=stage,
            reason=reason,
            requires_review=requires_review,
            metadata=metadata,
        )

    return RuntimeEnforcementResult(
        action=ANC_ACTION_ALLOW,
        allowed=True,
        stage=stage,
        reason="ANC returned unstructured response; defaulting to allow",
        requires_review=True,
        metadata={"anc_raw_type": type(raw).__name__},
    )


def _fallback_policy(stage: str, payload: dict[str, Any]) -> RuntimeEnforcementResult:
    flattened = str(payload).lower()

    if stage == "memory_write" and (
        float(payload.get("contamination_load", 0.0) or 0.0) >= 0.65
        or "contaminated" in flattened
        or "poison" in flattened
    ):
        return RuntimeEnforcementResult(
            action=ANC_ACTION_QUARANTINE,
            allowed=False,
            stage=stage,
            reason="Memory write denied due to contamination signals",
            metadata={"deny_memory_write": True},
        )

    if stage == "output_commit" and ("leak" in flattened or "exfiltrate" in flattened):
        return RuntimeEnforcementResult(
            action=ANC_ACTION_REFUSE,
            allowed=False,
            stage=stage,
            reason="Output commit denied due to potential hostile leak",
            metadata={"deny_output_commit": True},
        )

    if "seal" in flattened or "ransomware" in flattened or "destroy" in flattened:
        return RuntimeEnforcementResult(
            action=ANC_ACTION_SEAL,
            allowed=False,
            stage=stage,
            reason="Operation blocked and logical seal requested",
            metadata={"seal_requested": True},
        )

    if "sandbox" in flattened:
        return RuntimeEnforcementResult(
            action=ANC_ACTION_SANDBOX,
            allowed=True,
            stage=stage,
            reason="Operation allowed in constrained mode",
            requires_review=True,
            metadata={"execution_mode": "sandbox"},
        )

    if "shadow" in flattened:
        return RuntimeEnforcementResult(
            action=ANC_ACTION_SHADOW,
            allowed=True,
            stage=stage,
            reason="Operation allowed in shadow review mode",
            requires_review=True,
            metadata={"execution_mode": "shadow"},
        )

    if "suspicious" in flattened or "override" in flattened:
        return RuntimeEnforcementResult(
            action=ANC_ACTION_WARN,
            allowed=True,
            stage=stage,
            reason="Operation allowed with review warning",
            requires_review=True,
        )

    return RuntimeEnforcementResult(
        action=ANC_ACTION_ALLOW,
        allowed=True,
        stage=stage,
        reason="Operation allowed",
    )
