from __future__ import annotations

"""PhiKernel constitutional execution orchestrator.

This module composes the routing and control layers into one mode-aware runtime
entry point without executing external side effects itself.

Modes
-----
SHADOW
    Legacy routing remains authoritative. Crane Fly vNext observes only.

ADVISE
    Legacy routing remains authoritative. Crane Fly vNext may surface an
    advisory route, but it has zero steering authority.

BOUNDED_CONTROL
    Crane Fly vNext may become the steering route only when:
    - runtime control state is not sealed/quarantined/recovery-blocked,
    - no operator review hold is active,
    - a live ControlSession and matching ControlContract are present,
    - the selected relational route is bound to the same actor, operation,
      target, and dedicated control warrant as the ControlActionRequest,
    - license_control_action() returns LICENSE.

Any terminal bounded-control refusal stops the session and collapses promotion
state back to SHADOW. There is never a silent fallback to legacy execution
after a control refusal.

The orchestrator grants no new authority. It only consumes already-authorized
state and emits receipts describing what may proceed.
"""

from dataclasses import dataclass
from typing import Any
import time

from phikernel.control_state import RuntimeControlState
from phikernel.control_witness import (
    BoundedControlGrant,
    ControlActionLicenseReceipt,
    ControlActionRequest,
    ControlContract,
    ControlSession,
    ControlStopReceipt,
    collapse_control_to_shadow,
    license_control_action,
    terminate_control_session,
)
from phikernel.mutability import RoutingWeatherState
from phikernel.relational_router import (
    HumanRouteConstraint,
    RelationalRouteReceipt,
    RelationalRoutingPolicy,
    RelationalRoutingRequest,
    RouteCandidate,
)
from phikernel.router import CoachReply, CoachRouter
from phikernel.routing_shadow import (
    ShadowComparisonReceipt,
    run_shadow_routing,
)
from phikernel.saturation import SuccessObservation
from phikernel.scar import FailureScar
from phikernel.witness_bench import (
    ADVISE,
    BOUNDED_CONTROL,
    SHADOW,
    PromotionCollapseReceipt,
    PromotionState,
    collapse_to_shadow,
)


ORCHESTRATOR_VERSION = "0.2.0"

LEGACY_SHADOW = "LEGACY_SHADOW"
LEGACY_WITH_ADVICE = "LEGACY_WITH_ADVICE"
BOUNDED_LICENSED = "BOUNDED_LICENSED"
BOUNDED_REFUSED = "BOUNDED_REFUSED"
BOUNDED_UNAVAILABLE = "BOUNDED_UNAVAILABLE"
BOUNDED_REVIEW_HOLD = "BOUNDED_REVIEW_HOLD"
RUNTIME_BLOCKED = "RUNTIME_BLOCKED"

VALID_DISPOSITIONS = {
    LEGACY_SHADOW,
    LEGACY_WITH_ADVICE,
    BOUNDED_LICENSED,
    BOUNDED_REFUSED,
    BOUNDED_UNAVAILABLE,
    BOUNDED_REVIEW_HOLD,
    RUNTIME_BLOCKED,
}


class ConstitutionalRuntimeError(Exception):
    """Raised for invalid orchestrator inputs, not policy refusals."""


