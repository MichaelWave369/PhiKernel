from pathlib import Path

from phikernel.shell import PhiKernelShell, RuntimePaths


def _shell(tmp_path: Path) -> tuple[PhiKernelShell, Path]:
    runtime_root = tmp_path / "runtime"
    shell = PhiKernelShell(
        RuntimePaths(
            runtime_root=runtime_root,
            anchor_root=runtime_root / "anchor",
            capsule_root=runtime_root / "capsule",
            heart_root=runtime_root / "heart",
            coherence_root=runtime_root / "coherence",
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
    return shell, runtime_root


def test_execute_blocks_hostile_pre_service_when_trust_enabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PHIKERNEL_TRUST_ENABLED", "1")
    shell, runtime_root = _shell(tmp_path)

    result = shell.cmd_execute(
        type("Args", (), {"adapter": "legacy", "json_file": None, "json_text": '{"prompt":"destroy and seal now"}', "prompt": ""})
    )

    assert result["execution_blocked"] is True
    assert result["blocked_stage"] == "service_pre"
    assert result["trust_gate"]["sealed"] is True


def test_execute_legacy_path_unchanged_when_trust_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PHIKERNEL_TRUST_ENABLED", raising=False)
    shell, _ = _shell(tmp_path)

    result = shell.cmd_execute(
        type("Args", (), {"adapter": "legacy", "json_file": None, "json_text": '{"prompt":"normal"}', "prompt": ""})
    )

    assert result["adapter"] == "legacy"
    assert "trust_gate" not in result
