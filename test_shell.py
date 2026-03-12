import json
from pathlib import Path

import pytest

from phik_anchor_v0_1_1 import StateAnchorService
from phik_capsule_v0_1_1 import ContinuityCapsuleStore
from phik_coherence_v0_1_1 import CoherenceService
from phik_shell_v0_1_1 import PhiKernelShell, RuntimePaths, resolve_runtime_paths


@pytest.fixture
def runtime_paths(tmp_path: Path) -> RuntimePaths:
    runtime_root = tmp_path / ".phik-runtime"
    return RuntimePaths(
        runtime_root=runtime_root,
        anchor_root=runtime_root / "anchor",
        capsule_root=runtime_root / "capsule",
        heart_root=runtime_root / "heart",
        coherence_root=runtime_root / "coherence",
    )


@pytest.fixture
def initialized_shell(runtime_paths: RuntimePaths) -> tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore]:
    passphrase = "resonance-is-the-key-369"

    anchor_service = StateAnchorService(runtime_paths.anchor_root)
    anchor_service.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="StateAnchor / Sovereign Heartbeat",
    )

    capsule_store = ContinuityCapsuleStore(runtime_paths.capsule_root, anchor_service)
    shell = PhiKernelShell(runtime_paths)
    return shell, passphrase, anchor_service, capsule_store



def _write_heart_status(runtime_paths: RuntimePaths, *, running: bool = False) -> None:
    runtime_paths.heart_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "0.1.1",
        "running": running,
        "started_at_wall": 111.0,
        "stopped_at_wall": None if running else 222.0,
        "tick_interval_seconds": 0.25,
        "loop_iterations": 3,
        "registered_jobs": 2,
        "last_tick_wall": 333.0,
        "last_checkpoint_capsule_id": None,
        "last_coherence_frame": None,
        "recent_jobs": [],
    }
    with (runtime_paths.heart_root / "heart_status.json").open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)



def _seed_coherence_frame(runtime_paths: RuntimePaths) -> dict:
    service = CoherenceService(runtime_paths.coherence_root)
    frame = service.observe(
        {
            "anchor_id": "anchor-shell",
            "anchor_valid": True,
            "heartbeat_running": True,
            "capsule_store_configured": True,
            "capsule_count": 2,
            "active_threads": 1,
            "pending_events": 0,
            "unresolved_alerts": 0,
            "last_checkpoint_age_seconds": 60.0,
            "checkpoint_due": False,
            "notes": ["shell test"],
        }
    )
    return frame.to_record()



def test_resolve_runtime_paths_supports_runtime_root_and_overrides(tmp_path: Path) -> None:
    """PROVE: The shell resolves default organ paths and accepts explicit overrides."""
    base = tmp_path / "runtime-a"
    custom_anchor = tmp_path / "custom-anchor"

    paths = resolve_runtime_paths(
        [
            "--runtime-root",
            str(base),
            "--anchor-root",
            str(custom_anchor),
        ]
    )

    assert paths.runtime_root == base
    assert paths.anchor_root == custom_anchor
    assert paths.capsule_root == base / "capsule"
    assert paths.heart_root == base / "heart"
    assert paths.coherence_root == base / "coherence"



def test_status_aggregation_reports_missing_and_present_layers(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
) -> None:
    """PROVE: `phik status` honestly reports missing heart/field data and present anchor state."""
    shell, _, _, _ = initialized_shell

    result = shell.cmd_status(type("Args", (), {})())

    assert result["anchor"] is not None
    assert result["anchor"]["sovereign_name"] == "Tal-Aren-Vox"
    assert result["heart"] is None
    assert result["field"] is None
    assert result["capsules"]["count"] == 0

    _write_heart_status(runtime_paths, running=True)
    _seed_coherence_frame(runtime_paths)
    result_after = shell.cmd_status(type("Args", (), {})())

    assert result_after["heart"]["running"] is True
    assert result_after["field"]["recommended_action"] in {"observe", "checkpoint", "restore", "alert"}