@dataclass(frozen=True)
class ConstitutionalRuntimeResult:
    disposition: str
    mode_before: str
    mode_after: str
    legacy_reply: CoachReply | None
    legacy_route_key: str | None
    advisory_route_key: str | None
    steering_route_key: str | None
    execution_licensed: bool
    reason: str
    shadow_comparison: ShadowComparisonReceipt | None
    vnext_receipt: RelationalRouteReceipt | None
    control_license_receipt: ControlActionLicenseReceipt | None
    control_stop_receipt: ControlStopReceipt | None
    collapse_receipt: PromotionCollapseReceipt | None
    promotion_state_after: PromotionState
    control_session_after: ControlSession | None
    evaluated_at: float
    authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = ORCHESTRATOR_VERSION

    def __post_init__(self) -> None:
        if self.disposition not in VALID_DISPOSITIONS:
            raise ConstitutionalRuntimeError("invalid orchestrator disposition")
        if self.mode_before not in {SHADOW, ADVISE, BOUNDED_CONTROL}:
            raise ConstitutionalRuntimeError("invalid mode_before")
        if self.mode_after not in {SHADOW, ADVISE, BOUNDED_CONTROL}:
            raise ConstitutionalRuntimeError("invalid mode_after")
        if self.promotion_state_after.mode != self.mode_after:
            raise ConstitutionalRuntimeError(
                "promotion_state_after does not match mode_after"
            )
        if self.execution_licensed:
            if self.disposition != BOUNDED_LICENSED:
                raise ConstitutionalRuntimeError(
                    "execution_licensed requires BOUNDED_LICENSED"
                )
            if not self.steering_route_key:
                raise ConstitutionalRuntimeError(
                    "licensed execution requires steering_route_key"
                )
            if (
                self.control_license_receipt is None
                or not self.control_license_receipt.allowed
            ):
                raise ConstitutionalRuntimeError(
                    "licensed execution requires allowed control receipt"
                )
        elif self.steering_route_key is not None:
            raise ConstitutionalRuntimeError(
                "steering route may exist only for licensed execution"
            )

        if self.disposition in {
            BOUNDED_REFUSED,
            RUNTIME_BLOCKED,
        } and self.mode_before == BOUNDED_CONTROL:
            if self.mode_after != SHADOW:
                raise ConstitutionalRuntimeError(
                    "terminal bounded-control refusal/block must collapse to SHADOW"
                )

        if self.authority_change != "NONE":
            raise ConstitutionalRuntimeError(
                "orchestrator may not expand authority"
            )
        if self.constitutional_change != "NONE":
            raise ConstitutionalRuntimeError(
                "orchestrator may not change constitution"
            )

    @property
    def legacy_response_authoritative(self) -> bool:
        return self.disposition in {
            LEGACY_SHADOW,
            LEGACY_WITH_ADVICE,
            BOUNDED_REVIEW_HOLD,
        }

    @property
    def steering_authority_active(self) -> bool:
        return self.execution_licensed and self.mode_after == BOUNDED_CONTROL

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "disposition": self.disposition,
            "mode_before": self.mode_before,
            "mode_after": self.mode_after,
            "legacy_route_key": self.legacy_route_key,
            "advisory_route_key": self.advisory_route_key,
            "steering_route_key": self.steering_route_key,
            "execution_licensed": self.execution_licensed,
            "legacy_response_authoritative": self.legacy_response_authoritative,
            "steering_authority_active": self.steering_authority_active,
            "reason": self.reason,
            "shadow_comparison": (
                None
                if self.shadow_comparison is None
                else self.shadow_comparison.to_record()
            ),
            "vnext_receipt": (
                None if self.vnext_receipt is None else self.vnext_receipt.to_record()
            ),
            "control_license_receipt_id": (
                None
                if self.control_license_receipt is None
                else self.control_license_receipt.receipt_id
            ),
            "control_stop_receipt_id": (
                None
                if self.control_stop_receipt is None
                else self.control_stop_receipt.receipt_id
            ),
            "collapse_receipt_id": (
                None
                if self.collapse_receipt is None
                else self.collapse_receipt.receipt_id
            ),
            "promotion_revision_after": self.promotion_state_after.revision,
            "control_session_id": (
                None
                if self.control_session_after is None
                else self.control_session_after.session_id
            ),
            "evaluated_at": self.evaluated_at,
            "authority_change": self.authority_change,
            "constitutional_change": self.constitutional_change,
        }


