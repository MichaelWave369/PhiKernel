from __future__ import annotations

"""PhiKernel relational routing field / Crane Fly vNext.

This module composes the constitutional layers already present in PhiKernel into
one deterministic routing decision surface.

Routing happens in two stages:

1. HARD ADMISSIBILITY
   Warrant scope, capability, resources, citation rights, contradiction state,
   premise quarantine, coalition state, clock budget, and explicit human blocks.

2. RELATIONAL CONDUCTANCE
   Only admissible candidates are scored. Cost may be influenced by failure
   scars, saturation, L2 routing weather, challenged premises, clock pressure,
   resource pressure, degraded coalitions, and explicit human preference cost.

The score never grants authority. A high-conductance route that fails a hard
admissibility check remains unavailable.
"""

from dataclasses import dataclass, field
from typing import Any
import time
import uuid

from phikernel.carriage import CitationDecision
from phikernel.clockskin import ClockLease
from phikernel.coalition import (
    DEGRADED as COALITION_DEGRADED,
    CoalitionCharter,
)
from phikernel.contradiction import ContradictionObject
from phikernel.mutability import RoutingWeatherState
from phikernel.premise import (
    CHALLENGED as PREMISE_CHALLENGED,
    PremiseObject,
    evaluate_dependent_route,
)
from phikernel.saturation import (
    SaturationPolicy,
    SuccessObservation,
    compute_route_adjustment,
)
from phikernel.scar import FailureScar
from phikernel.transition import ResourceSpend
from phikernel.warrant import Warrant


RELATIONAL_ROUTER_VERSION = "0.2.0"

BLOCK = "BLOCK"
PENALTY = "PENALTY"
VALID_HUMAN_CONSTRAINT_MODES = {BLOCK, PENALTY}


class RelationalRoutingError(Exception):
    """Base exception for relational-routing failures."""


@dataclass(frozen=True)
class HumanRouteConstraint:
    constraint_id: str
    route_key: str
    mode: str
    authority_ref: str
    reason: str
    value: float = 0.0
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    version: str = RELATIONAL_ROUTER_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("constraint_id", self.constraint_id),
            ("route_key", self.route_key),
            ("mode", self.mode),
            ("authority_ref", self.authority_ref),
            ("reason", self.reason),
        ):
            if not value.strip():
                raise RelationalRoutingError(f"{name} must be non-empty")
        if self.mode not in VALID_HUMAN_CONSTRAINT_MODES:
            raise RelationalRoutingError("invalid human constraint mode")
        if self.value < 0:
            raise RelationalRoutingError("constraint value must be >= 0")
        if self.mode == BLOCK and self.value != 0:
            raise RelationalRoutingError("BLOCK constraint must use value=0")
        if self.expires_at is not None and self.expires_at < self.created_at:
            raise RelationalRoutingError("constraint expires_at may not precede created_at")

    @classmethod
    def block(
        cls,
        *,
        route_key: str,
        authority_ref: str,
        reason: str,
        created_at: float | None = None,
        expires_at: float | None = None,
    ) -> "HumanRouteConstraint":
        return cls(
            constraint_id=str(uuid.uuid4()),
            route_key=route_key,
            mode=BLOCK,
            authority_ref=authority_ref,
            reason=reason,
            value=0.0,
            created_at=time.time() if created_at is None else float(created_at),
            expires_at=expires_at,
        )

    @classmethod
    def penalty(
        cls,
        *,
        route_key: str,
        authority_ref: str,
        reason: str,
        value: float,
        created_at: float | None = None,
        expires_at: float | None = None,
    ) -> "HumanRouteConstraint":
        return cls(
            constraint_id=str(uuid.uuid4()),
            route_key=route_key,
            mode=PENALTY,
            authority_ref=authority_ref,
            reason=reason,
            value=float(value),
            created_at=time.time() if created_at is None else float(created_at),
            expires_at=expires_at,
        )

    def active_at(self, now: float) -> bool:
        return self.expires_at is None or now < self.expires_at


