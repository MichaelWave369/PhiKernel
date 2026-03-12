import json
from pathlib import Path

import pytest

from phikernel.anchor import StateAnchorService
from phikernel.capsule import (
    CapsuleDecryptionError,
    ContinuityCapsuleStore,
)


@pytest.fixture
def forge_paths(tmp_path: Path) -> tuple[Path, Path]:
    """Separate roots for anchor and capsule storage."""
    anchor_root = tmp_path / ".phik-anchor-vault"
    capsule_root = tmp_path / ".phik-capsule-vault"
    return anchor_root, capsule_root


@pytest.fixture
def initialized_capsule_store(
    forge_paths: tuple[Path, Path],
) -> tuple[StateAnchorService, ContinuityCapsuleStore, str]:
    anchor_root, capsule_root = forge_paths
    passphrase = "resonance-is-the-key-369"

    anchor_service = StateAnchorService(anchor_root)
    anchor_service.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="StateAnchor / Sovereign Heartbeat",
    )

    store = ContinuityCapsuleStore(capsule_root, anchor_service)
    return anchor_service, store, passphrase


def test_seal_and_rehydrate_round_trip(
    initialized_capsule_store: tuple[StateAnchorService, ContinuityCapsuleStore, str],
) -> None:
    """PROVE: A rich state payload survives seal -> verify -> rehydrate intact."""
    _, store, passphrase = initialized_capsule_store
    state = {
        "domain": "Forbidden Herbals",
        "recipe": {
            "name": "Sovereign Calm Tea",
            "ingredients": [
                {"name": "lemon balm", "grams": 3.5},
                {"name": "chamomile", "grams": 2.0},
                {"name": "lavender", "grams": 0.8},
            ],
            "instructions": [
                "Heat water to 94C",
                "Steep for 8 minutes",
                "Strain and rest before serving",
            ],
        },
        "coach_notes": ["seed", "build", "reveal"],
        "metrics": {"semantic_phase": 9, "coherence": 0.81},
    }

    capsule = store.seal(
        passphrase=passphrase,
        state=state,
        capsule_type="checkpoint",
        semantic_phase=9,
        tags=["herbals", "continuity", "checkpoint"],
        summary="Forbidden Herbals recipe checkpoint",
    )

    verification = store.verify_capsule(capsule.capsule_id)
    restored = store.rehydrate(capsule_id=capsule.capsule_id, passphrase=passphrase)

    assert verification.valid is True
    assert verification.reason == "Capsule verified successfully"
    assert restored == state


def test_capsule_listing_metadata_integrity(
    initialized_capsule_store: tuple[StateAnchorService, ContinuityCapsuleStore, str],
) -> None:
    """PROVE: Capsule listings expose correct lightweight metadata for the shell."""
    _, store, passphrase = initialized_capsule_store

    capsule = store.seal(
        passphrase=passphrase,
        state={"thread_summary": "Checkpoint alpha", "status": "stable"},
        capsule_type="working",
        semantic_phase=6,
        tags=["alpha", "working"],
        summary="Active working memory",
    )

    rows = store.list_capsules()

    assert len(rows) == 1
    assert rows[0]["capsule_id"] == capsule.capsule_id
    assert rows[0]["capsule_type"] == "working"
    assert rows[0]["semantic_phase"] == 6
    assert rows[0]["summary"] == "Active working memory"
    assert rows[0]["tags"] == ["alpha", "working"]



def test_ciphertext_tamper_detection(
    initialized_capsule_store: tuple[StateAnchorService, ContinuityCapsuleStore, str],
) -> None:
    """PROVE: Editing ciphertext breaks the capsule signature and is detected."""
    _, store, passphrase = initialized_capsule_store
    capsule = store.seal(
        passphrase=passphrase,
        state={"field": "memory", "status": "sealed"},
        capsule_type="checkpoint",
        semantic_phase=9,
        summary="Tamper probe",
    )

    capsule_path = store.capsule_dir / f"{capsule.capsule_id}.json"
    with capsule_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    ciphertext = data["ciphertext_b64"]
    data["ciphertext_b64"] = ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]

    with capsule_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)

    result = store.verify_capsule(capsule.capsule_id)

    assert result.valid is False
    assert "signature verification failed" in result.reason.lower()



def test_wrong_passphrase_rejected_on_rehydrate(
    initialized_capsule_store: tuple[StateAnchorService, ContinuityCapsuleStore, str],
) -> None:
    """PROVE: Verification can pass while decryption still rejects the wrong passphrase."""
    _, store, passphrase = initialized_capsule_store
    capsule = store.seal(
        passphrase=passphrase,
        state={"private_notes": ["anchor", "capsule", "heart"]},
        capsule_type="journal",
        semantic_phase=9,
        summary="Private journal state",
    )

    verification = store.verify_capsule(capsule.capsule_id)
    assert verification.valid is True

    with pytest.raises(CapsuleDecryptionError):
        store.rehydrate(capsule_id=capsule.capsule_id, passphrase="wrong-password-666")



def test_anchor_mismatch_rejected(tmp_path: Path) -> None:
    """PROVE: A capsule sealed under one anchor is rejected by a different anchor."""
    passphrase = "resonance-is-the-key-369"

    anchor_root_a = tmp_path / "anchor-a"
    anchor_root_b = tmp_path / "anchor-b"
    capsule_root = tmp_path / "capsule-root"

    anchor_a = StateAnchorService(anchor_root_a)
    anchor_a.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="Anchor A",
    )
    store_a = ContinuityCapsuleStore(capsule_root, anchor_a)
    capsule = store_a.seal(
        passphrase=passphrase,
        state={"continuity": "bound-to-anchor-a"},
        capsule_type="handoff",
        semantic_phase=9,
        summary="Anchor-bound continuity",
    )

    anchor_b = StateAnchorService(anchor_root_b)
    anchor_b.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="Anchor B",
    )
    store_b = ContinuityCapsuleStore(capsule_root, anchor_b)

    result = store_b.verify_capsule(capsule.capsule_id)

    assert result.valid is False
    assert "anchor_id does not match current anchor" in result.reason.lower() or "manifest_hash does not match current anchor manifest" in result.reason.lower()