def orchestrate_runtime(
    think_bundle: dict[str, Any],
    promotion_state: PromotionState,
    request: RelationalRoutingRequest,
    candidates: tuple[RouteCandidate, ...] | list[RouteCandidate],
    *,
    bindings,
    runtime_control_state: RuntimeControlState | None = None,
    legacy_router: CoachRouter | None = None,
    scars: tuple[FailureScar, ...] | list[FailureScar] = (),
    successes: tuple[SuccessObservation, ...] | list[SuccessObservation] = (),
    weather: RoutingWeatherState | None = None,
    human_constraints: tuple[HumanRouteConstraint, ...] | list[HumanRouteConstraint] = (),
    policy: RelationalRoutingPolicy | None = None,
    control_contract: ControlContract | None = None,
    control_grant: BoundedControlGrant | None = None,
    control_session: ControlSession | None = None,
    control_action: ControlActionRequest | None = None,
    now: float | None = None,
) -> ConstitutionalRuntimeResult:
    """Evaluate one runtime routing/control turn under the current promotion mode.

    This function licenses bounded-control actions but does not execute external
    side effects. An external executor must consume the returned control license
    and later submit the post-action outcome receipt through control_witness.
    """

    evaluated_at = request.requested_at if now is None else float(now)
    runtime_state = runtime_control_state or RuntimeControlState()

    if promotion_state.mode not in {SHADOW, ADVISE, BOUNDED_CONTROL}:
        raise ConstitutionalRuntimeError("unsupported promotion mode")

    blocked_reason = _runtime_block_reason(runtime_state)
    if blocked_reason is not None:
        if promotion_state.mode == BOUNDED_CONTROL and control_session is not None:
            (
                state_after,
                session_after,
                stop_receipt,
                collapse_receipt,
            ) = _stop_and_collapse(
                promotion_state,
                control_session,
                source_ref="runtime-control-state",
                reason=blocked_reason,
                at=evaluated_at,
            )
        elif promotion_state.mode == BOUNDED_CONTROL:
            state_after, collapse_receipt = collapse_to_shadow(
                promotion_state,
                source_ref="runtime-control-state",
                reason=blocked_reason,
                collapsed_at=evaluated_at,
            )
            session_after = None
            stop_receipt = None
        else:
            state_after = promotion_state
            session_after = control_session
            stop_receipt = None
            collapse_receipt = None

        return ConstitutionalRuntimeResult(
            disposition=RUNTIME_BLOCKED,
            mode_before=promotion_state.mode,
            mode_after=state_after.mode,
            legacy_reply=None,
            legacy_route_key=None,
            advisory_route_key=None,
            steering_route_key=None,
            execution_licensed=False,
            reason=blocked_reason,
            shadow_comparison=None,
            vnext_receipt=None,
            control_license_receipt=None,
            control_stop_receipt=stop_receipt,
            collapse_receipt=collapse_receipt,
            promotion_state_after=state_after,
            control_session_after=session_after,
            evaluated_at=evaluated_at,
        )

    shadow = run_shadow_routing(
        think_bundle,
        request,
        candidates,
        bindings=bindings,
        legacy_router=legacy_router,
        scars=scars,
        successes=successes,
        weather=weather,
        human_constraints=human_constraints,
        policy=policy,
        now=evaluated_at,
    )

    legacy_route_key = shadow.comparison.legacy_route_key
    advisory_route_key = (
        None
        if shadow.vnext_receipt is None
        else shadow.vnext_receipt.selected_route_key
    )

    if promotion_state.mode == SHADOW:
        return ConstitutionalRuntimeResult(
            disposition=LEGACY_SHADOW,
            mode_before=SHADOW,
            mode_after=SHADOW,
            legacy_reply=shadow.legacy_reply,
            legacy_route_key=legacy_route_key,
            advisory_route_key=None,
            steering_route_key=None,
            execution_licensed=False,
            reason="Crane Fly vNext observed in shadow; legacy routing remains authoritative",
            shadow_comparison=shadow.comparison,
            vnext_receipt=shadow.vnext_receipt,
            control_license_receipt=None,
            control_stop_receipt=None,
            collapse_receipt=None,
            promotion_state_after=promotion_state,
            control_session_after=None,
            evaluated_at=evaluated_at,
        )

    if promotion_state.mode == ADVISE:
        return ConstitutionalRuntimeResult(
            disposition=LEGACY_WITH_ADVICE,
            mode_before=ADVISE,
            mode_after=ADVISE,
            legacy_reply=shadow.legacy_reply,
            legacy_route_key=legacy_route_key,
            advisory_route_key=advisory_route_key,
            steering_route_key=None,
            execution_licensed=False,
            reason=(
                "Crane Fly vNext recommendation is advisory only; "
                "legacy routing remains authoritative"
            ),
            shadow_comparison=shadow.comparison,
            vnext_receipt=shadow.vnext_receipt,
            control_license_receipt=None,
            control_stop_receipt=None,
            collapse_receipt=None,
            promotion_state_after=promotion_state,
            control_session_after=None,
            evaluated_at=evaluated_at,
        )

    # BOUNDED_CONTROL from here onward.
    if runtime_state.review_required or runtime_state.recovery_required:
        return ConstitutionalRuntimeResult(
            disposition=BOUNDED_REVIEW_HOLD,
            mode_before=BOUNDED_CONTROL,
            mode_after=BOUNDED_CONTROL,
            legacy_reply=shadow.legacy_reply,
            legacy_route_key=legacy_route_key,
            advisory_route_key=advisory_route_key,
            steering_route_key=None,
            execution_licensed=False,
            reason=(
                "bounded steering held because runtime operator/recovery review "
                "is required"
            ),
            shadow_comparison=shadow.comparison,
            vnext_receipt=shadow.vnext_receipt,
            control_license_receipt=None,
            control_stop_receipt=None,
            collapse_receipt=None,
            promotion_state_after=promotion_state,
            control_session_after=control_session,
            evaluated_at=evaluated_at,
        )

    if (
        control_contract is None
        or control_grant is None
        or control_session is None
        or control_action is None
    ):
        return ConstitutionalRuntimeResult(
            disposition=BOUNDED_UNAVAILABLE,
            mode_before=BOUNDED_CONTROL,
            mode_after=BOUNDED_CONTROL,
            legacy_reply=shadow.legacy_reply,
            legacy_route_key=legacy_route_key,
            advisory_route_key=advisory_route_key,
            steering_route_key=None,
            execution_licensed=False,
            reason=(
                "bounded control requires a live contract, human-authorized "
                "grant, session, and exact control action request"
            ),
            shadow_comparison=shadow.comparison,
            vnext_receipt=shadow.vnext_receipt,
            control_license_receipt=None,
            control_stop_receipt=None,
            collapse_receipt=None,
            promotion_state_after=promotion_state,
            control_session_after=control_session,
            evaluated_at=evaluated_at,
        )

    if shadow.vnext_receipt is None:
        return _terminal_bounded_failure(
            promotion_state=promotion_state,
            session=control_session,
            legacy_reply=shadow.legacy_reply,
            legacy_route_key=legacy_route_key,
            advisory_route_key=None,
            shadow_comparison=shadow.comparison,
            vnext_receipt=None,
            reason="relational routing failed during bounded control",
            at=evaluated_at,
        )

    selected_route_key = shadow.vnext_receipt.selected_route_key
    if selected_route_key is None:
        return _terminal_bounded_failure(
            promotion_state=promotion_state,
            session=control_session,
            legacy_reply=shadow.legacy_reply,
            legacy_route_key=legacy_route_key,
            advisory_route_key=None,
            shadow_comparison=shadow.comparison,
            vnext_receipt=shadow.vnext_receipt,
            reason="no admissible relational route exists for bounded control",
            at=evaluated_at,
        )

    selected = next(
        (candidate for candidate in candidates if candidate.route_key == selected_route_key),
        None,
    )
    if selected is None:
        raise ConstitutionalRuntimeError(
            "relational receipt selected a route absent from candidate set"
        )

    integrity_reason = _control_binding_failure(
        promotion_state,
        selected,
        control_contract,
        control_grant,
        control_session,
        control_action,
    )
    if integrity_reason is not None:
        return _terminal_bounded_failure(
            promotion_state=promotion_state,
            session=control_session,
            legacy_reply=shadow.legacy_reply,
            legacy_route_key=legacy_route_key,
            advisory_route_key=selected_route_key,
            shadow_comparison=shadow.comparison,
            vnext_receipt=shadow.vnext_receipt,
            reason=integrity_reason,
            at=evaluated_at,
        )

    (
        session_after,
        license_receipt,
        _transition_evaluation,
    ) = license_control_action(
        control_session,
        control_contract,
        control_action,
        now=evaluated_at,
    )

    if not license_receipt.allowed:
        collapsed_state, collapse_receipt = collapse_control_to_shadow(
            promotion_state,
            session_after,
            source_ref=license_receipt.receipt_id,
            reason=license_receipt.reason,
            collapsed_at=evaluated_at,
        )
        return ConstitutionalRuntimeResult(
            disposition=BOUNDED_REFUSED,
            mode_before=BOUNDED_CONTROL,
            mode_after=SHADOW,
            legacy_reply=shadow.legacy_reply,
            legacy_route_key=legacy_route_key,
            advisory_route_key=selected_route_key,
            steering_route_key=None,
            execution_licensed=False,
            reason=license_receipt.reason,
            shadow_comparison=shadow.comparison,
            vnext_receipt=shadow.vnext_receipt,
            control_license_receipt=license_receipt,
            control_stop_receipt=None,
            collapse_receipt=collapse_receipt,
            promotion_state_after=collapsed_state,
            control_session_after=session_after,
            evaluated_at=evaluated_at,
        )

    return ConstitutionalRuntimeResult(
        disposition=BOUNDED_LICENSED,
        mode_before=BOUNDED_CONTROL,
        mode_after=BOUNDED_CONTROL,
        legacy_reply=shadow.legacy_reply,
        legacy_route_key=legacy_route_key,
        advisory_route_key=selected_route_key,
        steering_route_key=selected_route_key,
        execution_licensed=True,
        reason=(
            "relational route selected and exact bounded-control action licensed; "
            "external executor may perform only that receipted action"
        ),
        shadow_comparison=shadow.comparison,
        vnext_receipt=shadow.vnext_receipt,
        control_license_receipt=license_receipt,
        control_stop_receipt=None,
        collapse_receipt=None,
        promotion_state_after=promotion_state,
        control_session_after=session_after,
        evaluated_at=evaluated_at,
    )


