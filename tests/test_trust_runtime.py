from phikernel.anc_bridge import RuntimeEnforcementResult
from phikernel.trust_runtime import build_operator_trust_state, map_enforcement_to_guard_outcome


def test_map_enforcement_to_guard_outcome_for_quarantine() -> None:
    enforcement = RuntimeEnforcementResult(
        action="QUARANTINE",
        allowed=False,
        stage="memory_write",
        reason="contamination detected",
        metadata={"deny_memory_write": True},
    )

    outcome = map_enforcement_to_guard_outcome(enforcement)

    assert outcome.allowed is False
    assert outcome.quarantined is True
    assert outcome.deny_memory_write is True
    assert outcome.blocked_stage == "memory_write"


def test_build_operator_trust_state_populates_runtime_observability() -> None:
    enforcement = RuntimeEnforcementResult(
        action="WARN",
        allowed=True,
        stage="service_pre",
        reason="review advised",
        requires_review=True,
    )

    operator_state = build_operator_trust_state(
        enforcement=enforcement,
        runtime_state={"coherence_score": 0.81, "risk_score": 0.22},
    )

    assert operator_state["trust_posture"] in {"healthy", "degraded", "critical"}
    assert isinstance(operator_state["weakest_face"], str)
    assert "field_average" in operator_state
