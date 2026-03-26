from pathlib import Path

from phikernel.shell import PhiKernelShell, RuntimePaths


def test_output_commit_blocked_for_hostile_leak(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PHIKERNEL_TRUST_ENABLED", "1")
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

    result = shell.cmd_execute(
        type("Args", (), {"adapter": "legacy", "json_file": None, "json_text": '{"message":"attempt leak to external sink"}', "prompt": ""})
    )

    assert result["output_committed"] is False
    assert result["blocked_stage"] == "output_commit"
    assert result["trust_gate"]["deny_output_commit"] is True