def _runtime_block_reason(state: RuntimeControlState) -> str | None:
    if state.sealed:
        return "runtime is sealed; constitutional execution is blocked"
    if state.quarantined:
        return "runtime is quarantined; constitutional execution is blocked"
    return None


def _control_binding_failure(
    promotion_state: PromotionState,
    selected: RouteCandidate,
    contract: ControlContract,
    grant: BoundedControlGrant,
    session: ControlSession,
    action: ControlActionRequest,
) -> str | None:
    if promotion_state.authorized_by_seal_id != grant.human_seal_id:
        return "promotion state human seal lineage does not match control grant"
    if session.grant_id != grant.grant_id:
        return "control session does not belong to supplied control grant"
    if grant.contract_id != contract.contract_id:
        return "control grant references different contract"
    if grant.contract_hash != contract.contract_hash:
        return "control grant contract hash mismatch"
    if grant.actor_id != contract.actor_id:
        return "control grant actor does not match contract actor"
    if grant.warrant.warrant_id != session.warrant.warrant_id:
        return "control session warrant identity does not match control grant"
    if tuple(grant.warrant.scopes) != contract.scopes:
        return "control grant warrant scopes do not exactly match contract"
    if tuple(session.warrant.scopes) != contract.scopes:
        return "control session warrant scopes do not exactly match contract"
    if (
        session.warrant.metadata.get("control_contract_hash")
        != contract.contract_hash
    ):
        return "control session warrant is not bound to exact contract hash"
    if selected.actor_id != action.actor_id:
        return "selected route actor does not match control action actor"
    if selected.actor_id != session.actor_id:
        return "selected route actor does not match control session actor"
    if selected.operation != action.operation:
        return "selected route operation does not match control action"
    if selected.target != action.target:
        return "selected route target does not match control action"
    if selected.warrant.warrant_id != session.warrant.warrant_id:
        return (
            "selected route warrant is not the dedicated bounded-control "
            "session warrant"
        )
    return None


