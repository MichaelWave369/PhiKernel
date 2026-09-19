from __future__ import annotations

"""PhiKernel saturation-aware routing.

Success may improve evidence about a path, but repeated success must not silently
become permanent authority or monopoly. This module computes routing-cost
adjustments from decaying failure scars and recent success concentration.

The output is advisory routing evidence only. It cannot alter warrants,
capabilities, citation rights, or constitutional state.
"""

from dataclasses import dataclass, field
from math import pow
from typing import Any
import time
import uuid

from phikernel.scar import FailureScar, ScarProfile, build_scar_profile


SATURATION_VERSION = "0.2.0"


class SaturationError(Exception):
    """Base exception for saturation-routing errors."""


@dataclass(frozen=True)
class SuccessObservation:
    success_id: str
    subject_id: str
    route_key: str
    source_ref: str
    observed_at: float = field(default_factory=time.time)
    weight: float = 1.0
    half_life_seconds: float = 3600.0
    metadata: dict[str, Any] = field(default_factory=dict)
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = SATURATION_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("success_id", self.success_id),
            ("subject_id", self.subject_id),
            ("route_key", self.route_key),
            ("source_ref", self.source_ref),
        ):
            if not value.strip():
                raise SaturationError(f"{name} must be non-empty")
        if self.weight < 0:
            raise SaturationError("weight must be >= 0")
        if self.half_life_seconds <= 0:
            raise SaturationError("half_life_seconds must be > 0")
        if self.authority_change != "NONE":
            raise SaturationError("success history may not change authority")
        if self.warrant_change != "NONE":
            raise SaturationError("success history may not change warrants")
        if self.constitutional_change != "NONE":
            raise SaturationError("success history may not change constitutional state")

    @classmethod
    def create(
        cls,
        *,
        subject_id: str,
        route_key: str,
        source_ref: str,
        observed_at: float | None = None,
        weight: float = 1.0,
        half_life_seconds: float = 3600.0,
        metadata: dict[str, Any] | None = None,
    ) -> "SuccessObservation":
        return cls(
            success_id=str(uuid.uuid4()),
            subject_id=subject_id,
            route_key=route_key,
            source_ref=source_ref,
            observed_at=time.time() if observed_at is None else float(observed_at),
            weight=float(weight),
            half_life_seconds=float(half_life_seconds),
            metadata=dict(metadata or {}),
        )

    def weight_at(self, *, now: float | None = None) -> float:
        current = time.time() if now is None else float(now)
        age = max(0.0, current - self.observed_at)
        return self.weight * pow(0.5, age / self.half_life_seconds)


@dataclass(frozen=True)
class SaturationPolicy:
    flow_share_cap: float = 0.50
    scar_penalty_scale: float = 1.0
    saturation_penalty_scale: float = 1.0
    probe_threshold: float = 0.10

    def __post_init__(self) -> None:
        if not (0.0 < self.flow_share_cap <= 1.0):
            raise SaturationError("flow_share_cap must be in (0.0, 1.0]")
        if self.scar_penalty_scale < 0:
            raise SaturationError("scar_penalty_scale must be >= 0")
        if self.saturation_penalty_scale < 0:
            raise SaturationError("saturation_penalty_scale must be >= 0")
        if not (0.0 <= self.probe_threshold <= 1.0):
            raise SaturationError("probe_threshold must be in [0.0, 1.0]")


