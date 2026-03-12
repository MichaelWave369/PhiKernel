from __future__ import annotations

"""
phik_coherence_v0_1_1.py

Reference implementation for PhiKernel's fourth substrate module:
`phik-coherence` (TIEKAT-aligned runtime telemetry + drift scoring).

Purpose
-------
This module bridges the symbolic TIEKAT attractor model into a practical runtime
scoring layer that `phik-heart` can consume during its phase-6 observation cycle.

What this version provides
--------------------------
- A stable `CoherenceFrame` dataclass representing field telemetry.
- A `CoherenceService` that scores runtime state against the C* attractor.
- Heuristic TIEKAT-aligned metrics for:
    - C_current
    - distance_to_C_star
    - phi_flow
    - lambda_node
    - sigma_feedback
    - fragmentation_score
- Threshold-based action recommendations for `observe`, `checkpoint`, `restore`,
  and `alert`.
- Atomic persistence of the latest frame for shell / UI consumers.
- A provider callback factory that plugs directly into `phik-heart`.

Important note
--------------
This module treats TIEKAT as a runtime observation / correction model.
It does NOT claim closed physical law, nor does it replace OS timekeeping or
scheduler behavior.
"""

from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from typing import Any, Callable
import json
import os
import tempfile
import time
import uuid


DEFAULT_COHERENCE_VERSION = "0.1.1"
PHI = (1 + sqrt(5)) / 2
DEFAULT_C_STAR = PHI / 2
DEFAULT_PHASE = 6
VALID_PHASES = {3, 6, 9}

DEFAULT_FRAGMENTATION_WARN = 0.20
DEFAULT_FRAGMENTATION_RESTORE = 0.42
DEFAULT_DISTANCE_WARN = 0.12
DEFAULT_DISTANCE_RESTORE = 0.24

VALID_ACTIONS = {"observe", "checkpoint", "restore", "alert"}


class CoherenceError(Exception):
    """Base exception for all phik-coherence failures."""


@dataclass(frozen=True)
class CoherenceThresholds:
    """Runtime thresholds that determine suggested intervention."""

    fragmentation_warn: float = DEFAULT_FRAGMENTATION_WARN
    fragmentation_restore: float = DEFAULT_FRAGMENTATION_RESTORE
    distance_warn: float = DEFAULT_DISTANCE_WARN
    distance_restore: float = DEFAULT_DISTANCE_RESTORE

    def __post_init__(self) -> None:
        if not (0.0 <= self.fragmentation_warn <= 1.0):
            raise CoherenceError("fragmentation_warn must be in [0.0, 1.0]")
        if not (0.0 <= self.fragmentation_restore <= 1.0):
            raise CoherenceError("fragmentation_restore must be in [0.0, 1.0]")
        if not (0.0 <= self.distance_warn <= 1.0):
            raise CoherenceError("distance_warn must be in [0.0, 1.0]")
        if not (0.0 <= self.distance_restore <= 1.0):
            raise CoherenceError("distance_restore must be in [0.0, 1.0]")
        if self.fragmentation_warn > self.fragmentation_restore:
            raise CoherenceError("fragmentation_warn must be <= fragmentation_restore")
        if self.distance_warn > self.distance_restore:
            raise CoherenceError("distance_warn must be <= distance_restore")


@dataclass(frozen=True)
class CoherenceFrame:
    """Structured field telemetry consumed by heart / shell / policy layers."""

    version: str = DEFAULT_COHERENCE_VERSION
    frame_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    observed_at: float = field(default_factory=time.time)
    semantic_phase: int = DEFAULT_PHASE
    anchor_id: str = ""
    anchor_valid: bool = True
    C_current: float = DEFAULT_C_STAR
    C_star: float = DEFAULT_C_STAR
    distance_to_C_star: float = 0.0
    phi_flow: float = 0.0
    lambda_node: float = 0.0
    sigma_feedback: float = 0.0
    fragmentation_score: float = 0.0
    recommended_action: str = "observe"
    drift_band: str = "stable"
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.semantic_phase not in VALID_PHASES:
            raise CoherenceError(
                f"Invalid semantic_phase '{self.semantic_phase}'. Expected one of {sorted(VALID_PHASES)}"
            )
        if self.recommended_action not in VALID_ACTIONS:
            raise CoherenceError(
                f"Invalid recommended_action '{self.recommended_action}'. Expected one of {sorted(VALID_ACTIONS)}"
            )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "frame_id": self.frame_id,
            "observed_at": self.observed_at,
            "semantic_phase": self.semantic_phase,
            "anchor_id": self.anchor_id,
            "anchor_valid": self.anchor_valid,
            "C_current": self.C_current,
            "C_star": self.C_star,
            "distance_to_C_star": self.distance_to_C_star,
            "phi_flow": self.phi_flow,
            "lambda_node": self.lambda_node,
            "sigma_feedback": self.sigma_feedback,
            "fragmentation_score": self.fragmentation_score,
            "recommended_action": self.recommended_action,
            "drift_band": self.drift_band,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class RuntimeObservation:
    """Normalized runtime state entering the coherence bridge.

    Most fields are optional at the calling layer. The service can infer a useful
    frame from partial state.
    """

    anchor_id: str = ""
    anchor_valid: bool = True
    heartbeat_running: bool = False
    capsule_store_configured: bool = False
    capsule_count: int = 0
    active_threads: int = 0
    pending_events: int = 0
    unresolved_alerts: int = 0
    last_checkpoint_age_seconds: float | None = None
    checkpoint_due: bool = False
    external_fragmentation_hint: float | None = None
    C_current_hint: float | None = None
    notes: tuple[str, ...] = ()