def _terminal_bounded_failure(
    *,
    promotion_state: PromotionState,
    session: ControlSession,
    legacy_reply: CoachReply,
    legacy_route_key: str | None,
    advisory_route_key: str | None,
    shadow_comparison: ShadowComparisonReceipt,
    vnext_receipt: RelationalRouteReceipt | None,
    reason: str,
    at: float,
) -> ConstitutionalRuntimeResult:
    (
        state_after,
        session_after,
        stop_receipt,
        collapse_receipt,
    ) = _stop_and_collapse(
        promotion_state,
        session,
        source_ref="constitutional-orchestrator",
        reason=reason,
        at=at,
    )
    return ConstitutionalRuntimeResult(
        disposition=BOUNDED_REFUSED,
        mode_before=BOUNDED_CONTROL,
        mode_after=SHADOW,
        legacy_reply=legacy_reply,
        legacy_route_key=legacy_route_key,
        advisory_route_key=advisory_route_key,
        steering_route_key=None,
        execution_licensed=False,
        reason=reason,
        shadow_comparison=shadow_comparison,
        vnext_receipt=vnext_receipt,
        control_license_receipt=None,
        control_stop_receipt=stop_receipt,
        collapse_receipt=collapse_receipt,
        promotion_state_after=state_after,
        control_session_after=session_after,
        evaluated_at=at,
    )


def _stop_and_collapse(
    promotion_state: PromotionState,
    session: ControlSession,
    *,
    source_ref: str,
    reason: str,
    at: float,
) -> tuple[
    PromotionState,
    ControlSession,
    ControlStopReceipt | None,
    PromotionCollapseReceipt,
]:
    if promotion_state.mode != BOUNDED_CONTROL:
        raise ConstitutionalRuntimeError(
            "stop-and-collapse requires BOUNDED_CONTROL state"
        )

    stop_receipt: ControlStopReceipt | None = None
    stopped = session
    if not stopped.stopped:
        stopped, stop_receipt = terminate_control_session(
            stopped,
            source_ref=source_ref,
            reason=reason,
            stopped_at=at,
        )

    collapsed, collapse_receipt = collapse_control_to_shadow(
        promotion_state,
        stopped,
        source_ref=(
            stop_receipt.receipt_id
            if stop_receipt is not None
            else source_ref
        ),
        reason=reason,
        collapsed_at=at,
    )
    return collapsed, stopped, stop_receipt, collapse_receipt
