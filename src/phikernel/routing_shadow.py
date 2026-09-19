from __future__ import annotations

"""PhiKernel Crane Fly shadow runtime.

Shadow mode compares the legacy CoachRouter decision with Crane Fly vNext
relational routing while preserving one hard operational rule:

    SHADOW ROUTING HAS ZERO STEERING AUTHORITY.

The legacy router remains authoritative. vNext may agree, diverge, hard-block
the legacy-mapped path, produce no route, or fail internally; none of those
outcomes may change the returned legacy reply in shadow mode.

The purpose of this module is evidence collection before live promotion.
"""

from dataclasses import dataclass, field
from typing import Any
import hashlib
import json
import time
import uuid

from phikernel.mutability import RoutingWeatherState
from phikernel.relational_router import (
    HumanRouteConstraint,
    RelationalRouteReceipt,
    RelationalRoutingPolicy,
    RelationalRoutingRequest,
    RouteCandidate,
    route_relational,
)
from phikernel.router import CoachReply, CoachRouter
from phikernel.saturation import SuccessObservation
from phikernel.scar import FailureScar


SHADOW_ROUTER_VERSION = "0.2.0"

AGREE = "AGREE"
DIVERGE = "DIVERGE"
VNEXT_BLOCKS_LEGACY = "VNEXT_BLOCKS_LEGACY"
VNEXT_NO_ROUTE = "VNEXT_NO_ROUTE"
VNEXT_ERROR = "VNEXT_ERROR"
LEGACY_UNMAPPED = "LEGACY_UNMAPPED"
VALID_COMPARISON_STATES = {
    AGREE,
    DIVERGE,
    VNEXT_BLOCKS_LEGACY,
    VNEXT_NO_ROUTE,
    VNEXT_ERROR,
    LEGACY_UNMAPPED,
}


class ShadowRoutingError(Exception):
    """Base exception for shadow-runtime configuration failures."""


@dataclass(frozen=True)
class CoachRouteBinding:
    coach: str
    route_key: str

    def __post_init__(self) -> None:
        if not self.coach.strip():
            raise ShadowRoutingError("coach must be non-empty")
        if not self.route_key.strip():
            raise ShadowRoutingError("route_key must be non-empty")


@dataclass(frozen=True)
class ShadowComparisonReceipt:
    receipt_id: str
    legacy_coach: str
    legacy_route_key: str | None
    legacy_safe_to_proceed: bool
    legacy_authoritative: bool
    vnext_selected_route_key: str | None
    comparison_state: str
    legacy_candidate_admissible_in_vnext: bool | None
    legacy_candidate_block_reasons: tuple[str, ...]
    vnext_error: str | None
    think_bundle_hash: str
    evaluated_at: float
    shadow_steering_authority: str = "NONE"
    authority_change: str = "NONE"
    warrant_change: str = "NONE"
    citation_law_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = SHADOW_ROUTER_VERSION

    def __post_init__(self) -> None:
        if self.comparison_state not in VALID_COMPARISON_STATES:
            raise ShadowRoutingError("invalid comparison_state")
        if not self.legacy_coach.strip():
            raise ShadowRoutingError("legacy_coach must be non-empty")
        if len(self.think_bundle_hash) != 64:
            raise ShadowRoutingError("think_bundle_hash must be SHA-256 hex")
        try:
            int(self.think_bundle_hash, 16)
        except ValueError as exc:
            raise ShadowRoutingError(
                "think_bundle_hash must be hexadecimal"
            ) from exc

        for name, value in (
            ("shadow_steering_authority", self.shadow_steering_authority),
            ("authority_change", self.authority_change),
            ("warrant_change", self.warrant_change),
            ("citation_law_change", self.citation_law_change),
            ("constitutional_change", self.constitutional_change),
        ):
            if value != "NONE":
                raise ShadowRoutingError(f"{name} must remain NONE")

        if not self.legacy_authoritative:
            raise ShadowRoutingError(
                "shadow runtime must preserve legacy authority"
            )

        if self.comparison_state == VNEXT_ERROR:
            if not (self.vnext_error or "").strip():
                raise ShadowRoutingError(
                    "VNEXT_ERROR requires vnext_error"
                )
        elif self.vnext_error is not None:
            raise ShadowRoutingError(
                "vnext_error may be present only for VNEXT_ERROR"
            )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "receipt_id": self.receipt_id,
            "legacy_coach": self.legacy_coach,
            "legacy_route_key": self.legacy_route_key,
            "legacy_safe_to_proceed": self.legacy_safe_to_proceed,
            "legacy_authoritative": self.legacy_authoritative,
            "vnext_selected_route_key": self.vnext_selected_route_key,
            "comparison_state": self.comparison_state,
            "legacy_candidate_admissible_in_vnext": (
                self.legacy_candidate_admissible_in_vnext
            ),
            "legacy_candidate_block_reasons": list(
                self.legacy_candidate_block_reasons
            ),
            "vnext_error": self.vnext_error,
            "think_bundle_hash": self.think_bundle_hash,
            "evaluated_at": self.evaluated_at,
            "shadow_steering_authority": self.shadow_steering_authority,
            "authority_change": self.authority_change,
            "warrant_change": self.warrant_change,
            "citation_law_change": self.citation_law_change,
            "constitutional_change": self.constitutional_change,
        }


