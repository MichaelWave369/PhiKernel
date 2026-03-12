import json
from pathlib import Path

import pytest

from phikernel.anchor import StateAnchorService
from phikernel.capsule import ContinuityCapsuleStore
from phikernel.shell import PhiKernelShell, RuntimePaths


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



def _write_heart_status(runtime_paths: RuntimePaths, *, running: bool) -> None:
    runtime_paths.heart_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "0.1.1",
        "running": running,
        "started_at_wall": 111.0 if running else None,
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



def _write_field_frame(
    runtime_paths: RuntimePaths,
    *,
    recommended_action: str = "observe",
    drift_band: str = "stable",
    anchor_id: str = "anchor-test",
) -> None:
    runtime_paths.coherence_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "0.1.1",
        "frame_id": "frame-test",
        "observed_at": 123.456,
        "semantic_phase": 6 if recommended_action == "observe" else 9,
        "anchor_id": anchor_id,
        "anchor_valid": True,
        "C_current": 0.801,
        "C_star": 0.809016,
        "distance_to_C_star": 0.008016,
        "phi_flow": 0.74,
        "lambda_node": 0.91,
        "sigma_feedback": 0.18,
        "fragmentation_score": 0.12,
        "recommended_action": recommended_action,
        "drift_band": drift_band,
        "notes": ["shell-route test"],
    }
    with (runtime_paths.coherence_root / "coherence_frame.json").open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)



def _seed_capsule(capsule_store: ContinuityCapsuleStore, passphrase: str, *, summary: str = "Latest working state") -> str:
    capsule = capsule_store.seal(
        passphrase=passphrase,
        state={"context": summary, "phase": 9},
        capsule_type="working",
        semantic_phase=9,
        tags=["latest"],
        summary=summary,
    )
    return capsule.capsule_id



def test_ask_routes_to_flow_when_field_is_stable(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: `phik ask` natively builds the bundle and routes movement prompts to Flow when safe."""
    shell, passphrase, _, capsule_store = initialized_shell
    _write_heart_status(runtime_paths, running=True)
    _write_field_frame(runtime_paths, recommended_action="observe", drift_band="stable")
    _seed_capsule(capsule_store, passphrase)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "ask",
            "I need momentum to create and start this draft",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["coach"] == "Flow"
    assert payload["safe_to_proceed"] is True
    assert payload["field_action"] == "observe"
    assert payload["trace"]["heart_running"] is True
    assert payload["trace"]["has_latest_capsule"] is True



def test_route_routes_to_sage_when_field_is_stable(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: `phik route` natively routes reflection prompts to Sage when safe."""
    shell, passphrase, _, capsule_store = initialized_shell
    _write_heart_status(runtime_paths, running=True)
    _write_field_frame(runtime_paths, recommended_action="observe", drift_band="stable")
    _seed_capsule(capsule_store, passphrase, summary="Pattern journal")

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "route",
            "Help me understand the pattern and reflect on its meaning",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["coach"] == "Sage"
    assert payload["safe_to_proceed"] is True
    assert payload["field_band"] == "stable"
    assert "pattern" in payload["route_reason"].lower() or "reflection" in payload["route_reason"].lower()



def test_alert_field_forces_titan_even_for_flow_prompt(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: Native shell routing still obeys Titan override under critical field conditions."""
    shell, passphrase, _, capsule_store = initialized_shell
    _write_heart_status(runtime_paths, running=True)
    _write_field_frame(runtime_paths, recommended_action="alert", drift_band="critical")
    _seed_capsule(capsule_store, passphrase)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "ask",
            "please help me create momentum right now",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["coach"] == "Titan"
    assert payload["safe_to_proceed"] is False
    assert payload["field_action"] == "alert"
    assert payload["field_band"] == "critical"



def test_missing_heart_defaults_to_titan(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: Without a live pulse file, shell-native routing falls back to Titan."""
    shell, passphrase, _, capsule_store = initialized_shell
    _write_field_frame(runtime_paths, recommended_action="observe", drift_band="stable")
    _seed_capsule(capsule_store, passphrase)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "route",
            "I want momentum to start building",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["coach"] == "Titan"
    assert payload["safe_to_proceed"] is True
    assert "pulse is offline" in payload["route_reason"].lower()



def test_text_output_contract_for_ask(
    initialized_shell: tuple[PhiKernelShell, str, StateAnchorService, ContinuityCapsuleStore],
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PROVE: Human-readable ask output remains coach-readable and inspiring."""
    shell, passphrase, _, capsule_store = initialized_shell
    _write_heart_status(runtime_paths, running=True)
    _write_field_frame(runtime_paths, recommended_action="observe", drift_band="stable")
    _seed_capsule(capsule_store, passphrase, summary="Evening working state")

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "ask",
            "How should I begin?",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert ":: PHIK ROUTER ::" in captured.out
    assert "Coach:" in captured.out
    assert "Field Action:" in captured.out
    assert "Here." in captured.out
