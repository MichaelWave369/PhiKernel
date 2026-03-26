from pathlib import Path

import pytest

from phikernel.anchor import StateAnchorService
from phikernel.capsule import CapsuleVerificationError, ContinuityCapsuleStore


def test_contaminated_memory_write_is_denied(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PHIKERNEL_TRUST_ENABLED", "1")
    passphrase = "resonance-is-the-key-369"

    anchor = StateAnchorService(tmp_path / "anchor")
    anchor.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="StateAnchor / Sovereign Heartbeat",
    )
    store = ContinuityCapsuleStore(tmp_path / "capsule", anchor)

    with pytest.raises(CapsuleVerificationError):
        store.seal(
            passphrase=passphrase,
            state={"contamination_load": 0.91, "payload": "contaminated sample"},
            capsule_type="checkpoint",
            semantic_phase=9,
            summary="contaminated snapshot",
        )