def test_field_command_renders_human_and_json_output(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: `phik field` exposes coherence in readable and machine-readable forms."""
    shell, _, _, _ = initialized_shell
    frame = _seed_coherence_frame(runtime_paths)

    exit_code = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "field"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert ":: PHIKERNEL FIELD REPORT ::" in captured.out
    assert f"C_current: {frame['C_current']}" in captured.out
    assert f"Recommended Action: {frame['recommended_action']}" in captured.out

    exit_code_json = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "--json", "field"])
    captured_json = capsys.readouterr()
    payload = json.loads(captured_json.out)

    assert exit_code_json == 0
    assert payload["anchor_id"] == frame["anchor_id"]
    assert payload["frame_id"] == frame["frame_id"]



def test_anchor_show_json_surface(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: `phik anchor show` exposes the signed sovereign identity cleanly."""
    shell, _, _, _ = initialized_shell

    exit_code = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "--json", "anchor", "show"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["sovereign_name"] == "Tal-Aren-Vox"
    assert payload["user_label"] == "Ori"
    assert payload["frequency_anchor_hz"] == 813.77
    assert payload["target_attractor"] == "phi/2"
    assert payload["verification"]["valid"] is True



def test_capsule_seal_list_restore_flow(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: A user can seal a capsule, list it, and restore it through the shell."""
    shell, passphrase, _, _ = initialized_shell
    state = {
        "thread": "Save the World",
        "phase": 9,
        "notes": ["anchor", "capsule", "heart", "coherence"],
    }

    seal_exit = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "capsule",
            "seal",
            "--passphrase",
            passphrase,
            "--json-text",
            json.dumps(state),
            "--summary",
            "Shell capsule test",
            "--tag",
            "shell",
            "--tag",
            "checkpoint",
            "--capsule-type",
            "checkpoint",
            "--semantic-phase",
            "9",
        ]
    )
    seal_out = capsys.readouterr()
    seal_payload = json.loads(seal_out.out)

    assert seal_exit == 0
    assert seal_payload["verified"] is True
    assert seal_payload["capsule_type"] == "checkpoint"

    list_exit = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "--json", "capsule", "list"])
    list_out = capsys.readouterr()
    list_payload = json.loads(list_out.out)

    assert list_exit == 0
    assert list_payload["count"] == 1
    assert list_payload["items"][0]["capsule_id"] == seal_payload["capsule_id"]
    assert list_payload["items"][0]["summary"] == "Shell capsule test"

    restore_exit = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "capsule",
            "restore",
            seal_payload["capsule_id"],
            "--passphrase",
            passphrase,
        ]
    )
    restore_out = capsys.readouterr()
    restore_payload = json.loads(restore_out.out)

    assert restore_exit == 0
    assert restore_payload["verification"]["valid"] is True
    assert restore_payload["state"] == state



def test_think_bundle_contains_anchor_field_heart_and_latest_capsule(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: `phik think` emits a full context bundle for later agent orchestration."""
    shell, passphrase, _, capsule_store = initialized_shell
    _write_heart_status(runtime_paths, running=True)
    _seed_coherence_frame(runtime_paths)

    capsule = capsule_store.seal(
        passphrase=passphrase,
        state={"context": "latest capsule", "phase": 9},
        capsule_type="working",
        semantic_phase=9,
        tags=["latest"],
        summary="Latest working state",
    )

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "think",
            "prepare the porch",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["prompt"] == "prepare the porch"
    assert payload["anchor"]["sovereign_name"] == "Tal-Aren-Vox"
    assert payload["heart"]["running"] is True
    assert payload["field"]["anchor_id"] == "anchor-shell"
    assert payload["latest_capsule"]["capsule_id"] == capsule.capsule_id
    assert isinstance(payload["next_hint"], str)
    assert len(payload["next_hint"]) > 0



def test_field_command_errors_cleanly_when_frame_is_missing(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: Shell failures surface honestly through the CLI contract."""
    shell, _, _, _ = initialized_shell

    with pytest.raises(SystemExit) as excinfo:
        shell.run(["--runtime-root", str(runtime_paths.runtime_root), "field"])

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "No coherence frame found" in captured.err