@dataclass(frozen=True)
class RouteAdjustmentReceipt:
    receipt_id: str
    subject_id: str
    route_key: str
    base_cost: float
    scar_penalty: float
    saturation_penalty: float
    adjusted_cost: float
    success_flow_share: float
    viable_alternatives: int
    saturation_observed: bool
    probe_recommended: bool
    scar_profile: ScarProfile
    evaluated_at: float
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    citation_law_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = SATURATION_VERSION

    def __post_init__(self) -> None:
        if self.base_cost < 0:
            raise SaturationError("base_cost must be >= 0")
        if self.scar_penalty < 0 or self.saturation_penalty < 0:
            raise SaturationError("routing penalties must be >= 0")
        if self.adjusted_cost < self.base_cost:
            raise SaturationError("adjusted_cost may not be lower than base_cost")
        if not (0.0 <= self.success_flow_share <= 1.0):
            raise SaturationError("success_flow_share must be in [0.0, 1.0]")
        if self.viable_alternatives < 0:
            raise SaturationError("viable_alternatives must be >= 0")
        for field_name, value in (
            ("authority_change", self.authority_change),
            ("warrant_change", self.warrant_change),
            ("citation_law_change", self.citation_law_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise SaturationError(f"{field_name} must remain NONE")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "receipt_id": self.receipt_id,
            "subject_id": self.subject_id,
            "route_key": self.route_key,
            "base_cost": self.base_cost,
            "scar_penalty": self.scar_penalty,
            "saturation_penalty": self.saturation_penalty,
            "adjusted_cost": self.adjusted_cost,
            "success_flow_share": self.success_flow_share,
            "viable_alternatives": self.viable_alternatives,
            "saturation_observed": self.saturation_observed,
            "probe_recommended": self.probe_recommended,
            "scar_profile": self.scar_profile.to_record(),
            "evaluated_at": self.evaluated_at,
            "authority_change": self.authority_change,
            "warrant_change": self.warrant_change,
            "citation_law_change": self.citation_law_change,
            "constitutional_change": self.constitutional_change,
        }


def compute_route_adjustment(
    *,
    subject_id: str,
    route_key: str,
    base_cost: float,
    scars: tuple[FailureScar, ...] | list[FailureScar] = (),
    successes: tuple[SuccessObservation, ...] | list[SuccessObservation] = (),
    viable_alternatives: int = 0,
    policy: SaturationPolicy | None = None,
    now: float | None = None,
) -> RouteAdjustmentReceipt:
    """Compute a deterministic routing-cost receipt.

    Failure increases cost through decaying scar weight.
    Success concentration can add a saturation penalty only when viable
    alternatives exist. If no viable alternative exists, concentration is still
    recorded but the sole path is not throttled into uselessness.
    """

    evaluated_at = time.time() if now is None else float(now)
    if base_cost < 0:
        raise SaturationError("base_cost must be >= 0")
    if viable_alternatives < 0:
        raise SaturationError("viable_alternatives must be >= 0")

    active_policy = policy or SaturationPolicy()
    scar_profile = build_scar_profile(
        scars,
        subject_id=subject_id,
        route_key=route_key,
        now=evaluated_at,
    )
    scar_penalty = scar_profile.active_weight * active_policy.scar_penalty_scale

    route_successes = tuple(
        observation
        for observation in successes
        if observation.route_key == route_key
    )
    total_success_weight = sum(
        observation.weight_at(now=evaluated_at)
        for observation in route_successes
    )
    subject_success_weight = sum(
        observation.weight_at(now=evaluated_at)
        for observation in route_successes
        if observation.subject_id == subject_id
    )

    success_flow_share = (
        0.0
        if total_success_weight <= 0
        else subject_success_weight / total_success_weight
    )
    excess_share = max(0.0, success_flow_share - active_policy.flow_share_cap)
    saturation_observed = excess_share > 0.0

    saturation_penalty = 0.0
    if saturation_observed and viable_alternatives > 0:
        saturation_penalty = (
            excess_share * active_policy.saturation_penalty_scale
        )

    adjusted_cost = base_cost + scar_penalty + saturation_penalty
    probe_recommended = (
        saturation_observed
        and viable_alternatives > 0
        and excess_share >= active_policy.probe_threshold
    )

    return RouteAdjustmentReceipt(
        receipt_id=str(uuid.uuid4()),
        subject_id=subject_id,
        route_key=route_key,
        base_cost=base_cost,
        scar_penalty=scar_penalty,
        saturation_penalty=saturation_penalty,
        adjusted_cost=adjusted_cost,
        success_flow_share=success_flow_share,
        viable_alternatives=viable_alternatives,
        saturation_observed=saturation_observed,
        probe_recommended=probe_recommended,
        scar_profile=scar_profile,
        evaluated_at=evaluated_at,
    )