@dataclass(frozen=True)
class ShadowRoutingResult:
    legacy_reply: CoachReply
    comparison: ShadowComparisonReceipt
    vnext_receipt: RelationalRouteReceipt | None

    def __post_init__(self) -> None:
        if not self.comparison.legacy_authoritative:
            raise ShadowRoutingError(
                "ShadowRoutingResult requires legacy authority"
            )


def run_shadow_routing(
    think_bundle: dict[str, Any],
    request: RelationalRoutingRequest,
    candidates: tuple[RouteCandidate, ...] | list[RouteCandidate],
    *,
    bindings: tuple[CoachRouteBinding, ...] | list[CoachRouteBinding],
    legacy_router: CoachRouter | None = None,
    scars: tuple[FailureScar, ...] | list[FailureScar] = (),
    successes: tuple[SuccessObservation, ...] | list[SuccessObservation] = (),
    weather: RoutingWeatherState | None = None,
    human_constraints: tuple[HumanRouteConstraint, ...] | list[HumanRouteConstraint] = (),
    policy: RelationalRoutingPolicy | None = None,
    now: float | None = None,
) -> ShadowRoutingResult:
    """Run legacy routing authoritatively and vNext in fail-isolated shadow mode."""

    evaluated_at = request.requested_at if now is None else float(now)
    router = legacy_router or CoachRouter()

    binding_map = _validate_bindings(bindings)
    # Run the currently authoritative path first and keep its reply immutable.
    legacy_reply = router.route(think_bundle)
    legacy_route_key = binding_map.get(legacy_reply.coach)

    # Keep a valid coach->route binding even when that route is absent from the
    # current vNext candidate set. Absence is itself useful shadow evidence and
    # allows VNEXT_NO_ROUTE / DIVERGE to remain distinguishable from an
    # actually unmapped legacy coach.

    try:
        vnext_receipt = route_relational(
            request,
            candidates,
            scars=scars,
            successes=successes,
            weather=weather,
            human_constraints=human_constraints,
            policy=policy,
            now=evaluated_at,
        )
    except Exception as exc:
        comparison = ShadowComparisonReceipt(
            receipt_id=str(uuid.uuid4()),
            legacy_coach=legacy_reply.coach,
            legacy_route_key=legacy_route_key,
            legacy_safe_to_proceed=legacy_reply.safe_to_proceed,
            legacy_authoritative=True,
            vnext_selected_route_key=None,
            comparison_state=VNEXT_ERROR,
            legacy_candidate_admissible_in_vnext=None,
            legacy_candidate_block_reasons=(),
            vnext_error=f"{type(exc).__name__}: {exc}",
            think_bundle_hash=_bundle_hash(think_bundle),
            evaluated_at=evaluated_at,
        )
        return ShadowRoutingResult(
            legacy_reply=legacy_reply,
            comparison=comparison,
            vnext_receipt=None,
        )

    comparison = _compare(
        legacy_reply=legacy_reply,
        legacy_route_key=legacy_route_key,
        vnext_receipt=vnext_receipt,
        think_bundle_hash=_bundle_hash(think_bundle),
        evaluated_at=evaluated_at,
    )
    return ShadowRoutingResult(
        legacy_reply=legacy_reply,
        comparison=comparison,
        vnext_receipt=vnext_receipt,
    )


