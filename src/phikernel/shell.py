from __future__ import annotations

"""PhiKernel v0.2 human-facing CLI / terminal bridge.

The shell retains the original substrate commands and deterministic legacy
CoachRouter while adding explicit access to the constitutional runtime in
persisted constitutional state through:

    phik constitutional status
    phik constitutional route <prompt>
    phik constitutional action ...

The legacy `phik route` and `phik ask` commands remain unchanged and
authoritative. Constitutional commands consume validated persisted authority;
they do not manufacture ADVISE or BOUNDED_CONTROL from CLI flags. The action
surface can consume only an exact persisted BOUNDED_CONTROL lease and an
allowlisted normalized runtime-adapter target.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import argparse
import json
import os
import sys
import time

from phikernel.anc_bridge import guard_output_commit, guard_service_request
from phikernel.constitutional_action import (
    ConstitutionalActionError,
    ConstitutionalActionJournal,
    execute_constitutional_action,
    parse_resource_spends,
)
from phikernel.control_state import RuntimeControlState, apply_operator_action, load_runtime_control_state
from phikernel.constitutional_shell import run_constitutional_shell
from phikernel.constitutional_store import (
    ConstitutionalPersistenceError,
    ConstitutionalStateStore,
    PersistedConstitutionalState,
)
from phikernel.router import CoachReply, CoachRouter, render_reply, select_runtime_adapter
from phikernel.trust_runtime import (
    build_operator_trust_state,
    map_enforcement_to_guard_outcome,
)


DEFAULT_SHELL_VERSION = "0.2.0"
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
    control_root: Path


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
        self.constitutional_store = ConstitutionalStateStore(paths.runtime_root)

    def _ensure_runtime_services(self) -> None:
        if self.anchor_service is not None and self.capsule_store is not None:
            return

        from phikernel.anchor import AnchorAlreadyExistsError, AnchorNotInitializedError, StateAnchorService
        from phikernel.capsule import CapsuleNotFoundError, ContinuityCapsuleStore

        self.anchor_service = StateAnchorService(self.paths.anchor_root)
        self.capsule_store = ContinuityCapsuleStore(
            self.paths.capsule_root,
            self.anchor_service,
            control_root=self.paths.runtime_root,
        )
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
        self.paths.control_root.mkdir(parents=True, exist_ok=True)
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
        self.paths.control_root.mkdir(parents=True, exist_ok=True)
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
            "runtime_control": load_runtime_control_state(self.paths.runtime_root).to_record(),
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

    def cmd_constitutional_status(self, args: argparse.Namespace) -> dict[str, Any]:
        """Show validated persisted constitutional state without changing it."""
        state, persisted, snapshot_hash, history_count = self._load_constitutional_state()

        control = None
        if state.control_grant is not None and state.control_session is not None:
            control = {
                "contract_id": state.control_grant.contract_id,
                "grant_id": state.control_grant.grant_id,
                "warrant_id": state.control_grant.warrant.warrant_id,
                "expires_at": state.control_grant.expires_at,
                "session_id": state.control_session.session_id,
                "actions_used": state.control_session.actions_used,
                "clock_ticks_used": state.control_session.clock_ticks_used,
                "pending_action_id": state.control_session.pending_action_id,
                "stopped": state.control_session.stopped,
            }

        try:
            action_journal_count = len(
                ConstitutionalActionJournal(
                    self.paths.runtime_root
                ).history()
            )
        except ConstitutionalActionError as exc:
            raise ShellError(
                f"Constitutional action journal failed validation: {exc}"
            ) from exc

        return {
            "constitutional_version": "0.2.0",
            "persisted_state_present": persisted,
            "snapshot_hash": snapshot_hash,
            "history_count": history_count,
            "action_journal_count": action_journal_count,
            "mode": state.promotion_state.mode,
            "revision": state.promotion_state.revision,
            "last_receipt_id": state.promotion_state.last_receipt_id,
            "authorized_by_seal_id": state.promotion_state.authorized_by_seal_id,
            "advise_receipt_id": (
                None
                if state.advise_receipt is None
                else state.advise_receipt.receipt_id
            ),
            "collapse_receipt_id": (
                None
                if state.collapse_receipt is None
                else state.collapse_receipt.receipt_id
            ),
            "control": control,
        }

    def cmd_constitutional_route(self, args: argparse.Namespace) -> dict[str, Any]:
        """Route using validated persisted constitutional state."""
        self._ensure_runtime_services()
        bundle = self._build_think_bundle(args.prompt or "")
        control_state = load_runtime_control_state(self.paths.runtime_root)
        state, persisted, _, _ = self._load_constitutional_state()

        result = run_constitutional_shell(
            bundle,
            constitutional_state=state,
            persisted_state_present=persisted,
            runtime_control_state=control_state,
            router=self.router,
        )

        record = result.to_record()
        record["automatic_collapse_persisted"] = False

        if (
            state.promotion_state.mode == "BOUNDED_CONTROL"
            and result.runtime.mode_after == "SHADOW"
            and result.runtime.collapse_receipt is not None
        ):
            collapsed = PersistedConstitutionalState(
                promotion_state=result.runtime.promotion_state_after,
                collapse_receipt=result.runtime.collapse_receipt,
            )
            snapshot = self.constitutional_store.save(
                collapsed,
                written_at=result.runtime.evaluated_at,
                now=result.runtime.evaluated_at,
            )
            record["automatic_collapse_persisted"] = True
            record["persisted_snapshot_hash_after"] = snapshot.snapshot_hash

        return record

    def cmd_constitutional_action(self, args: argparse.Namespace) -> dict[str, Any]:
        """Consume one exact persisted bounded-control action lease."""
        self._ensure_runtime_services()
        state, persisted, _, _ = self._load_constitutional_state()
        if not persisted:
            raise ShellError(
                "Constitutional action requires persisted BOUNDED_CONTROL state"
            )

        if args.json_file and args.json_text:
            raise ShellError(
                "Use either --json-file or --json-text for constitutional action, not both"
            )
        if args.json_file:
            path = Path(args.json_file)
            if not path.exists():
                raise ShellError(f"JSON file not found: {path}")
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        elif args.json_text:
            payload = json.loads(args.json_text)
        else:
            raise ShellError(
                "constitutional action requires --json-file or --json-text"
            )
        if not isinstance(payload, dict):
            raise ShellError(
                "constitutional action payload must decode to a JSON object"
            )

        try:
            spends = parse_resource_spends(tuple(args.resources or ()))
            target = f"runtime/adapter/{args.adapter}"
            prompt = payload.get("prompt", "")
            bundle = self._build_think_bundle(
                prompt if isinstance(prompt, str) else ""
            )
            result = execute_constitutional_action(
                think_bundle=bundle,
                state=state,
                state_store=self.constitutional_store,
                runtime_control_state=load_runtime_control_state(
                    self.paths.runtime_root
                ),
                target=target,
                payload=payload,
                resource_spends=spends,
                clock_ticks=args.clock_ticks,
                evidence_refs=tuple(args.evidence or ()),
                rollback_ref=args.rollback_ref,
                runtime_bridge=self.runtime_bridge,
                legacy_router=self.router,
                action_journal=ConstitutionalActionJournal(
                    self.paths.runtime_root
                ),
            )
        except ConstitutionalActionError as exc:
            raise ShellError(
                f"Constitutional action failed: {exc}"
            ) from exc

        record = result.to_record()
        record["action_journal_count"] = len(
            ConstitutionalActionJournal(
                self.paths.runtime_root
            ).history()
        )
        return record

    def _load_constitutional_state(
        self,
    ) -> tuple[PersistedConstitutionalState, bool, str | None, int]:
        """Load constitutional state or genesis SHADOW; fail closed on bad persistence."""
        try:
            if not self.constitutional_store.exists():
                return (
                    PersistedConstitutionalState.genesis(),
                    False,
                    None,
                    0,
                )
            snapshot = self.constitutional_store.load_snapshot()
            history_count = len(self.constitutional_store.history())
            return snapshot.state, True, snapshot.snapshot_hash, history_count
        except ConstitutionalPersistenceError as exc:
            raise ShellError(
                f"Constitutional state failed validation: {exc}"
            ) from exc

    def cmd_execute(self, args: argparse.Namespace) -> dict[str, Any]:
        self._ensure_runtime_services()
        adapter = select_runtime_adapter(getattr(args, "adapter", None))
        trust_enabled = os.getenv("PHIKERNEL_TRUST_ENABLED", "0") == "1"
        control_state = load_runtime_control_state(self.paths.runtime_root)
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

        blocked_by_control = self._control_gate_for_service(control_state)
        if blocked_by_control:
            return blocked_by_control

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

        if trust_enabled:
            pre_enforcement = guard_service_request(payload)
            pre_outcome = map_enforcement_to_guard_outcome(pre_enforcement)
            if not pre_outcome.allowed:
                return {
                    "trust_gate": pre_outcome.to_record(),
                    "operator_trust_state": build_operator_trust_state(
                        enforcement=pre_enforcement,
                        runtime_state=payload,
                        control_state=control_state,
                    ),
                    "execution_blocked": True,
                    "blocked_stage": "service_pre",
                }

        result = self.runtime_bridge.execute(payload, adapter=adapter, mode=mode)
        result_record = result.to_record()

        if not trust_enabled:
            if control_state.review_required or control_state.recovery_state not in {None, "none"}:
                result_record["runtime_control_state"] = control_state.to_record()
            return result_record

        post_payload = {**payload, **result_record}
        post_enforcement = guard_output_commit(post_payload)
        post_outcome = map_enforcement_to_guard_outcome(post_enforcement)
        operator_trust_state = build_operator_trust_state(
            enforcement=post_enforcement,
            runtime_state=post_payload,
            control_state=control_state,
        )
        if post_outcome.deny_output_commit or not post_outcome.allowed:
            return {
                "trust_gate": post_outcome.to_record(),
                "operator_trust_state": operator_trust_state,
                "output_committed": False,
                "execution_result": result_record,
                "blocked_stage": "output_commit",
            }

        result_record["trust_gate"] = post_outcome.to_record()
        result_record["operator_trust_state"] = operator_trust_state
        if control_state.review_required or control_state.recovery_state not in {None, "none"}:
            result_record["runtime_control_state"] = control_state.to_record()
        return result_record

    def cmd_control(self, args: argparse.Namespace) -> dict[str, Any]:
        self.paths.control_root.mkdir(parents=True, exist_ok=True)
        return apply_operator_action(
            self.paths.runtime_root,
            action=args.action,
            operator_note=args.note,
        )


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
            "runtime_control_state": load_runtime_control_state(self.paths.runtime_root).to_record(),
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
        parser.add_argument("--control-root", default=None, help="Override runtime control root")
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

        constitutional = subparsers.add_parser(
            "constitutional",
            help="Constitutional runtime commands",
        )
        constitutional_sub = constitutional.add_subparsers(
            dest="constitutional_command",
            required=True,
        )
        constitutional_status = constitutional_sub.add_parser(
            "status",
            help="Show validated persisted constitutional mode and lineage summary",
        )
        constitutional_status.set_defaults(handler=self.cmd_constitutional_status)

        constitutional_route = constitutional_sub.add_parser(
            "route",
            help="Route using the validated persisted constitutional mode",
        )
        constitutional_route.add_argument(
            "prompt",
            nargs="?",
            default="",
            help="Prompt to route through the constitutional runtime",
        )
        constitutional_route.set_defaults(handler=self.cmd_constitutional_route)

        constitutional_action = constitutional_sub.add_parser(
            "action",
            help="Execute one exact persisted bounded-control runtime-adapter action",
        )
        constitutional_action.add_argument(
            "--adapter",
            required=True,
            choices=["legacy", "tiekat_v50"],
            help="Exact bounded runtime-adapter target",
        )
        action_payload = constitutional_action.add_mutually_exclusive_group(
            required=True
        )
        action_payload.add_argument(
            "--json-file",
            default=None,
            help="JSON object payload for the bounded runtime adapter",
        )
        action_payload.add_argument(
            "--json-text",
            default=None,
            help="Inline JSON object payload for the bounded runtime adapter",
        )
        constitutional_action.add_argument(
            "--resource",
            dest="resources",
            action="append",
            default=[],
            metavar="KIND=AMOUNT",
            help="Requested bounded resource spend (repeatable)",
        )
        constitutional_action.add_argument(
            "--clock-ticks",
            type=float,
            required=True,
            help="Requested bounded computational clock ticks",
        )
        constitutional_action.add_argument(
            "--evidence",
            action="append",
            default=[],
            help="Evidence reference supplied to the control contract (repeatable)",
        )
        constitutional_action.add_argument(
            "--rollback-ref",
            required=True,
            help="Declared rollback reference for this exact action",
        )
        constitutional_action.set_defaults(
            handler=self.cmd_constitutional_action
        )

        control = subparsers.add_parser("control", help="Apply operator runtime-control actions")
        control.add_argument(
            "action",
            choices=[
                "approve",
                "review",
                "quarantine",
                "seal",
                "clear_review",
                "release_quarantine",
                "recover_from_seal",
                "begin_recovery",
                "refresh",
            ],
        )
        control.add_argument("--note", default=None, help="Optional operator note for control action")
        control.set_defaults(handler=self.cmd_control)

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
        if command == "constitutional" and args.constitutional_command == "status":
            return self._render_constitutional_status(result)
        if command == "constitutional" and args.constitutional_command == "route":
            return self._render_constitutional_route(result)
        if command == "constitutional" and args.constitutional_command == "action":
            return self._render_constitutional_action(result)
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
            (
                f"Control: review_required={result['runtime_control'].get('review_required')} "
                f"quarantined={result['runtime_control'].get('quarantined')} "
                f"sealed={result['runtime_control'].get('sealed')}"
            ),
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

    def _render_constitutional_status(self, result: dict[str, Any]) -> str:
        control = result.get("control") or {}
        lines = [
            ":: PHIKERNEL CONSTITUTIONAL STATUS ::",
            f"Persisted: {result.get('persisted_state_present')}",
            f"Mode: {result.get('mode')}",
            f"Revision: {result.get('revision')}",
            f"Last Receipt: {result.get('last_receipt_id')}",
            f"Human Seal: {result.get('authorized_by_seal_id')}",
            f"History Entries: {result.get('history_count')}",
            f"Action Journal Entries: {result.get('action_journal_count')}",
        ]
        if control:
            lines.extend(
                [
                    f"Control Grant: {control.get('grant_id')}",
                    f"Control Session: {control.get('session_id')}",
                    f"Control Expires: {control.get('expires_at')}",
                    f"Actions Used: {control.get('actions_used')}",
                    f"Clock Ticks Used: {control.get('clock_ticks_used')}",
                    f"Pending Action: {control.get('pending_action_id')}",
                ]
            )
        return "\n".join(lines)

    def _render_constitutional_action(self, result: dict[str, Any]) -> str:
        runtime = result.get("runtime") or {}
        executor = result.get("executor_result") or {}
        outcome = result.get("outcome_receipt") or {}
        action = result.get("action") or {}
        lines = [
            ":: PHIKERNEL CONSTITUTIONAL ACTION ::",
            f"Transaction: {result.get('transaction_id')}",
            f"Action: {action.get('operation')}:{action.get('target')}",
            f"Runtime Disposition: {runtime.get('disposition')}",
            f"Executed: {result.get('executed')}",
            f"Success: {result.get('success')}",
            f"Resulting Mode: {result.get('resulting_mode')}",
            f"Result Ref: {executor.get('result_ref')}",
            f"Outcome: {outcome.get('reason')}",
            f"Action Journal Entries: {result.get('action_journal_count')}",
        ]
        return "\n".join(lines)

    def _render_constitutional_route(self, result: dict[str, Any]) -> str:
        comparison = result.get("shadow_comparison") or {}
        legacy = result.get("legacy_reply") or {}
        lines = [
            ":: PHIKERNEL CONSTITUTIONAL ROUTE ::",
            f"Mode: {result.get('shell_mode')}",
            f"Persisted State: {result.get('persisted_state_present')}",
            f"Disposition: {result.get('disposition')}",
            f"Legacy Coach: {legacy.get('coach')}",
            f"Legacy Route: {result.get('legacy_route_key')}",
            f"vNext Route: {(result.get('vnext_receipt') or {}).get('selected_route_key')}",
            f"Advisory Route: {result.get('advisory_route_key')}",
            f"Comparison: {comparison.get('comparison_state')}",
            f"Execution Licensed: {result.get('execution_licensed')}",
            f"Automatic Collapse Persisted: {result.get('automatic_collapse_persisted')}",
            f"Reason: {result.get('reason')}",
        ]
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
        control = load_runtime_control_state(self.paths.runtime_root)
        if control.sealed:
            return "Runtime is sealed. Follow recovery workflow before execution or writes."
        if control.quarantined:
            return "Runtime is quarantined. Request operator recovery before continuing."
        if control.recovery_state == "recovery_in_progress":
            return "Recovery is in progress. Complete explicit recovery actions before normal operation."
        if control.review_required:
            return "Runtime requires operator review. Proceed with caution."
        if not latest_capsule:
            return "Seal the first working capsule to establish continuity."
        if not heart:
            return "Start phik-heart to bring pulse monitoring online."
        return "Field is stable. Continue with orchestration or shell workflows."

    def _control_gate_for_service(self, control_state: RuntimeControlState) -> dict[str, Any] | None:
        if control_state.sealed:
            return {
                "execution_blocked": True,
                "blocked_stage": "service_pre",
                "operator_message": "Runtime sealed by operator control state.",
                "runtime_control_state": control_state.to_record(),
                "recovery_state": control_state.recovery_state,
                "recovery_message": control_state.recovery_message,
                "next_step": control_state.next_step or "recover_from_seal",
            }
        if control_state.quarantined:
            return {
                "execution_blocked": True,
                "blocked_stage": "service_pre",
                "operator_message": "Runtime quarantined by operator control state.",
                "runtime_control_state": control_state.to_record(),
                "recovery_state": control_state.recovery_state,
                "recovery_message": control_state.recovery_message,
                "next_step": control_state.next_step or "operator_quarantine_review",
            }
        return None


def resolve_runtime_paths(argv: list[str] | None = None) -> RuntimePaths:
    """Resolve runtime paths before the full parser is instantiated."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    pre.add_argument("--anchor-root", default=None)
    pre.add_argument("--capsule-root", default=None)
    pre.add_argument("--heart-root", default=None)
    pre.add_argument("--coherence-root", default=None)
    pre.add_argument("--control-root", default=None)
    args, _ = pre.parse_known_args(argv)

    runtime_root = Path(args.runtime_root)
    return RuntimePaths(
        runtime_root=runtime_root,
        anchor_root=Path(args.anchor_root) if args.anchor_root else runtime_root / "anchor",
        capsule_root=Path(args.capsule_root) if args.capsule_root else runtime_root / "capsule",
        heart_root=Path(args.heart_root) if args.heart_root else runtime_root / "heart",
        coherence_root=Path(args.coherence_root) if args.coherence_root else runtime_root / "coherence",
        control_root=Path(args.control_root) if args.control_root else runtime_root / "control",
    )


def main(argv: list[str] | None = None) -> int:
    paths = resolve_runtime_paths(argv)
    shell = PhiKernelShell(paths)
    return shell.run(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
