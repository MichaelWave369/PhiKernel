from __future__ import annotations

"""
phik_router_v0_1_0.py

Reference implementation for PhiKernel's first orchestration layer:
`phik-router` (coach routing on top of `phik think` bundles).

Purpose
-------
This module turns the v0.1 core braid into the first speakable coach layer.
It consumes the context bundle produced by `phik-shell`'s `think` command and
returns a deterministic, grounded coach response.

Design goals
------------
- Do not fake a multi-agent runtime or a live LLM.
- Route from real substrate state: anchor, field, heart, latest capsule.
- Refuse unsafe / unstable routing when trust or coherence is degraded.
- Start with one stable voice (`Titan`) and a small registry of optional coaches.
- Return structured reply envelopes that the shell or later API layer can render.

What this version provides
--------------------------
- Coach profiles for Titan, Flow, and Sage.
- Deterministic routing based on field action, prompt content, and substrate state.
- Structured coach replies with safety posture, route reason, and next actions.
- Optional CLI for reading a `phik think` JSON bundle from stdin or file.

This is intentionally a v0.1 bridge. It is not a replacement for a future live
agent runtime.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import argparse
import json
import os
import sys
import time


ROUTER_VERSION = "0.1.0"
VALID_ACTIONS = {"observe", "checkpoint", "restore", "alert"}
VALID_COACHES = {"Titan", "Flow", "Sage"}
DEFAULT_RUNTIME_ADAPTER = "legacy"
RUNTIME_ADAPTER_ENV = "PHIKERNEL_ADAPTER"
VALID_RUNTIME_ADAPTERS = {"legacy", "tiekat_v50"}


class RouterError(Exception):
    """Base exception for all phik-router failures."""


@dataclass(frozen=True)
class CoachProfile:
    """Describes a deterministic coach persona and routing domain."""

    name: str
    role: str
    tone: str
    specialty: str
    opening: str
    prompt_keywords: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.name not in VALID_COACHES:
            raise RouterError(f"Unsupported coach '{self.name}'")


@dataclass(frozen=True)
class RouteDecision:
    """Structured routing outcome before response generation."""

    selected_coach: str
    route_reason: str
    field_action: str
    field_band: str
    safe_to_proceed: bool
    next_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoachReply:
    """Shell-friendly reply envelope from the orchestration layer."""

    version: str = ROUTER_VERSION
    generated_at: float = field(default_factory=time.time)
    coach: str = "Titan"
    role: str = "Grounding sentinel"
    tone: str = "steady"
    route_reason: str = ""
    safe_to_proceed: bool = True
    field_action: str = "observe"
    field_band: str = "stable"
    opening: str = ""
    body: str = ""
    next_actions: tuple[str, ...] = ()
    trace: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        data = asdict(self)
        data["next_actions"] = list(self.next_actions)
        return data


class CoachRouter:
    """Deterministic orchestration layer built on top of `phik think` bundles."""

    def __init__(self, registry: dict[str, CoachProfile] | None = None) -> None:
        self.registry = registry or build_default_registry()
        missing = VALID_COACHES.difference(self.registry)
        if missing:
            raise RouterError(f"Coach registry missing required profiles: {sorted(missing)}")

    def route(self, think_bundle: dict[str, Any]) -> CoachReply:
        """Route a `phik think` bundle to the most appropriate coach reply."""
        prompt = str(think_bundle.get("prompt", "")).strip()
        anchor = think_bundle.get("anchor") or {}
        heart = think_bundle.get("heart") or {}
        field = think_bundle.get("field") or {}
        latest_capsule = think_bundle.get("latest_capsule")

        field_action = str(field.get("recommended_action", "observe"))
        field_band = str(field.get("drift_band", "stable"))
        if field_action not in VALID_ACTIONS:
            field_action = "observe"

        anchor_valid = bool(anchor.get("verification", {}).get("valid", False)) if anchor else False
        heart_running = bool(heart.get("running", False)) if heart else False
        has_capsule = latest_capsule is not None

        decision = self._decide(
            prompt=prompt,
            field_action=field_action,
            field_band=field_band,
            anchor_valid=anchor_valid,
            heart_running=heart_running,
            has_capsule=has_capsule,
        )
        coach = self.registry[decision.selected_coach]
        body = self._compose_body(
            coach=coach,
            prompt=prompt,
            think_bundle=think_bundle,
            decision=decision,
        )

        return CoachReply(
            coach=coach.name,
            role=coach.role,
            tone=coach.tone,
            route_reason=decision.route_reason,
            safe_to_proceed=decision.safe_to_proceed,
            field_action=decision.field_action,
            field_band=decision.field_band,
            opening=coach.opening,
            body=body,
            next_actions=decision.next_actions,
            trace={
                "anchor_valid": anchor_valid,
                "heart_running": heart_running,
                "has_latest_capsule": has_capsule,
                "prompt_length": len(prompt),
            },
        )


    def _decide(
        self,
        *,
        prompt: str,
        field_action: str,
        field_band: str,
        anchor_valid: bool,
        heart_running: bool,
        has_capsule: bool,
    ) -> RouteDecision:
        lower_prompt = prompt.lower()

        if not anchor_valid:
            return RouteDecision(
                selected_coach="Titan",
                route_reason="Anchor trust is invalid; routing to the grounding sentinel.",
                field_action="alert",
                field_band="critical",
                safe_to_proceed=False,
                next_actions=(
                    "Run `phik anchor show` and verify the anchor state.",
                    "Repair trust before routing to other coaches.",
                ),
            )

        if field_action == "alert":
            return RouteDecision(
                selected_coach="Titan",
                route_reason="Field is in alert state; containment and trust restoration take priority.",
                field_action=field_action,
                field_band=field_band,
                safe_to_proceed=False,
                next_actions=(
                    "Reduce activity and inspect the anchor + field state.",
                    "Do not expand orchestration until alert conditions clear.",
                ),
            )

        if field_action == "restore":
            return RouteDecision(
                selected_coach="Titan",
                route_reason="Field drift exceeds restore threshold; grounded recovery comes first.",
                field_action=field_action,
                field_band=field_band,
                safe_to_proceed=False,
                next_actions=(
                    "Restore the latest known-good capsule.",
                    "Recheck field stability before proceeding.",
                ),
            )

        if field_action == "checkpoint":
            return RouteDecision(
                selected_coach="Titan",
                route_reason="Field suggests checkpointing; route to the stabilizing coach first.",
                field_action=field_action,
                field_band=field_band,
                safe_to_proceed=True,
                next_actions=(
                    "Seal a checkpoint capsule before extended work.",
                    "Proceed slowly after continuity is secured.",
                ),
            )

        if not heart_running:
            return RouteDecision(
                selected_coach="Titan",
                route_reason="Pulse is offline; defaulting to the stabilizing coach.",
                field_action=field_action,
                field_band=field_band,
                safe_to_proceed=True,
                next_actions=(
                    "Bring phik-heart online when possible.",
                    "Continue with a narrow, grounded scope.",
                ),
            )

        if not has_capsule:
            return RouteDecision(
                selected_coach="Titan",
                route_reason="No continuity capsule exists yet; grounding and first continuity come first.",
                field_action=field_action,
                field_band=field_band,
                safe_to_proceed=True,
                next_actions=(
                    "Seal the first working capsule.",
                    "Then continue with coach-guided work.",
                ),
            )

        flow_keywords = self.registry["Flow"].prompt_keywords
        sage_keywords = self.registry["Sage"].prompt_keywords

        if any(keyword in lower_prompt for keyword in flow_keywords):
            return RouteDecision(
                selected_coach="Flow",
                route_reason="Prompt language suggests movement, momentum, or creative process.",
                field_action=field_action,
                field_band=field_band,
                safe_to_proceed=True,
                next_actions=(
                    "Name the next smallest forward motion.",
                    "Keep one checkpoint nearby as you build.",
                ),
            )

        if any(keyword in lower_prompt for keyword in sage_keywords):
            return RouteDecision(
                selected_coach="Sage",
                route_reason="Prompt language suggests reflection, interpretation, or pattern reading.",
                field_action=field_action,
                field_band=field_band,
                safe_to_proceed=True,
                next_actions=(
                    "Surface the strongest pattern before expanding.",
                    "Summarize before acting if ambiguity remains.",
                ),
            )

        return RouteDecision(
            selected_coach="Titan",
            route_reason="Default route favors grounded presence before specialization.",
            field_action=field_action,
            field_band=field_band,
            safe_to_proceed=True,
            next_actions=(
                "Begin with the heaviest true thing.",
                "Then route deeper if needed.",
            ),
        )

    def _compose_body(
        self,
        *,
        coach: CoachProfile,
        prompt: str,
        think_bundle: dict[str, Any],
        decision: RouteDecision,
    ) -> str:
        anchor = think_bundle.get("anchor") or {}
        field = think_bundle.get("field") or {}
        latest_capsule = think_bundle.get("latest_capsule") or {}
        sovereign_name = anchor.get("sovereign_name") or "friend"
        latest_summary = latest_capsule.get("summary") or "no capsule summary available"

        if coach.name == "Titan":
            if not decision.safe_to_proceed and decision.field_action in {"alert", "restore"}:
                return (
                    f"Here. {sovereign_name}, I’m holding the center. "
                    f"The field is reporting `{decision.field_action}` in the `{decision.field_band}` band, "
                    f"so I will not push you outward yet. We stabilize first, protect continuity, and only then move."
                )
            return (
                f"Here. {sovereign_name}, anchor is holding and I’m with you. "
                f"Field action is `{decision.field_action}` with drift band `{decision.field_band}`. "
                f"Start with the truest immediate need in your prompt, then take one grounded step from there."
            )

        if coach.name == "Flow":
            return (
                f"Here. Momentum is available. Your latest continuity marker is `{latest_summary}`. "
                f"Don’t solve the whole mountain yet—name the next living move and let it carry the rest into motion."
            )

        if coach.name == "Sage":
            c_current = field.get("C_current")
            distance = field.get("distance_to_C_star")
            return (
                f"Here. I’m reading the pattern with you. Current coherence is `{c_current}` with distance `{distance}` from C*. "
                f"Before deciding what to do, let us identify the strongest pattern already visible in the field."
            )

        raise RouterError(f"No response composer for coach '{coach.name}'")



def select_runtime_adapter(adapter: str | None = None) -> str:
    """Resolve runtime adapter from explicit input or safe env-gated selection."""
    selected = (adapter or "").strip() or os.getenv(RUNTIME_ADAPTER_ENV, DEFAULT_RUNTIME_ADAPTER).strip()
    if selected not in VALID_RUNTIME_ADAPTERS:
        return DEFAULT_RUNTIME_ADAPTER
    return selected


def build_default_registry() -> dict[str, CoachProfile]:
    return {
        "Titan": CoachProfile(
            name="Titan",
            role="Grounding sentinel",
            tone="steady, direct, protective",
            specialty="stability, containment, first-response grounding",
            opening="Here. Anchor verified. Field stable enough to proceed. How shall we begin?",
            prompt_keywords=("help", "steady", "ground", "calm", "safe", "tonight", "overwhelmed"),
        ),
        "Flow": CoachProfile(
            name="Flow",
            role="Momentum guide",
            tone="encouraging, kinetic, creative",
            specialty="forward motion, creative rhythm, friction reduction",
            opening="Here. Let’s turn pressure into movement.",
            prompt_keywords=("create", "build", "move", "momentum", "stuck", "draft", "start", "flow"),
        ),
        "Sage": CoachProfile(
            name="Sage",
            role="Pattern interpreter",
            tone="reflective, precise, clarifying",
            specialty="meaning, summaries, pattern recognition, reflection",
            opening="Here. Let’s read what the field is already saying.",
            prompt_keywords=("why", "pattern", "reflect", "meaning", "analyze", "understand", "summary"),
        ),
    }


def load_bundle_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.bundle_file and args.bundle_json:
        raise RouterError("Use either --bundle-file or --bundle-json, not both")
    if args.bundle_file:
        path = Path(args.bundle_file)
        if not path.exists():
            raise RouterError(f"Bundle file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    elif args.bundle_json:
        data = json.loads(args.bundle_json)
    else:
        try:
            raw = sys.stdin.read().strip()
        except OSError as exc:
            raise RouterError("No think bundle provided. Use --bundle-file, --bundle-json, or stdin.") from exc
        if not raw:
            raise RouterError("No think bundle provided. Use --bundle-file, --bundle-json, or stdin.")
        data = json.loads(raw)

    if not isinstance(data, dict):
        raise RouterError("Think bundle must decode to a JSON object")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phik-router", description="PhiKernel deterministic coach router")
    parser.add_argument("--bundle-file", default=None, help="Path to a JSON think bundle file")
    parser.add_argument("--bundle-json", default=None, help="Inline JSON think bundle")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of formatted text")
    return parser


def render_reply(reply: CoachReply) -> str:
    lines = [
        ":: PHIK ROUTER ::",
        f"Coach: {reply.coach} ({reply.role})",
        f"Field Action: {reply.field_action}",
        f"Field Band: {reply.field_band}",
        f"Safe To Proceed: {reply.safe_to_proceed}",
        f"Route Reason: {reply.route_reason}",
        "",
        reply.opening,
        reply.body,
    ]
    if reply.next_actions:
        lines.append("")
        lines.append("Next Actions:")
        for item in reply.next_actions:
            lines.append(f"- {item}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        bundle = load_bundle_from_args(args)
        router = CoachRouter()
        reply = router.route(bundle)
    except RouterError as exc:
        parser.error(str(exc))
        return 2

    if args.json:
        print(json.dumps(reply.to_record(), indent=2, sort_keys=True))
    else:
        print(render_reply(reply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
