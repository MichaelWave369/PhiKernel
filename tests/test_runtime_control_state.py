from pathlib import Path

import pytest

from phikernel.anc_bridge import RuntimeEnforcementResult
from phikernel.capsule import CapsuleVerificationError
from phikernel.control_state import (
    load_runtime_control_state,
    list_recent_runtime_control_actions,
)
from phikernel.shell import PhiKernelShell, RuntimePaths
from phikernel.trust_runtime import build_operator_trust_state


def _shell(tmp_path: Path) -> PhiKernelShell:
    runtime_root = tmp_path / "runtime"
    shell = PhiKernelShell(
        RuntimePaths(
            runtime_root=runtime_root,
            anchor_root=runtime_root / "anchor",
            capsule_root=runtime_root / "capsule",
            heart_root=runtime_root / "heart",
            coherence_root=runtime_root / "coherence",
            control_root=runtime_root / "control",
        )
    )
    shell.run(
        [
            "--runtime-root",
            str(runtime_root),
            "init",
            "--passphrase",
            "resonance-is-the-key-369",
            "--sovereign-name",
            "Tal-Aren-Vox",
            "--user-label",
            "Ori",
        ]
    )
    return shell


def test_review_action_sets_persistent_state_and_execute_surfaces_review(tmp_path: Path) -> None:
    shell = _shell(tmp_path)

    review = shell.cmd_control(type("Args", (), {"action": "review", "note": "manual check required"})())

    assert review["accepted"] is True
    persisted = load_runtime_control_state(shell.paths.runtime_root)
    assert persisted.review_required is True

    execute_result = shell.cmd_execute(
        type("Args", (), {"adapter": "legacy", "json_file": None, "json_text": '{"prompt":"normal"}', "prompt": ""})
    )
    assert execute_result["runtime_control_state"]["review_required"] is True


def test_quarantine_blocks_execute_and_capsule_writes(tmp_path: Path) -> None:
    shell = _shell(tmp_path)

    quarantine = shell.cmd_control(type("Args", (), {"action": "quarantine", "note": "suspected poisoning"})())
    assert quarantine["runtime_control_state"]["quarantined"] is True

    execute_result = shell.cmd_execute(
        type("Args", (), {"adapter": "legacy", "json_file": None, "json_text": '{"prompt":"normal"}', "prompt": ""})
    )
    assert execute_result["execution_blocked"] is True
    assert execute_result["blocked_stage"] == "service_pre"

    with pytest.raises(CapsuleVerificationError):
        shell.capsule_store.seal(
            passphrase="resonance-is-the-key-369",
            state={"phase": 9, "summary": "blocked"},
            capsule_type="working",
            semantic_phase=9,
            summary="should fail",
        )


def test_seal_blocks_runtime_and_sets_recovery_next_step(tmp_path: Path) -> None:
    shell = _shell(tmp_path)

    sealed = shell.cmd_control(type("Args", (), {"action": "seal", "note": "incident containment"})())

    assert sealed["runtime_control_state"]["sealed"] is True
    assert sealed["next_step"] == "recover_from_seal"

    execute_result = shell.cmd_execute(
        type("Args", (), {"adapter": "legacy", "json_file": None, "json_text": '{"prompt":"normal"}', "prompt": ""})
    )
    assert execute_result["execution_blocked"] is True
    assert execute_result["next_step"] == "recover_from_seal"


def test_approve_clears_review_but_not_quarantine_or_seal(tmp_path: Path) -> None:
    shell = _shell(tmp_path)

    shell.cmd_control(type("Args", (), {"action": "review", "note": "check"})())
    approved = shell.cmd_control(type("Args", (), {"action": "approve", "note": "looks good"})())
    assert approved["accepted"] is True
    assert approved["runtime_control_state"]["review_required"] is False

    shell.cmd_control(type("Args", (), {"action": "quarantine", "note": "contain"})())
    blocked_approve = shell.cmd_control(type("Args", (), {"action": "approve", "note": "should not clear"})())
    assert blocked_approve["accepted"] is False
    assert blocked_approve["runtime_control_state"]["quarantined"] is True


def test_refresh_and_action_history_persistence(tmp_path: Path) -> None:
    shell = _shell(tmp_path)

    shell.cmd_control(type("Args", (), {"action": "review", "note": "n1"})())
    refresh = shell.cmd_control(type("Args", (), {"action": "refresh", "note": "n2"})())

    assert refresh["runtime_control_state"]["action_history_count"] == 2
    records = list_recent_runtime_control_actions(shell.paths.runtime_root, limit=5)
    assert len(records) == 2
    assert records[-1].operator_note == "n2"
    assert records[-1].timestamp


def test_operator_trust_export_includes_runtime_control_fields(tmp_path: Path) -> None:
    shell = _shell(tmp_path)
    shell.cmd_control(type("Args", (), {"action": "review", "note": "requires oversight"})())
    state = load_runtime_control_state(shell.paths.runtime_root)

    export = build_operator_trust_state(
        enforcement=RuntimeEnforcementResult(
            action="ALLOW",
            allowed=True,
            stage="service_pre",
            reason="ok",
        ),
        runtime_state={"coherence_score": 0.77},
        control_state=state,
    )

    assert export["review_required"] is True
    assert export["quarantined"] is False
    assert export["sealed"] is False
    assert export["last_operator_note"] == "requires oversight"
    assert export["action_history_count"] >= 1
