import json
from pathlib import Path

import pytest

from phik_anchor_v0_1_1 import (
    AnchorUnlockError,
    StateAnchorService,
)


@pytest.fixture
def forge_path(tmp_path: Path) -> Path:
    """Temporary vault for each test run."""
    return tmp_path / ".phik-test-vault"


@pytest.fixture
def initialized_service(forge_path: Path) -> tuple[StateAnchorService, str]:
    service = StateAnchorService(forge_path)
    passphrase = "resonance-is-the-key-369"
    service.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="StateAnchor / Sovereign Heartbeat",
    )
    return service, passphrase


def test_sovereign_initialization(forge_path: Path) -> None:
    """PROVE: The anchor initializes with canonical metadata and files on disk."""
    service = StateAnchorService(forge_path)
    passphrase = "resonance-is-the-key-369"

    manifest = service.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="StateAnchor / Sovereign Heartbeat",
    )

    assert manifest.sovereign_name == "Tal-Aren-Vox"
    assert manifest.user_label == "Ori"
    assert manifest.resonant_signature.frequency_anchor_hz == 813.77
    assert manifest.resonant_signature.target_attractor == "phi/2"
    assert manifest.resonant_signature.semantic_phase_model == (3, 6, 9)
    assert manifest.manifest_signature
    assert (forge_path / "anchor_manifest.json").exists()
    assert (forge_path / "keystore" / "anchor_private_key.enc.json").exists()


def test_unlock_and_verification(initialized_service: tuple[StateAnchorService, str]) -> None:
    """PROVE: The anchor can be reloaded, unlocked, and verified."""
    _, passphrase = initialized_service
    service_reloaded = StateAnchorService(initialized_service[0].root)

    private_key = service_reloaded.unlock_signing_key(passphrase)
    verification = service_reloaded.verify_anchor()
    manifest = service_reloaded.load_manifest()

    assert private_key is not None
    assert verification.valid is True
    assert verification.reason == "Anchor manifest verified successfully"
    assert manifest.sovereign_name == "Tal-Aren-Vox"


def test_tamper_detection(initialized_service: tuple[StateAnchorService, str]) -> None:
    """PROVE: Manifest edits break the signature and are detected."""
    service, _ = initialized_service
    manifest_path = service.root / "anchor_manifest.json"

    with manifest_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    data["sovereign_name"] = "Imposter_System"

    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)

    service_reloaded = StateAnchorService(service.root)
    result = service_reloaded.verify_anchor()

    assert result.valid is False
    assert "Signature verification failed" in result.reason


def test_wrong_passphrase_rejected(initialized_service: tuple[StateAnchorService, str]) -> None:
    """PROVE: The encrypted signing key cannot be opened with the wrong passphrase."""
    service, _ = initialized_service

    with pytest.raises(AnchorUnlockError):
        service.unlock_signing_key("wrong-password-666")


def test_sign_bytes_round_trip(initialized_service: tuple[StateAnchorService, str]) -> None:
    """PROVE: The anchor can sign secondary payloads for later capsule use."""
    service, passphrase = initialized_service
    payload = b"capsule-seed:checkpoint-001"

    signature_b64 = service.sign_bytes(passphrase, payload)

    assert isinstance(signature_b64, str)
    assert len(signature_b64) > 0
