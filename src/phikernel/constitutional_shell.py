from __future__ import annotations

"""Shell adapter for PhiKernel v0.2 constitutional routing.

The shell adapter exposes the constitutional runtime without manufacturing
promotion authority.

v0.2 shell support is intentionally SHADOW-only. The shell may construct
temporary routing-evaluation warrants because those warrants authorize only
consideration as a routing candidate:

    route-evaluate:coach/<Coach>

They do not authorize tool execution, output commit, memory mutation, or
bounded steering.

ADVISE and BOUNDED_CONTROL require persisted, verifiable promotion/grant
lineage and are not synthesized from CLI flags.
"""

from dataclasses import dataclass
from typing import Any
import time

from phikernel.constitutional_runtime import ConstitutionalRuntimeResult, orchestrate_runtime
from phikernel.control_state import RuntimeControlState
from phikernel.relational_router import RelationalRoutingRequest, RouteCandidate
from phikernel.router import CoachRouter
from phikernel.routing_shadow import CoachRouteBinding
from phikernel.warrant import Warrant
from phikernel.witness_bench import PromotionState


CONSTITUTIONAL_SHELL_VERSION = "0.2.0"
SUPPORTED_SHELL_MODE = "SHADOW"

COACH_ROUTE_KEYS = {
    "Titan": "route:titan",
    "Flow": "route:flow",
    "Sage": "route:sage",
}


class ConstitutionalShellError(Exception):
    """Raised when shell constitutional-routing inputs are invalid."""


