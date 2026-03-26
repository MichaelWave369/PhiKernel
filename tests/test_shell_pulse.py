import json
from pathlib import Path

import pytest

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
        control_root=runtime_root / "control",
    )


@pytest.fixture
def shell(runtime_paths: RuntimePaths) -> PhiKernelShell:
    return PhiKernelShell(runtime_paths)


@pytest.fixture
def passphrase() -> str:
    return "resonance-is-the-key-369"


def _init(shell: PhiKernelShell, runtime_paths: RuntimePaths, passphrase: str, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "init",
            "--passphrase",
            passphrase,
            "--sovereign-name",
            "Tal-Aren-Vox",
            "--user-label",
            "Ori",
        ]
    )
    assert exit_code == 0
    _ = capsys.readouterr()


def test_pulse_once_fails_cleanly_without_anchor(
    shell: PhiKernelShell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        shell.run(["--runtime-root", str(runtime_paths.runtime_root), "pulse", "once"])

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "No anchor is initialized" in captured.err


def test_pulse_once_succeeds_after_init(
    shell: PhiKernelShell,
    runtime_paths: RuntimePaths,
    passphrase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init(shell, runtime_paths, passphrase, capsys)

    exit_code = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "--json", "pulse", "once"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["anchor_verification"]["valid"] is True
    assert payload["recommended_field_action"] in {"observe", "checkpoint", "restore", "alert"}
    assert payload["drift_band"] in {"stable", "warning", "restore", "critical"}
    assert payload["heart_status_written"] is True
    assert payload["coherence_frame_written"] is True


def test_pulse_once_writes_heart_status_and_coherence_frame(
    shell: PhiKernelShell,
    runtime_paths: RuntimePaths,
    passphrase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init(shell, runtime_paths, passphrase, capsys)

    exit_code = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "pulse", "once"])
    _ = capsys.readouterr()

    assert exit_code == 0
    assert (runtime_paths.heart_root / "heart_status.json").exists()
    assert (runtime_paths.coherence_root / "coherence_frame.json").exists()


def test_pulse_once_checkpoint_creates_capsule(
    shell: PhiKernelShell,
    runtime_paths: RuntimePaths,
    passphrase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init(shell, runtime_paths, passphrase, capsys)

    pulse_exit = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "pulse",
            "once",
            "--checkpoint",
            "--passphrase",
            passphrase,
        ]
    )
    pulse_out = capsys.readouterr()
    payload = json.loads(pulse_out.out)

    assert pulse_exit == 0
    assert payload.get("capsule_id")

    list_exit = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "--json", "capsule", "list"])
    list_out = capsys.readouterr()
    list_payload = json.loads(list_out.out)

    assert list_exit == 0
    assert list_payload["count"] >= 1
    assert any(item["capsule_id"] == payload["capsule_id"] for item in list_payload["items"])


def test_pulse_once_checkpoint_without_passphrase_fails_cleanly(
    shell: PhiKernelShell,
    runtime_paths: RuntimePaths,
    passphrase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init(shell, runtime_paths, passphrase, capsys)

    with pytest.raises(SystemExit) as excinfo:
        shell.run(["--runtime-root", str(runtime_paths.runtime_root), "pulse", "once", "--checkpoint"])

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "requires --passphrase" in captured.err


def test_field_works_after_pulse_once(
    shell: PhiKernelShell,
    runtime_paths: RuntimePaths,
    passphrase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init(shell, runtime_paths, passphrase, capsys)

    pulse_exit = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "pulse", "once"])
    assert pulse_exit == 0
    _ = capsys.readouterr()

    field_exit = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "--json", "field"])
    field_out = capsys.readouterr()
    payload = json.loads(field_out.out)

    assert field_exit == 0
    assert payload["recommended_action"] in {"observe", "checkpoint", "restore", "alert"}
    assert payload["drift_band"] in {"stable", "warning", "restore", "critical"}


def test_ask_has_richer_context_after_pulse_once(
    shell: PhiKernelShell,
    runtime_paths: RuntimePaths,
    passphrase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _init(shell, runtime_paths, passphrase, capsys)

    pulse_exit = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "pulse",
            "once",
            "--checkpoint",
            "--passphrase",
            passphrase,
        ]
    )
    assert pulse_exit == 0
    _ = capsys.readouterr()

    ask_exit = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "ask",
            "How should I begin?",
        ]
    )
    ask_out = capsys.readouterr()
    payload = json.loads(ask_out.out)

    assert ask_exit == 0
    assert payload["field_action"] in {"observe", "checkpoint", "restore", "alert"}
    assert payload["trace"]["has_latest_capsule"] is True
