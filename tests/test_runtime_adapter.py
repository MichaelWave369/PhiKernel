import json
from pathlib import Path

from phikernel.heart import RuntimeBridge
from phikernel.router import select_runtime_adapter
from phikernel.shell import PhiKernelShell, RuntimePaths


def _sessions_payload() -> dict:
    sessions = []
    members = ["a", "b", "c", "d", "e"]
    for i in range(1, 9):
        sessions.append(
            {
                "cohort_id": "C9",
                "member_ids": members,
                "session_n": i,
                "member_CI": {m: 0.84 + (0.001 * i) for m in members},
                "member_dS": {m: 0.05 for m in members},
                "is_simulation": False,
                "data_origin": "RUNTIME_TEST",
            }
        )
    return {"cohort_id": "C9", "sessions": sessions}


def test_adapter_selection_defaults_and_env(monkeypatch) -> None:
    monkeypatch.delenv("PHIKERNEL_ADAPTER", raising=False)
    assert select_runtime_adapter(None) == "legacy"

    monkeypatch.setenv("PHIKERNEL_ADAPTER", "tiekat_v50")
    assert select_runtime_adapter(None) == "tiekat_v50"
    assert select_runtime_adapter("invalid") == "legacy"


def test_runtime_bridge_normalized_contract_tiekat_v50() -> None:
    bridge = RuntimeBridge()
    result = bridge.execute(_sessions_payload(), adapter="tiekat_v50", mode="sessions").to_record()

    assert result["engine"] == "phikernel"
    assert result["adapter"] == "tiekat_v50"
    assert result["substrate"] == "tiekat"
    assert result["substrate_version"] == "50.0.0"
    assert set(result).issuperset(
        {
            "engine",
            "engine_version",
            "substrate",
            "substrate_version",
            "adapter",
            "mode",
            "verdict",
            "evidence_level",
            "coherence_score",
            "stability_score",
            "readiness_score",
            "risk_score",
            "null_result",
            "recommendation",
            "debug",
        }
    )


def test_runtime_bridge_migrates_field_session_shape() -> None:
    bridge = RuntimeBridge()
    result = bridge.execute(
        {
            "cohort_id": "C3",
            "field_session": {
                "cohort_id": "C3",
                "session_n": 1,
                "member_ids": ["a", "b", "c", "d", "e"],
                "member_CI": {"a": 0.81, "b": 0.82, "c": 0.81, "d": 0.82, "e": 0.81},
                "member_dS": {"a": 0.09, "b": 0.09, "c": 0.08, "d": 0.09, "e": 0.08},
            },
        },
        adapter="tiekat_v50",
        mode="field_session",
    ).to_record()

    assert result["adapter"] == "tiekat_v50"
    assert result["debug"]["total_sessions"] == 1


def test_shell_execute_uses_selected_adapter(tmp_path: Path, capsys) -> None:
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
    _ = capsys.readouterr()

    payload_file = tmp_path / "sessions.json"
    payload_file.write_text(json.dumps(_sessions_payload()), encoding="utf-8")

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_root),
            "--json",
            "execute",
            "--adapter",
            "tiekat_v50",
            "--json-file",
            str(payload_file),
        ]
    )
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert exit_code == 0
    assert payload["adapter"] == "tiekat_v50"
    assert payload["engine"] == "phikernel"
