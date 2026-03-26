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
def passphrase() -> str:
    return "resonance-is-the-key-369"


def test_init_successful_first_run(runtime_paths: RuntimePaths, passphrase: str, capsys: pytest.CaptureFixture[str]) -> None:
    shell = PhiKernelShell(runtime_paths)

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
    captured = capsys.readouterr()

    assert exit_code == 0
    assert ":: PHIKERNEL INIT ::" in captured.out
    assert "Sovereign Name: Tal-Aren-Vox" in captured.out
    assert "User Label: Ori" in captured.out
    assert "Target Attractor: phi/2" in captured.out
    assert "Frequency Anchor (Hz): 813.77" in captured.out
    assert "Verified: True" in captured.out


def test_init_fails_cleanly_when_anchor_exists(
    runtime_paths: RuntimePaths,
    passphrase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = PhiKernelShell(runtime_paths)

    first = shell.run(
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
    assert first == 0
    _ = capsys.readouterr()

    with pytest.raises(SystemExit) as excinfo:
        shell.run(
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

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "Anchor already exists" in captured.err


def test_init_json_output_shape(runtime_paths: RuntimePaths, passphrase: str, capsys: pytest.CaptureFixture[str]) -> None:
    shell = PhiKernelShell(runtime_paths)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "init",
            "--passphrase",
            passphrase,
            "--sovereign-name",
            "Tal-Aren-Vox",
            "--user-label",
            "Ori",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["anchor_id"]
    assert payload["sovereign_name"] == "Tal-Aren-Vox"
    assert payload["user_label"] == "Ori"
    assert payload["target_attractor"] == "phi/2"
    assert payload["frequency_anchor_hz"] == 813.77
    assert payload["verification"]["valid"] is True


def test_init_runtime_root_override_works(tmp_path: Path, passphrase: str, capsys: pytest.CaptureFixture[str]) -> None:
    runtime_root = tmp_path / "runtime-override"
    paths = RuntimePaths(
        runtime_root=runtime_root,
        anchor_root=runtime_root / "anchor",
        capsule_root=runtime_root / "capsule",
        heart_root=runtime_root / "heart",
        coherence_root=runtime_root / "coherence",
        control_root=runtime_root / "control",
    )
    shell = PhiKernelShell(paths)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_root),
            "init",
            "--passphrase",
            passphrase,
            "--sovereign-name",
            "Tal-Aren-Vox",
            "--user-label",
            "Ori",
        ]
    )
    _ = capsys.readouterr()

    assert exit_code == 0
    assert (runtime_root / "anchor" / "anchor_manifest.json").exists()


def test_anchor_show_works_after_init(runtime_paths: RuntimePaths, passphrase: str, capsys: pytest.CaptureFixture[str]) -> None:
    shell = PhiKernelShell(runtime_paths)

    init_exit = shell.run(
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
    assert init_exit == 0
    _ = capsys.readouterr()

    show_exit = shell.run(["--runtime-root", str(runtime_paths.runtime_root), "--json", "anchor", "show"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert show_exit == 0
    assert payload["sovereign_name"] == "Tal-Aren-Vox"
    assert payload["user_label"] == "Ori"
    assert payload["verification"]["valid"] is True
