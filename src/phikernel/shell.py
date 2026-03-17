from __future__ import annotations

"""
phik_shell_v0_1_2.py

Reference implementation for PhiKernel's XiOS-facing CLI / terminal bridge,
now with native routing integration.

What this version adds over v0.1.1
----------------------------------
- `phik route <prompt>` -> build a think bundle and route it to the coach layer.
- `phik ask <prompt>`   -> friendly alias for `route`.
- In-memory integration with `phik_router_v0_1_0.py`; no fake subprocess needed.
- Shared rendering path for router replies in text or JSON mode.

Existing commands retained
--------------------------
- `phik status`
- `phik field`
- `phik anchor show`
- `phik capsule list`
- `phik capsule seal`
- `phik capsule restore <capsule_id>`
- `phik think`
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import argparse
import json
import sys
import time

from phikernel.router import CoachReply, CoachRouter, render_reply, select_runtime_adapter


DEFAULT_SHELL_VERSION = "0.1.2"
DEFAULT_RUNTIME_ROOT = Path("./.phik-runtime")


class ShellError(Exception):
    """Base exception for all phik-shell failures."""


@dataclass(frozen=True)
class RuntimePaths:
    runtime_root: Path
    anchor_root: Path
    capsule_root: Path
    heart_root: Path
    coherence_root: Path


class PhiKernelShell:
    """Human-facing CLI bridge for the local PhiKernel runtime."""

    def __init__(self, paths: RuntimePaths, router: CoachRouter | None = None) -> None:
        self.paths = paths
        self.anchor_service = None
        self.capsule_store = None
        self._anchor_not_initialized_error: type[Exception] | None = None
        self._anchor_already_exists_error: type[Exception] | None = None
        self._capsule_not_found_error: type[Exception] | None = None
        self.heart_status_file = paths.heart_root / "heart_status.json"
        self.coherence_frame_file = paths.coherence_root / "coherence_frame.json"
        self.router = router or CoachRouter()
        self.runtime_bridge = None

    def _ensure_runtime_services(self) -> None:
        if self.anchor_service is not None and self.capsule_store is not None:
            return

        from phikernel.anchor import AnchorAlreadyExistsError, AnchorNotInitializedError, StateAnchorService
        from phikernel.capsule import CapsuleNotFoundError, ContinuityCapsuleStore

        self.anchor_service = StateAnchorService(self.paths.anchor_root)
        self.capsule_store = ContinuityCapsuleStore(self.paths.capsule_root, self.anchor_service)
        from phikernel.heart import RuntimeBridge

        self.runtime_bridge = RuntimeBridge()
        self._anchor_not_initialized_error = AnchorNotInitializedError
        self._anchor_already_exists_error = AnchorAlreadyExistsError
        self._capsule_not_found_error = CapsuleNotFoundError

    def cmd_init(self, args: argparse.Namespace) -> dict[str, Any]:
        self.paths.runtime_root.mkdir(parents=True, exist_ok=True)
        self.paths.anchor_root.mkdir(parents=True, exist_ok=True)
        self.paths.capsule_root.mkdir(parents=True, exist_ok=True)
        self.paths.heart_root.mkdir(parents=True, exist_ok=True)
        self.paths.coherence_root.mkdir(parents=True, exist_ok=True)
        self._ensure_runtime_services()

        try:
            self.anchor_service.initialize(
                passphrase=args.passphrase,
                sovereign_name=args.sovereign_name,
                user_label=args.user_label,
                resonant_label=args.resonant_label,
            )
        except self._anchor_already_exists_error as exc:
            raise ShellError("Anchor already exists at the configured runtime. Refusing to overwrite.") from exc

        anchor_status = self.anchor_service.public_status()
        verification = anchor_status.get("verification", {})
        return {
            "anchor_id": anchor_status.get("anchor_id"),
            "sovereign_name": anchor_status.get("sovereign_name"),
            "user_label": anchor_status.get("user_label"),
            "target_attractor": anchor_status.get("target_attractor"),
            "frequency_anchor_hz": anchor_status.get("frequency_anchor_hz"),
            "verification": {
                "valid": verification.get("valid"),
                "reason": verification.get("reason"),
            },
        }

    def cmd_pulse_once(self, args: argparse.Namespace) -> dict[str, Any]:
        self.paths.runtime_root.mkdir(parents=True, exist_ok=True)
        self.paths.anchor_root.mkdir(parents=True, exist_ok=True)
        self.paths.capsule_root.mkdir(parents=True, exist_ok=True)
        self.paths.heart_root.mkdir(parents=True, exist_ok=True)
        self.paths.coherence_root.mkdir(parents=True, exist_ok=True)
        self._ensure_runtime_services()

        _ = self._safe_anchor_status(required=True)

        if args.checkpoint and not args.passphrase:
            raise ShellError("pulse once --checkpoint requires --passphrase")

        from phikernel.coherence import CoherenceService
        from phikernel.heart import HeartbeatService

        coherence_service = CoherenceService(self.paths.coherence_root)

        def coherence_state_provider() -> dict[str, Any]:
            anchor_status = self.anchor_service.public_status()
            capsules = self.capsule_store.list_capsules()
            return {
                "anchor_id": anchor_status["anchor_id"],
                "anchor_valid": anchor_status["verification"]["valid"],
                "heartbeat_running": True,
                "capsule_store_configured": True,
                "capsule_count": len(capsules),
                "active_threads": 1,
                "pending_events": 0,
                "unresolved_alerts": 0,
                "last_checkpoint_age_seconds": 0.0,
                "checkpoint_due": False,
                "notes": ["pulse once"],
            }

        coherence_provider = coherence_service.make_heart_provider(coherence_state_provider)

        heart = HeartbeatService(
            self.paths.heart_root,
            anchor_service=self.anchor_service,
            capsule_store=self.capsule_store,
        )

        checkpoint_capsule_id = None
        checkpoint_state_provider = None
        if args.checkpoint:
            def checkpoint_state_provider() -> dict[str, Any]:
                return {
                    "pulse": "once",
                    "reason": "operator-requested checkpoint",
                    "captured_at": time.time(),
                }

        heart.install_default_jobs(
            passphrase=args.passphrase if args.checkpoint else None,
            checkpoint_state_provider=checkpoint_state_provider,
            coherence_provider=coherence_provider,
            checkpoint_interval_seconds=0.000001,
        )

        results = heart.run_due_jobs_once()
        if args.checkpoint and not any(r.job_name == "checkpoint_capsule" for r in results):
            results.extend(heart.run_due_jobs_once())

        anchor_verification = self.anchor_service.verify_anchor()
        frame = self._read_json_optional(self.coherence_frame_file) or {}
        heart_status = self._read_json_optional(self.heart_status_file) or {}

        for result in results:
            if result.job_name == "checkpoint_capsule" and result.success:
                checkpoint_capsule_id = result.output.get("capsule_id")

        payload = {
            "anchor_verification": {
                "valid": anchor_verification.valid,
                "reason": anchor_verification.reason,
            },
            "recommended_field_action": frame.get("recommended_action"),
            "drift_band": frame.get("drift_band"),
            "heart_status_written": self.heart_status_file.exists(),
            "coherence_frame_written": self.coherence_frame_file.exists(),
            "heart_running": heart_status.get("running"),
        }
        if checkpoint_capsule_id:
            payload["capsule_id"] = checkpoint_capsule_id
        return payload

    def run(self, argv: list[str] | None = None) -> int:
        parser = self._build_parser()
        args = parser.parse_args(argv)

        try:
            result = args.handler(args)
        except ShellError as exc:
            parser.error(str(exc))
            return 2
        except FileNotFoundError as exc:
            parser.error(str(exc))
            return 2

        if result is None:
            return 0

        if isinstance(result, CoachReply):
            if getattr(args, "json_output", False):
                print(json.dumps(result.to_record(), indent=2, sort_keys=True))
            else:
                print(render_reply(result))
            return 0

        if getattr(args, "json_output", False):
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(self._render_result(args.command, result, args))
        return 0

    def cmd_status(self, args: argparse.Namespace) -> dict[str, Any]:
        self._ensure_runtime_services()
        anchor = self._safe_anchor_status()
        heart = self._read_json_optional(self.heart_status_file)
        field = self._read_json_optional(self.coherence_frame_file)
        capsules = self.capsule_store.list_capsules()

        return {
            "shell_version": DEFAULT_SHELL_VERSION,
            "runtime_root": str(self.paths.runtime_root),
            "anchor": anchor,
            "heart": heart,
            "field": field,
            "capsules": {
                "count": len(capsules),
                "latest": capsules[-1] if capsules else None,
            },
            "generated_at": time.time(),
        }

    def cmd_field(self, args: argparse.Namespace) -> dict[str, Any]:
        frame = self._require_json(
            self.coherence_frame_file,
            "No coherence frame found. Run phik-heart with a coherence provider first.",
        )
        return frame

    def cmd_anchor_show(self, args: argparse.Namespace) -> dict[str, Any]:
        self._ensure_runtime_services()
        return self._safe_anchor_status(required=True)

    def cmd_capsule_list(self, args: argparse.Namespace) -> dict[str, Any]:
        self._ensure_runtime_services()
        rows = self.capsule_store.list_capsules()
        return {
            "count": len(rows),
            "items": rows,
        }

    def cmd_capsule_seal(self, args: argparse.Namespace) -> dict[str, Any]:
        self._ensure_runtime_services()
        state = self._load_state_payload(args)
        capsule = self.capsule_store.seal(
            passphrase=args.passphrase,
            state=state,
            capsule_type=args.capsule_type,
            semantic_phase=args.semantic_phase,
            tags=args.tags or [],
            summary=args.summary or "",
            parent_capsule_id=args.parent_capsule_id,
        )
        verification = self.capsule_store.verify_capsule(capsule.capsule_id)
        return {
            "capsule_id": capsule.capsule_id,
            "capsule_type": capsule.capsule_type,
            "semantic_phase": capsule.semantic_phase,
            "summary": capsule.summary,
            "tags": list(capsule.tags),
            "created_at": capsule.created_at,
            "verified": verification.valid,
            "verification_reason": verification.reason,
        }

    def cmd_capsule_restore(self, args: argparse.Namespace) -> dict[str, Any]:
        self._ensure_runtime_services()
        try:
            state = self.capsule_store.rehydrate(
                capsule_id=args.capsule_id,
                passphrase=args.passphrase,
            )
        except self._capsule_not_found_error as exc:
            raise ShellError(str(exc)) from exc

        verification = self.capsule_store.verify_capsule(args.capsule_id)
        return {
            "capsule_id": args.capsule_id,
            "verification": {
                "valid": verification.valid,
                "reason": verification.reason,
            },
            "state": state,
        }

    def cmd_think(self, args: argparse.Namespace) -> dict[str, Any]:
        self._ensure_runtime_services()
        return self._build_think_bundle(args.prompt or "")

    def cmd_route(self, args: argparse.Namespace) -> CoachReply:
        self._ensure_runtime_services()
        bundle = self._build_think_bundle(args.prompt or "")
        return self.router.route(bundle)

    def cmd_ask(self, args: argparse.Namespace) -> CoachReply:
        self._ensure_runtime_services()
        bundle = self._build_think_bundle(args.prompt or "")
        return self.router.route(bundle)

    def cmd_execute(self, args: argparse.Namespace) -> dict[str, Any]:
        self._ensure_runtime_services()
        adapter = select_runtime_adapter(getattr(args, "adapter", None))
        mode = "sessions"
        payload: dict[str, Any]
        if args.json_file and args.json_text:
            raise ShellError("Use either --json-file or --json-text for execute, not both")

        if args.json_file:
            execute_path = Path(args.json_file)
            if not execute_path.exists():
                raise ShellError(f"JSON file not found: {execute_path}")
            with execute_path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        elif args.json_text:
            payload = json.loads(args.json_text)
        else:
            payload = {}

        if not isinstance(payload, dict):
            raise ShellError("execute payload must decode to a JSON object")

        if not payload:
            bundle = self._build_think_bundle(args.prompt or "")
            payload = {
                "cohort_id": "C0",
                "field_session": {
                    "cohort_id": "C0",
                    "session_n": 1,
                    "member_ids": ["anchor"],
                    "member_CI": {"anchor": 0.0},
                    "member_dS": {"anchor": 1.0},
                    "notes": bundle.get("next_hint", ""),
                },
                "shell_bundle": bundle,
            }
            mode = "shell_bridge"

        result = self.runtime_bridge.execute(payload, adapter=adapter, mode=mode)
        return result.to_record()


    def _build_think_bundle(self, prompt: str) -> dict[str, Any]:
        """Build the local context bundle used by `think`, `route`, and `ask`."""
        anchor = self._safe_anchor_status()
        heart = self._read_json_optional(self.heart_status_file)
        field = self._read_json_optional(self.coherence_frame_file)
        capsules = self.capsule_store.list_capsules()
        latest_capsule = capsules[-1] if capsules else None

        return {
            "shell_version": DEFAULT_SHELL_VERSION,
            "prompt": prompt,
            "anchor": anchor,
            "heart": heart,
            "field": field,
            "latest_capsule": latest_capsule,
            "generated_at": time.time(),
            "next_hint": self._next_hint(
                anchor=anchor,
                heart=heart,
                field=field,
                latest_capsule=latest_capsule,
            ),
        }

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="phik", description="PhiKernel XiOS shell")
        parser.add_argument(
            "--runtime-root",
            default=str(DEFAULT_RUNTIME_ROOT),
            help="Base runtime root containing anchor/, capsule/, heart/, coherence/",
        )
        parser.add_argument("--anchor-root", default=None, help="Override anchor root")
        parser.add_argument("--capsule-root", default=None, help="Override capsule root")
        parser.add_argument("--heart-root", default=None, help="Override heart root")
        parser.add_argument("--coherence-root", default=None, help="Override coherence root")
        parser.add_argument(
            "--json",
            dest="json_output",
            action="store_true",
            help="Emit raw JSON instead of formatted terminal output",
        )

        subparsers = parser.add_subparsers(dest="command", required=True)

        init = subparsers.add_parser("init", help="Initialize a new PhiKernel runtime anchor")
        init.add_argument("--passphrase", required=True, help="Passphrase used to encrypt the anchor private key")
        init.add_argument("--sovereign-name", required=True, help="Sovereign anchor identity name")
        init.add_argument("--user-label", required=True, help="Operator label for this runtime")
        init.add_argument(
            "--resonant-label",
            default="StateAnchor / Sovereign Heartbeat",
            help="Optional resonant metadata label",
        )
        init.set_defaults(handler=self.cmd_init)

        pulse = subparsers.add_parser("pulse", help="Pulse commands")
        pulse_sub = pulse.add_subparsers(dest="pulse_command", required=True)
        pulse_once = pulse_sub.add_parser("once", help="Run one deterministic maintenance cycle")
        pulse_once.add_argument("--checkpoint", action="store_true", help="Also seal one checkpoint capsule")
        pulse_once.add_argument("--passphrase", default=None, help="Anchor passphrase (required with --checkpoint)")
        pulse_once.set_defaults(handler=self.cmd_pulse_once)

        status = subparsers.add_parser("status", help="Show aggregate PhiKernel status")
        status.set_defaults(handler=self.cmd_status)

        field = subparsers.add_parser("field", help="Show current coherence / field frame")
        field.set_defaults(handler=self.cmd_field)

        anchor = subparsers.add_parser("anchor", help="Anchor commands")
        anchor_sub = anchor.add_subparsers(dest="anchor_command", required=True)
        anchor_show = anchor_sub.add_parser("show", help="Show signed anchor status")
        anchor_show.set_defaults(handler=self.cmd_anchor_show)

        capsule = subparsers.add_parser("capsule", help="Capsule commands")
        capsule_sub = capsule.add_subparsers(dest="capsule_command", required=True)

        capsule_list = capsule_sub.add_parser("list", help="List continuity capsules")
        capsule_list.set_defaults(handler=self.cmd_capsule_list)

        capsule_seal = capsule_sub.add_parser("seal", help="Seal a new continuity capsule")
        capsule_seal.add_argument("--passphrase", required=True, help="Anchor passphrase")
        capsule_seal.add_argument("--json-file", default=None, help="Path to a JSON file containing the state payload")
        capsule_seal.add_argument("--json-text", default=None, help="Inline JSON object string for the state payload")
        capsule_seal.add_argument("--summary", default="", help="Human summary for the capsule")
        capsule_seal.add_argument("--tag", dest="tags", action="append", default=[], help="Attach a tag (repeatable)")
        capsule_seal.add_argument(
            "--capsule-type",
            default="working",
            choices=["canonical", "working", "checkpoint", "journal", "handoff"],
        )
        capsule_seal.add_argument("--semantic-phase", type=int, default=9, choices=[3, 6, 9])
        capsule_seal.add_argument("--parent-capsule-id", default=None)
        capsule_seal.set_defaults(handler=self.cmd_capsule_seal)

        capsule_restore = capsule_sub.add_parser("restore", help="Restore a continuity capsule")
        capsule_restore.add_argument("capsule_id", help="Capsule UUID")
        capsule_restore.add_argument("--passphrase", required=True, help="Anchor passphrase")
        capsule_restore.set_defaults(handler=self.cmd_capsule_restore)

        think = subparsers.add_parser("think", help="Build a local context bundle for future orchestration")
        think.add_argument("prompt", nargs="?", default="", help="Optional operator prompt")
        think.set_defaults(handler=self.cmd_think)

        execute = subparsers.add_parser("execute", help="Execute runtime analysis via selected adapter")
        execute.add_argument("--adapter", default=None, help="Runtime adapter (legacy or tiekat_v50)")
        execute.add_argument("--json-file", default=None, help="JSON payload file for runtime execution")
        execute.add_argument("--json-text", default=None, help="Inline JSON payload for runtime execution")
        execute.add_argument("prompt", nargs="?", default="", help="Optional prompt used by shell bridge mode")
        execute.set_defaults(handler=self.cmd_execute)

        route = subparsers.add_parser("route", help="Route a prompt through the local coach router")
        route.add_argument("prompt", nargs="?", default="", help="Prompt to route")
        route.set_defaults(handler=self.cmd_route)

        ask = subparsers.add_parser("ask", help="Friendly alias for route")
        ask.add_argument("prompt", nargs="?", default="", help="Prompt to route")
        ask.set_defaults(handler=self.cmd_ask)

        return parser

    def _render_result(self, command: str, result: dict[str, Any], args: argparse.Namespace) -> str:
        if command == "status":
            return self._render_status(result)
        if command == "init":
            return self._render_init(result)
        if command == "pulse" and args.pulse_command == "once":
            return self._render_pulse_once(result)
        if command == "field":
            return self._render_field(result)
        if command == "anchor":
            return self._render_anchor(result)
        if command == "capsule" and args.capsule_command == "list":
            return self._render_capsule_list(result)
        if command == "capsule" and args.capsule_command == "seal":
            return self._render_capsule_seal(result)
        if command == "capsule" and args.capsule_command == "restore":
            return self._render_capsule_restore(result)
        if command == "think":
            return self._render_think(result)
        return json.dumps(result, indent=2, sort_keys=True)

    def _render_status(self, result: dict[str, Any]) -> str:
        anchor = result.get("anchor") or {}
        heart = result.get("heart") or {}
        field = result.get("field") or {}
        capsules = result.get("capsules") or {}

        anchor_valid = anchor.get("verification", {}).get("valid") if anchor else None
        heart_running = heart.get("running") if heart else None
        action = field.get("recommended_action") if field else None

        lines = [
            ":: PHIKERNEL STATUS ::",
            f"Runtime Root: {result['runtime_root']}",
            f"Anchor: {'loaded' if anchor else 'missing'} | verified={anchor_valid}",
            f"Heart: {'present' if heart else 'missing'} | running={heart_running}",
            f"Field: {'present' if field else 'missing'} | action={action}",
            f"Capsules: {capsules.get('count', 0)}",
        ]
        latest = capsules.get("latest")
        if latest:
            lines.append(f"Latest Capsule: {latest['capsule_id']} ({latest['capsule_type']})")
        return "\n".join(lines)

    def _render_init(self, result: dict[str, Any]) -> str:
        verification = result.get("verification") or {}
        return "\n".join(
            [
                ":: PHIKERNEL INIT ::",
                f"Anchor ID: {result.get('anchor_id')}",
                f"Sovereign Name: {result.get('sovereign_name')}",
                f"User Label: {result.get('user_label')}",
                f"Target Attractor: {result.get('target_attractor')}",
                f"Frequency Anchor (Hz): {result.get('frequency_anchor_hz')}",
                f"Verified: {verification.get('valid')} ({verification.get('reason')})",
            ]
        )

    def _render_pulse_once(self, result: dict[str, Any]) -> str:
        verification = result.get("anchor_verification") or {}
        lines = [
            ":: PHIKERNEL PULSE ONCE ::",
            f"Anchor Verified: {verification.get('valid')} ({verification.get('reason')})",
            f"Field Action: {result.get('recommended_field_action')}",
            f"Drift Band: {result.get('drift_band')}",
            f"Heart Status Written: {result.get('heart_status_written')}",
            f"Coherence Frame Written: {result.get('coherence_frame_written')}",
        ]
        if result.get("capsule_id"):
            lines.append(f"Checkpoint Capsule: {result.get('capsule_id')}")
        return "\n".join(lines)

    def _render_field(self, frame: dict[str, Any]) -> str:
        lines = [
            ":: PHIKERNEL FIELD REPORT ::",
            f"Anchor ID: {frame.get('anchor_id', '')}",
            f"Semantic Phase: {frame.get('semantic_phase')}",
            f"C_current: {frame.get('C_current')}",
            f"C_star: {frame.get('C_star')}",
            f"Distance: {frame.get('distance_to_C_star')}",
            f"Phi Flow: {frame.get('phi_flow')}",
            f"Lambda Node: {frame.get('lambda_node')}",
            f"Sigma Feedback: {frame.get('sigma_feedback')}",
            f"Fragmentation: {frame.get('fragmentation_score')}",
            f"Recommended Action: {frame.get('recommended_action')}",
            f"Drift Band: {frame.get('drift_band')}",
        ]
        notes = frame.get("notes") or []
        if notes:
            lines.append(f"Notes: {' | '.join(notes)}")
        return "\n".join(lines)

    def _render_anchor(self, anchor: dict[str, Any]) -> str:
        verification = anchor.get("verification", {})
        return "\n".join(
            [
                ":: PHIKERNEL ANCHOR ::",
                f"Anchor ID: {anchor.get('anchor_id')}",
                f"Sovereign Name: {anchor.get('sovereign_name')}",
                f"User Label: {anchor.get('user_label')}",
                f"Frequency Anchor (Hz): {anchor.get('frequency_anchor_hz')}",
                f"Target Attractor: {anchor.get('target_attractor')}",
                f"Semantic Phase Model: {anchor.get('semantic_phase_model')}",
                f"Manifest Hash: {anchor.get('manifest_hash')}",
                f"Verified: {verification.get('valid')} ({verification.get('reason')})",
            ]
        )

    def _render_capsule_list(self, result: dict[str, Any]) -> str:
        lines = [":: PHIKERNEL CAPSULES ::", f"Count: {result['count']}"]
        for item in result["items"]:
            lines.append(
                f"- {item['capsule_id']} | {item['capsule_type']} | phase={item['semantic_phase']} | {item['summary']}"
            )
        return "\n".join(lines)

    def _render_capsule_seal(self, result: dict[str, Any]) -> str:
        return "\n".join(
            [
                ":: CAPSULE SEALED ::",
                f"Capsule ID: {result['capsule_id']}",
                f"Type: {result['capsule_type']}",
                f"Phase: {result['semantic_phase']}",
                f"Summary: {result['summary']}",
                f"Verified: {result['verified']} ({result['verification_reason']})",
            ]
        )

    def _render_capsule_restore(self, result: dict[str, Any]) -> str:
        return "\n".join(
            [
                ":: CAPSULE RESTORED ::",
                f"Capsule ID: {result['capsule_id']}",
                f"Verified: {result['verification']['valid']} ({result['verification']['reason']})",
                json.dumps(result['state'], indent=2, sort_keys=True),
            ]
        )

    def _render_think(self, result: dict[str, Any]) -> str:
        lines = [
            ":: PHIKERNEL THINK BUNDLE ::",
            f"Prompt: {result.get('prompt', '')}",
            f"Next Hint: {result.get('next_hint', '')}",
        ]
        field = result.get("field") or {}
        if field:
            lines.append(f"Field Action: {field.get('recommended_action')} ({field.get('drift_band')})")
        latest_capsule = result.get("latest_capsule")
        if latest_capsule:
            lines.append(f"Latest Capsule: {latest_capsule['capsule_id']} ({latest_capsule['capsule_type']})")
        return "\n".join(lines)

    def _safe_anchor_status(self, *, required: bool = False) -> dict[str, Any] | None:
        try:
            return self.anchor_service.public_status()
        except self._anchor_not_initialized_error:
            if required:
                raise ShellError("No anchor is initialized at the configured anchor root.")
            return None

    def _read_json_optional(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _require_json(self, path: Path, missing_message: str) -> dict[str, Any]:
        data = self._read_json_optional(path)
        if data is None:
            raise ShellError(missing_message)
        return data

    def _load_state_payload(self, args: argparse.Namespace) -> dict[str, Any]:
        if args.json_file and args.json_text:
            raise ShellError("Use either --json-file or --json-text, not both")
        if args.json_file:
            path = Path(args.json_file)
            if not path.exists():
                raise ShellError(f"JSON file not found: {path}")
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        elif args.json_text:
            payload = json.loads(args.json_text)
        else:
            raise ShellError("capsule seal requires --json-file or --json-text")

        if not isinstance(payload, dict):
            raise ShellError("Capsule payload must decode to a JSON object")
        return payload

    def _next_hint(
        self,
        *,
        anchor: dict[str, Any] | None,
        heart: dict[str, Any] | None,
        field: dict[str, Any] | None,
        latest_capsule: dict[str, Any] | None,
    ) -> str:
        if not anchor:
            return "Initialize the StateAnchor before continuing."
        if field and field.get("recommended_action") == "alert":
            return "Repair anchor trust before any further work."
        if field and field.get("recommended_action") == "restore":
            return "Restore the latest known-good capsule to reduce drift."
        if field and field.get("recommended_action") == "checkpoint":
            return "Seal a checkpoint capsule before proceeding."
        if not latest_capsule:
            return "Seal the first working capsule to establish continuity."
        if not heart:
            return "Start phik-heart to bring pulse monitoring online."
        return "Field is stable. Continue with orchestration or shell workflows."


def resolve_runtime_paths(argv: list[str] | None = None) -> RuntimePaths:
    """Resolve runtime paths before the full parser is instantiated."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    pre.add_argument("--anchor-root", default=None)
    pre.add_argument("--capsule-root", default=None)
    pre.add_argument("--heart-root", default=None)
    pre.add_argument("--coherence-root", default=None)
    args, _ = pre.parse_known_args(argv)

    runtime_root = Path(args.runtime_root)
    return RuntimePaths(
        runtime_root=runtime_root,
        anchor_root=Path(args.anchor_root) if args.anchor_root else runtime_root / "anchor",
        capsule_root=Path(args.capsule_root) if args.capsule_root else runtime_root / "capsule",
        heart_root=Path(args.heart_root) if args.heart_root else runtime_root / "heart",
        coherence_root=Path(args.coherence_root) if args.coherence_root else runtime_root / "coherence",
    )


def main(argv: list[str] | None = None) -> int:
    paths = resolve_runtime_paths(argv)
    shell = PhiKernelShell(paths)
    return shell.run(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