def _compare(
    *,
    legacy_reply: CoachReply,
    legacy_route_key: str | None,
    vnext_receipt: RelationalRouteReceipt,
    think_bundle_hash: str,
    evaluated_at: float,
) -> ShadowComparisonReceipt:
    if legacy_route_key is None:
        return ShadowComparisonReceipt(
            receipt_id=str(uuid.uuid4()),
            legacy_coach=legacy_reply.coach,
            legacy_route_key=None,
            legacy_safe_to_proceed=legacy_reply.safe_to_proceed,
            legacy_authoritative=True,
            vnext_selected_route_key=vnext_receipt.selected_route_key,
            comparison_state=LEGACY_UNMAPPED,
            legacy_candidate_admissible_in_vnext=None,
            legacy_candidate_block_reasons=(),
            vnext_error=None,
            think_bundle_hash=think_bundle_hash,
            evaluated_at=evaluated_at,
        )

    legacy_evaluation = next(
        (
            evaluation
            for evaluation in vnext_receipt.evaluations
            if evaluation.route_key == legacy_route_key
        ),
        None,
    )

    if legacy_evaluation is not None and not legacy_evaluation.admissible:
        comparison_state = VNEXT_BLOCKS_LEGACY
        admissible = False
        block_reasons = legacy_evaluation.hard_block_reasons
    elif vnext_receipt.selected_route_key is None:
        comparison_state = VNEXT_NO_ROUTE
        admissible = (
            None if legacy_evaluation is None
            else legacy_evaluation.admissible
        )
        block_reasons = (
            ()
            if legacy_evaluation is None
            else legacy_evaluation.hard_block_reasons
        )
    elif vnext_receipt.selected_route_key == legacy_route_key:
        comparison_state = AGREE
        admissible = True if legacy_evaluation is None else legacy_evaluation.admissible
        block_reasons = ()
    else:
        comparison_state = DIVERGE
        admissible = (
            None if legacy_evaluation is None
            else legacy_evaluation.admissible
        )
        block_reasons = (
            ()
            if legacy_evaluation is None
            else legacy_evaluation.hard_block_reasons
        )

    return ShadowComparisonReceipt(
        receipt_id=str(uuid.uuid4()),
        legacy_coach=legacy_reply.coach,
        legacy_route_key=legacy_route_key,
        legacy_safe_to_proceed=legacy_reply.safe_to_proceed,
        legacy_authoritative=True,
        vnext_selected_route_key=vnext_receipt.selected_route_key,
        comparison_state=comparison_state,
        legacy_candidate_admissible_in_vnext=admissible,
        legacy_candidate_block_reasons=block_reasons,
        vnext_error=None,
        think_bundle_hash=think_bundle_hash,
        evaluated_at=evaluated_at,
    )


def _validate_bindings(
    bindings: tuple[CoachRouteBinding, ...] | list[CoachRouteBinding],
) -> dict[str, str]:
    binding_tuple = tuple(bindings)
    coaches = [binding.coach for binding in binding_tuple]
    routes = [binding.route_key for binding in binding_tuple]

    if len(coaches) != len(set(coaches)):
        raise ShadowRoutingError("each legacy coach may have only one route binding")
    if len(routes) != len(set(routes)):
        raise ShadowRoutingError("each route_key may map to only one legacy coach")

    return {
        binding.coach: binding.route_key
        for binding in binding_tuple
    }


def _bundle_hash(bundle: dict[str, Any]) -> str:
    try:
        canonical = json.dumps(
            bundle,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ShadowRoutingError(
            "think_bundle must be deterministically JSON-serializable"
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
