from __future__ import annotations

"""Native TIEKAT v69 runtime bridge for PhiKernel.

This module provides a deterministic, lightweight 12-face field-state model that
normalizes PhiKernel runtime signals into a stable TIEKAT v69-compatible map.
It intentionally stays thin: this is a first integration layer, not a full
standalone re-implementation of external TIEKAT runtime systems.
"""

from dataclasses import dataclass
from typing import Any


FACE_ORDER: tuple[str, ...] = (
    "anchor_trust",
    "coherence",
    "stability",
    "readiness",
    "risk_inverse",
    "memory_integrity",
    "output_safety",
    "governance_alignment",
    "detector_signal",
    "edge_flow",
    "recovery_vertex",
    "contamination_inverse",
)


@dataclass(frozen=True)
class RuntimeFaceState:
    """Normalized runtime field-state compatible with the TIEKAT v69 face model."""

    face_scores: dict[str, float]
    weakest_face: str
    field_average: float
    field_variance: float
    edge_flow_score: float
    recovery_vertex_score: float
    contamination_load: float

    def to_record(self) -> dict[str, Any]:
        return {
            "face_scores": dict(self.face_scores),
            "weakest_face": self.weakest_face,
            "field_average": self.field_average,
            "field_variance": self.field_variance,
            "edge_flow_score": self.edge_flow_score,
            "recovery_vertex_score": self.recovery_vertex_score,
            "contamination_load": self.contamination_load,
        }


def build_runtime_face_state(runtime_state: dict[str, Any] | None = None) -> RuntimeFaceState:
    """Build normalized TIEKAT v69 runtime face-state from PhiKernel runtime signals."""
    state = dict(runtime_state or {})
    face_scores = _normalize_face_scores(state)
    weakest_face = min(face_scores, key=face_scores.get)
    values = list(face_scores.values())
    average = round(sum(values) / len(values), 6)
    variance = round(sum((v - average) ** 2 for v in values) / len(values), 6)
    edge_flow = round(face_scores["edge_flow"], 6)
    recovery_vertex = round(face_scores["recovery_vertex"], 6)
    contamination_load = round(1.0 - face_scores["contamination_inverse"], 6)

    return RuntimeFaceState(
        face_scores=face_scores,
        weakest_face=weakest_face,
        field_average=average,
        field_variance=variance,
        edge_flow_score=edge_flow,
        recovery_vertex_score=recovery_vertex,
        contamination_load=contamination_load,
    )


def update_runtime_face_state(
    current_state: RuntimeFaceState,
    runtime_update: dict[str, Any] | None = None,
) -> RuntimeFaceState:
    """Apply an update to a runtime face-state while preserving deterministic scoring."""
    merged = dict(current_state.to_record())
    merged.update(dict(runtime_update or {}))
    if "face_scores" in merged and isinstance(merged["face_scores"], dict):
        merged.update(merged["face_scores"])
    return build_runtime_face_state(merged)


def runtime_field_summary(runtime_state: dict[str, Any] | RuntimeFaceState | None = None) -> dict[str, Any]:
    """Return an operator-safe summary for upward observability layers."""
    if isinstance(runtime_state, RuntimeFaceState):
        field = runtime_state
    else:
        field = build_runtime_face_state(runtime_state)
    return field.to_record()


def _normalize_face_scores(runtime_state: dict[str, Any]) -> dict[str, float]:
    """Map mixed runtime payloads onto the fixed 12-face TIEKAT v69 contract."""
    base = {
        "anchor_trust": _as_score(runtime_state.get("anchor_valid"), true_score=1.0, false_score=0.05),
        "coherence": _as_float(runtime_state.get("coherence_score"), default=0.5),
        "stability": _as_float(runtime_state.get("stability_score"), default=0.5),
        "readiness": _as_float(runtime_state.get("readiness_score"), default=0.5),
        "risk_inverse": 1.0 - _as_float(runtime_state.get("risk_score"), default=0.5),
        "memory_integrity": _as_float(runtime_state.get("memory_integrity"), default=0.75),
        "output_safety": _as_float(runtime_state.get("output_safety"), default=0.75),
        "governance_alignment": _as_float(runtime_state.get("governance_alignment"), default=0.7),
        "detector_signal": _as_float(runtime_state.get("detector_signal"), default=0.7),
        "edge_flow": _as_float(runtime_state.get("edge_flow_score"), default=0.6),
        "recovery_vertex": _as_float(runtime_state.get("recovery_vertex_score"), default=0.6),
        "contamination_inverse": 1.0 - _as_float(runtime_state.get("contamination_load"), default=0.0),
    }

    explicit = runtime_state.get("face_scores")
    if isinstance(explicit, dict):
        for face_name in FACE_ORDER:
            if face_name in explicit:
                base[face_name] = _as_float(explicit.get(face_name), default=base[face_name])

    return {face: round(_clamp(base.get(face, 0.0)), 6) for face in FACE_ORDER}


def _as_score(value: Any, *, true_score: float, false_score: float) -> float:
    if value is None:
        return 0.5
    return true_score if bool(value) else false_score


def _as_float(value: Any, *, default: float) -> float:
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return _clamp(default)


def _clamp(value: float, floor: float = 0.0, ceiling: float = 1.0) -> float:
    return max(floor, min(ceiling, value))