@dataclass(frozen=True)
class ShellCandidateSeed:
    coach: str
    route_key: str
    actor_id: str
    base_cost: float
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.coach not in COACH_ROUTE_KEYS:
            raise ConstitutionalShellError(f"unsupported coach '{self.coach}'")
        if self.route_key != COACH_ROUTE_KEYS[self.coach]:
            raise ConstitutionalShellError(
                "route_key does not match constitutional shell coach mapping"
            )
        if self.base_cost < 0:
            raise ConstitutionalShellError("base_cost must be >= 0")

    def to_record(self) -> dict[str, Any]:
        return {
            "coach": self.coach,
            "route_key": self.route_key,
            "actor_id": self.actor_id,
            "base_cost": self.base_cost,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ConstitutionalShellResult:
    runtime: ConstitutionalRuntimeResult
    candidate_seeds: tuple[ShellCandidateSeed, ...]
    mode: str = SUPPORTED_SHELL_MODE
    version: str = CONSTITUTIONAL_SHELL_VERSION

    def __post_init__(self) -> None:
        if self.mode != SUPPORTED_SHELL_MODE:
            raise ConstitutionalShellError(
                "v0.2 shell adapter supports SHADOW mode only"
            )

    def to_record(self) -> dict[str, Any]:
        record = self.runtime.to_record()
        record.update(
            {
                "constitutional_shell_version": self.version,
                "shell_mode": self.mode,
                "candidate_seeds": [
                    seed.to_record() for seed in self.candidate_seeds
                ],
                "legacy_reply": (
                    None
                    if self.runtime.legacy_reply is None
                    else self.runtime.legacy_reply.to_record()
                ),
            }
        )
        return record


def run_constitutional_shell_shadow(
    think_bundle: dict[str, Any],
    *,
    runtime_control_state: RuntimeControlState | None = None,
    router: CoachRouter | None = None,
    now: float | None = None,
) -> ConstitutionalShellResult:
    """Run one shell think bundle through the v0.2 SHADOW orchestration path."""

    evaluated_at = time.time() if now is None else float(now)
    active_router = router or CoachRouter()
    seeds = build_shell_candidate_seeds(think_bundle, router=active_router)
    candidates = build_shell_route_candidates(
        seeds,
        now=evaluated_at,
    )
    request = RelationalRoutingRequest.create(
        required_capabilities=("coach",),
        requested_at=evaluated_at,
        metadata={
            "source": "phik-shell",
            "mode": SUPPORTED_SHELL_MODE,
            "authority": "routing-evaluation-only",
        },
    )
    bindings = tuple(
        CoachRouteBinding(coach=coach, route_key=route_key)
        for coach, route_key in COACH_ROUTE_KEYS.items()
    )

    runtime = orchestrate_runtime(
        think_bundle,
        PromotionState.genesis(),
        request,
        candidates,
        bindings=bindings,
        runtime_control_state=runtime_control_state or RuntimeControlState(),
        legacy_router=active_router,
        now=evaluated_at,
    )
    return ConstitutionalShellResult(
        runtime=runtime,
        candidate_seeds=seeds,
    )


def build_shell_candidate_seeds(
    think_bundle: dict[str, Any],
    *,
    router: CoachRouter | None = None,
) -> tuple[ShellCandidateSeed, ...]:
    """Build deterministic coach seed costs from observable shell state.

    These are routing seed costs, not confidence scores and not authority.
    Crane Fly may later add constitutional field pressures on top of them.
    """

    active_router = router or CoachRouter()
    prompt = str(think_bundle.get("prompt", "")).strip().lower()
    anchor = think_bundle.get("anchor") or {}
    heart = think_bundle.get("heart") or {}
    field = think_bundle.get("field") or {}
    latest_capsule = think_bundle.get("latest_capsule")

    anchor_valid = (
        bool(anchor.get("verification", {}).get("valid", False))
        if anchor
        else False
    )
    heart_running = bool(heart.get("running", False)) if heart else False
    field_action = str(field.get("recommended_action", "observe"))
    has_capsule = latest_capsule is not None

    flow_hits = sum(
        1
        for keyword in active_router.registry["Flow"].prompt_keywords
        if keyword in prompt
    )
    sage_hits = sum(
        1
        for keyword in active_router.registry["Sage"].prompt_keywords
        if keyword in prompt
    )

    if not anchor_valid:
        return _safety_seeds("anchor trust invalid")
    if field_action == "alert":
        return _safety_seeds("field action alert")
    if field_action == "restore":
        return _safety_seeds("field action restore")
    if field_action == "checkpoint":
        return _safety_seeds("field action checkpoint")
    if not heart_running:
        return _safety_seeds("runtime pulse offline")
    if not has_capsule:
        return _safety_seeds("continuity capsule missing")

    titan_cost = 1.25
    flow_cost = max(0.25, 1.25 - (0.35 * flow_hits))
    sage_cost = max(0.25, 1.25 - (0.35 * sage_hits))

    reasons = {
        "Titan": ["stable substrate; grounding remains available"],
        "Flow": [
            f"flow keyword matches={flow_hits}",
            "stable substrate permits specialized routing",
        ],
        "Sage": [
            f"sage keyword matches={sage_hits}",
            "stable substrate permits specialized routing",
        ],
    }

    if flow_hits == 0 and sage_hits == 0:
        titan_cost = 0.75
        reasons["Titan"].append(
            "no specialization keywords; grounded default receives lower seed cost"
        )

    return (
        ShellCandidateSeed(
            coach="Titan",
            route_key=COACH_ROUTE_KEYS["Titan"],
            actor_id="coach:titan",
            base_cost=titan_cost,
            reasons=tuple(reasons["Titan"]),
        ),
        ShellCandidateSeed(
            coach="Flow",
            route_key=COACH_ROUTE_KEYS["Flow"],
            actor_id="coach:flow",
            base_cost=flow_cost,
            reasons=tuple(reasons["Flow"]),
        ),
        ShellCandidateSeed(
            coach="Sage",
            route_key=COACH_ROUTE_KEYS["Sage"],
            actor_id="coach:sage",
            base_cost=sage_cost,
            reasons=tuple(reasons["Sage"]),
        ),
    )


def _safety_seeds(reason: str) -> tuple[ShellCandidateSeed, ...]:
    return (
        ShellCandidateSeed(
            coach="Titan",
            route_key=COACH_ROUTE_KEYS["Titan"],
            actor_id="coach:titan",
            base_cost=0.0,
            reasons=(reason, "grounding route receives safety-priority seed"),
        ),
        ShellCandidateSeed(
            coach="Flow",
            route_key=COACH_ROUTE_KEYS["Flow"],
            actor_id="coach:flow",
            base_cost=10.0,
            reasons=(reason, "specialized route deprioritized under safety condition"),
        ),
        ShellCandidateSeed(
            coach="Sage",
            route_key=COACH_ROUTE_KEYS["Sage"],
            actor_id="coach:sage",
            base_cost=10.0,
            reasons=(reason, "specialized route deprioritized under safety condition"),
        ),
    )


def build_shell_route_candidates(
    seeds: tuple[ShellCandidateSeed, ...] | list[ShellCandidateSeed],
    *,
    now: float,
) -> tuple[RouteCandidate, ...]:
    """Create routing-evaluation-only candidates from deterministic seeds."""
    return tuple(
        _candidate_from_seed(seed, now=now)
        for seed in seeds
    )


def _candidate_from_seed(
    seed: ShellCandidateSeed,
    *,
    now: float,
) -> RouteCandidate:
    target = f"coach/{seed.coach}"
    warrant = Warrant.issue(
        issuer="phik-shell:routing-evaluator",
        bearer=seed.actor_id,
        scopes=(f"route-evaluate:{target}",),
        budgets=(),
        lifetime_seconds=60.0,
        issued_at=now,
        metadata={
            "purpose": "routing-evaluation-only",
            "coach": seed.coach,
            "route_key": seed.route_key,
            "execution_authority": "NONE",
        },
    )
    return RouteCandidate(
        route_key=seed.route_key,
        actor_id=seed.actor_id,
        operation="route-evaluate",
        target=target,
        warrant=warrant,
        base_cost=seed.base_cost,
        capability_tags=("coach", seed.coach.lower()),
        metadata={
            "source": "phik-shell",
            "coach": seed.coach,
            "routing_evaluation_only": True,
            "execution_authority": "NONE",
        },
    )