@dataclass(frozen=True)
class RelationalRoutingPolicy:
    premise_challenge_penalty: float = 0.50
    clock_pressure_scale: float = 1.00
    resource_pressure_scale: float = 1.00
    degraded_coalition_penalty: float = 0.25
    weather_scale: float = 1.00
    saturation_policy: SaturationPolicy = field(default_factory=SaturationPolicy)

    def __post_init__(self) -> None:
        for name, value in (
            ("premise_challenge_penalty", self.premise_challenge_penalty),
            ("clock_pressure_scale", self.clock_pressure_scale),
            ("resource_pressure_scale", self.resource_pressure_scale),
            ("degraded_coalition_penalty", self.degraded_coalition_penalty),
            ("weather_scale", self.weather_scale),
        ):
            if value < 0:
                raise RelationalRoutingError(f"{name} must be >= 0")


@dataclass(frozen=True)
class RouteCandidate:
    route_key: str
    actor_id: str
    operation: str
    target: str
    warrant: Warrant
    base_cost: float
    capability_tags: tuple[str, ...] = ()
    resource_spends: tuple[ResourceSpend, ...] = ()
    citation_decisions: tuple[CitationDecision, ...] = ()
    contradictions: tuple[ContradictionObject, ...] = ()
    premises: tuple[PremiseObject, ...] = ()
    weather_keys: tuple[str, ...] = ()
    clock_lease: ClockLease | None = None
    expected_ticks: float = 0.0
    coalition: CoalitionCharter | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = RELATIONAL_ROUTER_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("route_key", self.route_key),
            ("actor_id", self.actor_id),
            ("operation", self.operation),
            ("target", self.target),
        ):
            if not value.strip():
                raise RelationalRoutingError(f"{name} must be non-empty")
        if self.base_cost < 0:
            raise RelationalRoutingError("base_cost must be >= 0")
        if self.expected_ticks < 0:
            raise RelationalRoutingError("expected_ticks must be >= 0")
        if len(self.capability_tags) != len(set(self.capability_tags)):
            raise RelationalRoutingError("capability_tags must be unique")
        if len(self.weather_keys) != len(set(self.weather_keys)):
            raise RelationalRoutingError("weather_keys must be unique")

        resource_kinds = [spend.kind for spend in self.resource_spends]
        if len(resource_kinds) != len(set(resource_kinds)):
            raise RelationalRoutingError(
                "resource_spends may contain each resource kind only once"
            )