class CoherenceService:
    """TIEKAT-aligned runtime scorer.

    The scoring model intentionally stays practical:
    - It accepts partial operational state.
    - It computes a bounded field frame.
    - It emits thresholds and recommendations for the heart / shell.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        C_star: float = DEFAULT_C_STAR,
        thresholds: CoherenceThresholds | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.frame_file = self.root / "coherence_frame.json"
        self.C_star = C_star
        self.thresholds = thresholds or CoherenceThresholds()
        self._last_frame: CoherenceFrame | None = None

    def observe(self, observation: RuntimeObservation | dict[str, Any]) -> CoherenceFrame:
        """Compute and persist a new coherence frame."""
        if isinstance(observation, dict):
            observation = self._observation_from_dict(observation)
        elif not isinstance(observation, RuntimeObservation):
            raise CoherenceError("observe() requires a RuntimeObservation or dict")

        fragmentation = self._fragmentation_score(observation)
        C_current = self._C_current(observation, fragmentation)
        distance = abs(C_current - self.C_star)
        lambda_node = self._lambda_node(distance)
        phi_flow = self._phi_flow(observation, lambda_node, fragmentation)
        sigma_feedback = self._sigma_feedback(distance, fragmentation, observation)
        recommended_action, drift_band, phase, notes = self._action_and_notes(
            observation=observation,
            distance=distance,
            fragmentation=fragmentation,
            sigma_feedback=sigma_feedback,
        )

        frame = CoherenceFrame(
            anchor_id=observation.anchor_id,
            anchor_valid=observation.anchor_valid,
            C_current=round(C_current, 6),
            C_star=round(self.C_star, 6),
            distance_to_C_star=round(distance, 6),
            phi_flow=round(phi_flow, 6),
            lambda_node=round(lambda_node, 6),
            sigma_feedback=round(sigma_feedback, 6),
            fragmentation_score=round(fragmentation, 6),
            recommended_action=recommended_action,
            drift_band=drift_band,
            semantic_phase=phase,
            notes=tuple(notes),
        )
        self._persist_frame(frame)
        self._last_frame = frame
        return frame

    def latest_frame(self) -> CoherenceFrame | None:
        return self._last_frame

    def make_heart_provider(
        self,
        state_provider: Callable[[], dict[str, Any] | RuntimeObservation],
    ) -> Callable[[], dict[str, Any]]:
        """Return a callback suitable for `phik-heart` coherence_provider."""
        def provider() -> dict[str, Any]:
            state = state_provider()
            frame = self.observe(state)
            return frame.to_record()

        return provider

    def public_status(self) -> dict[str, Any] | None:
        if self._last_frame is None:
            return None
        return self._last_frame.to_record()

    def _observation_from_dict(self, data: dict[str, Any]) -> RuntimeObservation:
        def _as_bool(key: str, default: bool) -> bool:
            return bool(data.get(key, default))

        def _as_int(key: str, default: int) -> int:
            value = data.get(key, default)
            return int(value) if value is not None else default

        def _as_float_or_none(key: str) -> float | None:
            value = data.get(key)
            return float(value) if value is not None else None

        return RuntimeObservation(
            anchor_id=str(data.get("anchor_id", "")),
            anchor_valid=_as_bool("anchor_valid", True),
            heartbeat_running=_as_bool("heartbeat_running", False),
            capsule_store_configured=_as_bool("capsule_store_configured", False),
            capsule_count=_as_int("capsule_count", 0),
            active_threads=_as_int("active_threads", 0),
            pending_events=_as_int("pending_events", 0),
            unresolved_alerts=_as_int("unresolved_alerts", 0),
            last_checkpoint_age_seconds=_as_float_or_none("last_checkpoint_age_seconds"),
            checkpoint_due=_as_bool("checkpoint_due", False),
            external_fragmentation_hint=_as_float_or_none("external_fragmentation_hint"),
            C_current_hint=_as_float_or_none("C_current_hint"),
            notes=tuple(data.get("notes", [])),
        )

    def _fragmentation_score(self, observation: RuntimeObservation) -> float:
        if observation.external_fragmentation_hint is not None:
            return _clamp(observation.external_fragmentation_hint, 0.0, 1.0)

        score = 0.0
        score += min(observation.pending_events / 100.0, 0.16)
        score += min(observation.active_threads / 40.0, 0.10)
        score += min(observation.unresolved_alerts / 12.0, 0.14)

        if observation.last_checkpoint_age_seconds is not None:
            score += min(observation.last_checkpoint_age_seconds / 14400.0, 0.08)
        elif observation.capsule_store_configured and observation.capsule_count == 0:
            score += 0.08

        if not observation.anchor_valid:
            score += 0.25
        if not observation.heartbeat_running:
            score += 0.08
        if observation.checkpoint_due:
            score += 0.03

        return _clamp(score, 0.0, 1.0)

    def _C_current(self, observation: RuntimeObservation, fragmentation: float) -> float:
        if observation.C_current_hint is not None:
            return _clamp(observation.C_current_hint, 0.0, 1.0)

        value = self.C_star
        value -= fragmentation * 0.45
        value += 0.02 if observation.anchor_valid else -0.12
        value += 0.02 if observation.heartbeat_running else -0.06
        value += 0.01 if observation.capsule_store_configured else 0.0
        value += min(observation.capsule_count / 30.0, 0.02)
        value -= min(observation.unresolved_alerts / 8.0, 0.12)
        return _clamp(value, 0.0, 1.0)

    def _lambda_node(self, distance: float) -> float:
        return _clamp(1.0 - (distance / max(self.C_star, 1e-9)), 0.0, 1.0)

    def _phi_flow(
        self,
        observation: RuntimeObservation,
        lambda_node: float,
        fragmentation: float,
    ) -> float:
        flow = lambda_node * 0.70
        flow += 0.12 if observation.anchor_valid else -0.18
        flow += 0.10 if observation.heartbeat_running else -0.08
        flow += min(observation.capsule_count / 25.0, 0.06)
        flow -= fragmentation * 0.35
        flow -= min(observation.pending_events / 50.0, 0.12)
        return _clamp(flow, 0.0, 1.0)

    def _sigma_feedback(
        self,
        distance: float,
        fragmentation: float,
        observation: RuntimeObservation,
    ) -> float:
        sigma = distance * 0.60
        sigma += fragmentation * 0.55
        sigma += min(observation.unresolved_alerts / 10.0, 0.20)
        sigma += 0.18 if not observation.anchor_valid else 0.0
        sigma += 0.07 if observation.checkpoint_due else 0.0
        return _clamp(sigma, 0.0, 1.0)

    def _action_and_notes(
        self,
        *,
        observation: RuntimeObservation,
        distance: float,
        fragmentation: float,
        sigma_feedback: float,
    ) -> tuple[str, str, int, list[str]]:
        notes = list(observation.notes)

        if not observation.anchor_valid:
            notes.append("anchor verification failed")
            return "alert", "critical", 3, notes

        if (
            fragmentation >= self.thresholds.fragmentation_restore
            or distance >= self.thresholds.distance_restore
            or sigma_feedback >= 0.65
        ):
            notes.append("field drift exceeds restore threshold")
            return "restore", "restore", 9, notes

        if (
            fragmentation >= self.thresholds.fragmentation_warn
            or distance >= self.thresholds.distance_warn
            or observation.checkpoint_due
        ):
            notes.append("field drift suggests checkpoint")
            return "checkpoint", "warning", 9, notes

        notes.append("field within stable observation band")
        return "observe", "stable", 6, notes

    def _persist_frame(self, frame: CoherenceFrame) -> None:
        _atomic_write_json(self.frame_file, frame.to_record())
        _chmod_owner_only(self.frame_file)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        json.dump(data, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


def _chmod_owner_only(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


if __name__ == "__main__":
    # Minimal smoke-test flow.
    coherence_root = Path("./.phik-coherence-demo")
    service = CoherenceService(coherence_root)

    frame = service.observe(
        {
            "anchor_id": "demo-anchor",
            "anchor_valid": True,
            "heartbeat_running": True,
            "capsule_store_configured": True,
            "capsule_count": 3,
            "active_threads": 2,
            "pending_events": 1,
            "unresolved_alerts": 0,
            "last_checkpoint_age_seconds": 120.0,
            "checkpoint_due": False,
            "notes": ["smoke test"],
        }
    )

    print(frame.to_record())