@dataclass(frozen=True)
class RelationalRoutingRequest:
    request_id: str
    required_capabilities: tuple[str, ...]
    requested_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str = RELATIONAL_ROUTER_VERSION

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise RelationalRoutingError("request_id must be non-empty")
        if any(not capability.strip() for capability in self.required_capabilities):
            raise RelationalRoutingError("required capabilities must be non-empty")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise RelationalRoutingError("required capabilities must be unique")

    @classmethod
    def create(
        cls,
        *,
        required_capabilities: tuple[str, ...] | list[str] = (),
        requested_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "RelationalRoutingRequest":
        return cls(
            request_id=str(uuid.uuid4()),
            required_capabilities=tuple(required_capabilities),
            requested_at=time.time() if requested_at is None else float(requested_at),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class CandidateFieldEvaluation:
    route_key: str
    actor_id: str
    admissible: bool
    hard_block_reasons: tuple[str, ...]
    base_cost: float
    scar_penalty: float
    saturation_penalty: float
    weather_penalty: float
    premise_penalty: float
    clock_penalty: float
    resource_penalty: float
    coalition_penalty: float
    human_penalty: float
    total_cost: float | None
    conductance: float
    probe_recommended: bool
    required_capabilities: tuple[str, ...]
    available_capabilities: tuple[str, ...]
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    citation_law_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = RELATIONAL_ROUTER_VERSION

    def __post_init__(self) -> None:
        if self.admissible:
            if self.total_cost is None:
                raise RelationalRoutingError("admissible route requires total_cost")
            if self.total_cost < 0:
                raise RelationalRoutingError("route total_cost must be >= 0")
            if not (0.0 < self.conductance <= 1.0):
                raise RelationalRoutingError(
                    "admissible route conductance must be in (0, 1]"
                )
        else:
            if self.total_cost is not None:
                raise RelationalRoutingError(
                    "blocked route may not receive a routing score"
                )
            if self.conductance != 0.0:
                raise RelationalRoutingError(
                    "blocked route conductance must be zero"
                )
            if not self.hard_block_reasons:
                raise RelationalRoutingError(
                    "blocked route requires at least one reason"
                )

        for name, value in (
            ("authority_change", self.authority_change),
            ("warrant_change", self.warrant_change),
            ("citation_law_change", self.citation_law_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise RelationalRoutingError(f"{name} must remain NONE")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "route_key": self.route_key,
            "actor_id": self.actor_id,
            "admissible": self.admissible,
            "hard_block_reasons": list(self.hard_block_reasons),
            "base_cost": self.base_cost,
            "scar_penalty": self.scar_penalty,
            "saturation_penalty": self.saturation_penalty,
            "weather_penalty": self.weather_penalty,
            "premise_penalty": self.premise_penalty,
            "clock_penalty": self.clock_penalty,
            "resource_penalty": self.resource_penalty,
            "coalition_penalty": self.coalition_penalty,
            "human_penalty": self.human_penalty,
            "total_cost": self.total_cost,
            "conductance": self.conductance,
            "probe_recommended": self.probe_recommended,
            "required_capabilities": list(self.required_capabilities),
            "available_capabilities": list(self.available_capabilities),
            "authority_change": self.authority_change,
            "warrant_change": self.warrant_change,
            "citation_law_change": self.citation_law_change,
            "constitutional_change": self.constitutional_change,
        }


@dataclass(frozen=True)
class RelationalRouteReceipt:
    receipt_id: str
    request_id: str
    selected_route_key: str | None
    selected_actor_id: str | None
    evaluations: tuple[CandidateFieldEvaluation, ...]
    evaluated_at: float
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    citation_law_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = RELATIONAL_ROUTER_VERSION

    def __post_init__(self) -> None:
        admissible = tuple(item for item in self.evaluations if item.admissible)
        if self.selected_route_key is None:
            if self.selected_actor_id is not None:
                raise RelationalRoutingError(
                    "selected_actor_id requires selected_route_key"
                )
            if admissible:
                raise RelationalRoutingError(
                    "receipt with admissible candidates requires a selection"
                )
        else:
            matches = tuple(
                item
                for item in admissible
                if item.route_key == self.selected_route_key
                and item.actor_id == self.selected_actor_id
            )
            if len(matches) != 1:
                raise RelationalRoutingError(
                    "selected route must match one admissible evaluation"
                )

        for name, value in (
            ("authority_change", self.authority_change),
            ("warrant_change", self.warrant_change),
            ("citation_law_change", self.citation_law_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise RelationalRoutingError(f"{name} must remain NONE")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "receipt_id": self.receipt_id,
            "request_id": self.request_id,
            "selected_route_key": self.selected_route_key,
            "selected_actor_id": self.selected_actor_id,
            "evaluated_at": self.evaluated_at,
            "evaluations": [item.to_record() for item in self.evaluations],
            "authority_change": self.authority_change,
            "warrant_change": self.warrant_change,
            "citation_law_change": self.citation_law_change,
            "constitutional_change": self.constitutional_change,
        }


def route_relational(
    request: RelationalRoutingRequest,
    candidates: tuple[RouteCandidate, ...] | list[RouteCandidate],
    *,
    scars: tuple[FailureScar, ...] | list[FailureScar] = (),
    successes: tuple[SuccessObservation, ...] | list[SuccessObservation] = (),
    weather: RoutingWeatherState | None = None,
    human_constraints: tuple[HumanRouteConstraint, ...] | list[HumanRouteConstraint] = (),
    policy: RelationalRoutingPolicy | None = None,
    now: float | None = None,
) -> RelationalRouteReceipt:
    """Evaluate candidates and choose the highest-conductance admissible route."""

    evaluated_at = request.requested_at if now is None else float(now)
    active_policy = policy or RelationalRoutingPolicy()
    weather_state = weather or RoutingWeatherState()

    candidate_tuple = tuple(candidates)
    route_keys = [candidate.route_key for candidate in candidate_tuple]
    if len(route_keys) != len(set(route_keys)):
        raise RelationalRoutingError("candidate route_key values must be unique")

    hard_results: dict[str, tuple[str, ...]] = {}
    for candidate in candidate_tuple:
        hard_results[candidate.route_key] = _hard_block_reasons(
            request,
            candidate,
            human_constraints=human_constraints,
            now=evaluated_at,
        )

    hard_viable_count = sum(
        1 for reasons in hard_results.values() if not reasons
    )

    evaluations: list[CandidateFieldEvaluation] = []
    for candidate in candidate_tuple:
        reasons = hard_results[candidate.route_key]
        if reasons:
            evaluations.append(
                CandidateFieldEvaluation(
                    route_key=candidate.route_key,
                    actor_id=candidate.actor_id,
                    admissible=False,
                    hard_block_reasons=reasons,
                    base_cost=candidate.base_cost,
                    scar_penalty=0.0,
                    saturation_penalty=0.0,
                    weather_penalty=0.0,
                    premise_penalty=0.0,
                    clock_penalty=0.0,
                    resource_penalty=0.0,
                    coalition_penalty=0.0,
                    human_penalty=0.0,
                    total_cost=None,
                    conductance=0.0,
                    probe_recommended=False,
                    required_capabilities=request.required_capabilities,
                    available_capabilities=_available_capabilities(candidate),
                )
            )
            continue

        history = compute_route_adjustment(
            subject_id=candidate.actor_id,
            route_key=candidate.route_key,
            base_cost=candidate.base_cost,
            scars=scars,
            successes=successes,
            viable_alternatives=max(0, hard_viable_count - 1),
            policy=active_policy.saturation_policy,
            now=evaluated_at,
        )
        weather_penalty = _weather_penalty(
            candidate,
            weather_state,
            active_policy,
            evaluated_at,
        )
        premise_penalty = _premise_penalty(candidate, active_policy)
        clock_penalty = _clock_penalty(candidate, active_policy)
        resource_penalty = _resource_penalty(candidate, active_policy)
        coalition_penalty = _coalition_penalty(candidate, active_policy)
        human_penalty = _human_penalty(
            candidate,
            human_constraints,
            evaluated_at,
        )

        total_cost = (
            history.adjusted_cost
            + weather_penalty
            + premise_penalty
            + clock_penalty
            + resource_penalty
            + coalition_penalty
            + human_penalty
        )
        conductance = 1.0 / (1.0 + total_cost)

        evaluations.append(
            CandidateFieldEvaluation(
                route_key=candidate.route_key,
                actor_id=candidate.actor_id,
                admissible=True,
                hard_block_reasons=(),
                base_cost=candidate.base_cost,
                scar_penalty=history.scar_penalty,
                saturation_penalty=history.saturation_penalty,
                weather_penalty=weather_penalty,
                premise_penalty=premise_penalty,
                clock_penalty=clock_penalty,
                resource_penalty=resource_penalty,
                coalition_penalty=coalition_penalty,
                human_penalty=human_penalty,
                total_cost=total_cost,
                conductance=conductance,
                probe_recommended=history.probe_recommended,
                required_capabilities=request.required_capabilities,
                available_capabilities=_available_capabilities(candidate),
            )
        )

    admissible = tuple(item for item in evaluations if item.admissible)
    selected = (
        min(admissible, key=lambda item: (item.total_cost, item.route_key))
        if admissible
        else None
    )

    return RelationalRouteReceipt(
        receipt_id=str(uuid.uuid4()),
        request_id=request.request_id,
        selected_route_key=None if selected is None else selected.route_key,
        selected_actor_id=None if selected is None else selected.actor_id,
        evaluations=tuple(evaluations),
        evaluated_at=evaluated_at,
    )


def _available_capabilities(candidate: RouteCandidate) -> tuple[str, ...]:
    capabilities = set(candidate.capability_tags)
    if candidate.coalition is not None:
        capabilities.update(candidate.coalition.capability_union)
    return tuple(sorted(capabilities))


def _hard_block_reasons(
    request: RelationalRoutingRequest,
    candidate: RouteCandidate,
    *,
    human_constraints: tuple[HumanRouteConstraint, ...] | list[HumanRouteConstraint],
    now: float,
) -> tuple[str, ...]:
    reasons: list[str] = []

    available_capabilities = set(_available_capabilities(candidate))
    missing_capabilities = tuple(
        capability
        for capability in request.required_capabilities
        if capability not in available_capabilities
    )
    if missing_capabilities:
        reasons.append(
            "missing required capabilities: " + ",".join(missing_capabilities)
        )

    warrant_check = candidate.warrant.authorize(
        actor_id=candidate.actor_id,
        operation=candidate.operation,
        target=candidate.target,
        now=now,
    )
    if not warrant_check.allowed:
        reasons.append(f"warrant denied: {warrant_check.reason}")

    for spend in candidate.resource_spends:
        budget = candidate.warrant.budget(spend.kind)
        if budget is None:
            reasons.append(
                f"resource '{spend.kind}' not granted by warrant"
            )
        elif spend.amount > budget.remaining:
            reasons.append(
                f"resource '{spend.kind}' insufficient: "
                f"requested={spend.amount}, remaining={budget.remaining}"
            )

    for citation in candidate.citation_decisions:
        if not citation.allowed:
            reasons.append(
                f"citation blocked for {citation.carriage_id}: {citation.reason}"
            )

    for contradiction in candidate.contradictions:
        if contradiction.is_open:
            reasons.append(
                f"open contradiction blocks route: {contradiction.contradiction_id}"
            )

    for premise in candidate.premises:
        try:
            decision = evaluate_dependent_route(
                premise,
                candidate.route_key,
                now=now,
            )
        except Exception as exc:
            reasons.append(
                f"premise relation invalid for {premise.premise_id}: {exc}"
            )
            continue
        if not decision.allowed:
            reasons.append(
                f"premise blocks route: {premise.premise_id} ({premise.state})"
            )

    if candidate.clock_lease is not None:
        lease = candidate.clock_lease
        if lease.subject_id != candidate.actor_id:
            reasons.append("clock lease belongs to a different actor")
        if lease.closed:
            reasons.append("clock lease is closed")
        elif candidate.expected_ticks > lease.remaining_ticks:
            reasons.append(
                f"clock budget insufficient: requested={candidate.expected_ticks}, "
                f"remaining={lease.remaining_ticks}"
            )

    if candidate.coalition is not None:
        if candidate.actor_id != candidate.coalition.coalition_id:
            reasons.append("candidate actor does not match coalition identity")
        elif not candidate.coalition.active_at(now):
            reasons.append("coalition is not active at routing time")
    elif candidate.actor_id.startswith("coalition:"):
        reasons.append("coalition actor requires an explicit coalition charter")

    for constraint in human_constraints:
        if (
            constraint.route_key == candidate.route_key
            and constraint.mode == BLOCK
            and constraint.active_at(now)
        ):
            reasons.append(
                f"human constraint blocks route: {constraint.constraint_id}"
            )

    return tuple(reasons)


def _weather_penalty(
    candidate: RouteCandidate,
    weather: RoutingWeatherState,
    policy: RelationalRoutingPolicy,
    now: float,
) -> float:
    total = 0.0
    for key in candidate.weather_keys:
        entry = weather.entry(key)
        if entry is None:
            continue
        # v0.2 treats L2 weather as routing pressure, never as a subsidy.
        total += max(0.0, entry.value_at(now))
    return total * policy.weather_scale


def _premise_penalty(
    candidate: RouteCandidate,
    policy: RelationalRoutingPolicy,
) -> float:
    challenged = sum(
        1 for premise in candidate.premises
        if premise.state == PREMISE_CHALLENGED
    )
    return challenged * policy.premise_challenge_penalty


def _clock_penalty(
    candidate: RouteCandidate,
    policy: RelationalRoutingPolicy,
) -> float:
    lease = candidate.clock_lease
    if lease is None or lease.granted_ticks <= 0:
        return 0.0

    utilization = lease.spent_ticks / lease.granted_ticks
    demand_ratio = (
        0.0
        if lease.remaining_ticks <= 0
        else candidate.expected_ticks / lease.remaining_ticks
    )
    pressure = min(1.0, max(utilization, demand_ratio))
    return pressure * policy.clock_pressure_scale


def _resource_penalty(
    candidate: RouteCandidate,
    policy: RelationalRoutingPolicy,
) -> float:
    ratios: list[float] = []
    for spend in candidate.resource_spends:
        budget = candidate.warrant.budget(spend.kind)
        if budget is None or budget.remaining <= 0:
            continue
        ratios.append(spend.amount / budget.remaining)

    pressure = 0.0 if not ratios else min(1.0, max(ratios))
    return pressure * policy.resource_pressure_scale


def _coalition_penalty(
    candidate: RouteCandidate,
    policy: RelationalRoutingPolicy,
) -> float:
    if (
        candidate.coalition is not None
        and candidate.coalition.state == COALITION_DEGRADED
    ):
        return policy.degraded_coalition_penalty
    return 0.0


def _human_penalty(
    candidate: RouteCandidate,
    constraints: tuple[HumanRouteConstraint, ...] | list[HumanRouteConstraint],
    now: float,
) -> float:
    return sum(
        constraint.value
        for constraint in constraints
        if (
            constraint.route_key == candidate.route_key
            and constraint.mode == PENALTY
            and constraint.active_at(now)
        )
    )
