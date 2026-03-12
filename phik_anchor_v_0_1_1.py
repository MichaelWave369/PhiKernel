from __future__ import annotations

"""
phik_anchor_v0_1_1.py

Corrected reference implementation for PhiKernel's first substrate module:
`phik-anchor` (StateAnchor).

What this version fixes:
- Uses an immutable manifest dataclass with proper timestamp creation.
- Uses a single canonical serialization path for signing and verification.
- Stores the private key encrypted at rest.
- Persists Argon2id salt and KDF parameters alongside the ciphertext.
- Returns structured verification results instead of printing.
- Detects manifest tampering through signature verification.

Dependencies:
- cryptography
- argon2-cffi

Install:
    pip install cryptography argon2-cffi
"""

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
import base64
import hashlib
import json
import os
import tempfile
import time
import uuid

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


DEFAULT_VERSION = "0.1.1"
DEFAULT_FREQUENCY_ANCHOR_HZ = 813.77
DEFAULT_TARGET_ATTRACTOR = "phi/2"
DEFAULT_PHASE_MODEL = (3, 6, 9)
DEFAULT_FRAGMENTATION_THRESHOLD = 0.42


class StateAnchorError(Exception):
    """Base exception for all phik-anchor failures."""


class AnchorAlreadyExistsError(StateAnchorError):
    """Raised when initialization is attempted in a non-empty anchor directory."""


class AnchorNotInitializedError(StateAnchorError):
    """Raised when a requested anchor file does not exist yet."""


class AnchorUnlockError(StateAnchorError):
    """Raised when the encrypted private key cannot be unlocked."""


class AnchorVerificationError(StateAnchorError):
    """Raised when a manifest is malformed or unverifiable."""


@dataclass(frozen=True)
class ResonantSignature:
    """Semantic metadata describing the identity's symbolic / operational profile.

    This is metadata only. It is not cryptographic key material.
    """

    label: str
    semantic_phase_model: tuple[int, int, int] = DEFAULT_PHASE_MODEL
    target_attractor: str = DEFAULT_TARGET_ATTRACTOR
    frequency_anchor_hz: float = DEFAULT_FREQUENCY_ANCHOR_HZ


@dataclass(frozen=True)
class AnchorPolicy:
    """Operational policy bound into the signed anchor manifest."""

    auto_seal: bool = True
    auto_rehydrate: bool = True
    fragmentation_threshold: float = DEFAULT_FRAGMENTATION_THRESHOLD


@dataclass(frozen=True)
class AnchorManifest:
    """Signed root identity for the local PhiKernel runtime."""

    version: str = DEFAULT_VERSION
    anchor_id: str = ""
    device_id: str = ""
    sovereign_name: str = ""
    user_label: str = ""
    created_at: float = field(default_factory=time.time)
    public_key: str = ""
    resonant_signature: ResonantSignature = field(
        default_factory=lambda: ResonantSignature(label="Unnamed Anchor")
    )
    policy: AnchorPolicy = field(default_factory=AnchorPolicy)
    manifest_signature: str = ""

    def payload_dict(self) -> dict[str, Any]:
        """Return the canonical payload fields to be signed.

        The signature field is intentionally excluded so both signing and
        verification operate on the exact same byte sequence.
        """
        return {
            "version": self.version,
            "anchor_id": self.anchor_id,
            "device_id": self.device_id,
            "sovereign_name": self.sovereign_name,
            "user_label": self.user_label,
            "created_at": self.created_at,
            "public_key": self.public_key,
            "resonant_signature": asdict(self.resonant_signature),
            "policy": asdict(self.policy),
        }

    def canonical_bytes(self) -> bytes:
        """Stable serialization for signature and hashing."""
        return json.dumps(
            self.payload_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def manifest_hash(self) -> str:
        """Hash of the canonical payload for structured reporting."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_record(self) -> dict[str, Any]:
        """JSON-safe full record including the signature."""
        return {
            **self.payload_dict(),
            "manifest_signature": self.manifest_signature,
        }

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> "AnchorManifest":
        try:
            return cls(
                version=data["version"],
                anchor_id=data["anchor_id"],
                device_id=data["device_id"],
                sovereign_name=data["sovereign_name"],
                user_label=data["user_label"],
                created_at=float(data["created_at"]),
                public_key=data["public_key"],
                resonant_signature=ResonantSignature(**data["resonant_signature"]),
                policy=AnchorPolicy(**data["policy"]),
                manifest_signature=data.get("manifest_signature", ""),
            )
        except KeyError as exc:
            raise AnchorVerificationError(f"Manifest missing required field: {exc}") from exc
        except TypeError as exc:
            raise AnchorVerificationError(f"Manifest contains invalid structure: {exc}") from exc


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    reason: str
    anchor_id: str
    manifest_hash: str
    verified_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class KeystoreEnvelope:
    """Encrypted private-key container stored on disk."""

    algorithm: str
    kdf: str
    memory_cost_kib: int
    time_cost: int
    parallelism: int
    salt_b64: str
    nonce_b64: str
    ciphertext_b64: str

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> "KeystoreEnvelope":
        return cls(**data)


class AnchorKeyStore:
    """Manages encrypted private-key storage for the anchor."""

    def __init__(
        self,
        root: str | Path,
        *,
        memory_cost_kib: int = 65536,
        time_cost: int = 3,
        parallelism: int = 4,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_cost_kib = memory_cost_kib
        self.time_cost = time_cost
        self.parallelism = parallelism

        self.private_key_file = self.root / "anchor_private_key.enc.json"

    def create(self, passphrase: str, anchor_id: str) -> str:
        """Generate a new Ed25519 keypair and persist the private key encrypted.

        Returns the base64-encoded public key.
        """
        if self.private_key_file.exists():
            raise AnchorAlreadyExistsError(
                f"Encrypted private key already exists at {self.private_key_file}"
            )

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        private_key_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

        salt = os.urandom(16)
        nonce = os.urandom(12)
        aad = anchor_id.encode("utf-8")
        key = self._derive_aes_key(passphrase, salt)
        ciphertext = AESGCM(key).encrypt(nonce, private_key_bytes, aad)

        envelope = KeystoreEnvelope(
            algorithm="AES-256-GCM",
            kdf="Argon2id",
            memory_cost_kib=self.memory_cost_kib,
            time_cost=self.time_cost,
            parallelism=self.parallelism,
            salt_b64=_b64e(salt),
            nonce_b64=_b64e(nonce),
            ciphertext_b64=_b64e(ciphertext),
        )
        _atomic_write_json(self.private_key_file, envelope.to_record())
        _chmod_owner_only(self.private_key_file)

        public_key_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return _b64e(public_key_bytes)

    def unlock(self, passphrase: str, anchor_id: str) -> Ed25519PrivateKey:
        if not self.private_key_file.exists():
            raise AnchorNotInitializedError(
                f"Encrypted private key not found at {self.private_key_file}"
            )

        envelope = KeystoreEnvelope.from_record(_read_json(self.private_key_file))
        salt = _b64d(envelope.salt_b64)
        nonce = _b64d(envelope.nonce_b64)
        ciphertext = _b64d(envelope.ciphertext_b64)
        aad = anchor_id.encode("utf-8")

        key = hash_secret_raw(
            secret=passphrase.encode("utf-8"),
            salt=salt,
            time_cost=envelope.time_cost,
            memory_cost=envelope.memory_cost_kib,
            parallelism=envelope.parallelism,
            hash_len=32,
            type=Type.ID,
        )

        try:
            private_key_bytes = AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise AnchorUnlockError("Passphrase incorrect, keystore corrupted, or anchor ID mismatch") from exc

        return Ed25519PrivateKey.from_private_bytes(private_key_bytes)

    def _derive_aes_key(self, passphrase: str, salt: bytes) -> bytes:
        return hash_secret_raw(
            secret=passphrase.encode("utf-8"),
            salt=salt,
            time_cost=self.time_cost,
            memory_cost=self.memory_cost_kib,
            parallelism=self.parallelism,
            hash_len=32,
            type=Type.ID,
        )


class StateAnchorService:
    """High-level service that creates, signs, loads, and verifies the StateAnchor."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.root / "anchor_manifest.json"
        self.keystore = AnchorKeyStore(self.root / "keystore")

    def initialize(
        self,
        *,
        passphrase: str,
        sovereign_name: str,
        user_label: str,
        resonant_label: str,
        device_id: str | None = None,
        phase_model: tuple[int, int, int] = DEFAULT_PHASE_MODEL,
        frequency_anchor_hz: float = DEFAULT_FREQUENCY_ANCHOR_HZ,
        target_attractor: str = DEFAULT_TARGET_ATTRACTOR,
        policy: AnchorPolicy | None = None,
    ) -> AnchorManifest:
        """Create a brand-new anchor and persist its signed manifest."""
        if self.manifest_file.exists():
            raise AnchorAlreadyExistsError(
                f"Anchor manifest already exists at {self.manifest_file}"
            )

        anchor_id = str(uuid.uuid4())
        device_id = device_id or str(uuid.uuid4())
        policy = policy or AnchorPolicy()

        public_key_b64 = self.keystore.create(passphrase, anchor_id)
        manifest = AnchorManifest(
            anchor_id=anchor_id,
            device_id=device_id,
            sovereign_name=sovereign_name,
            user_label=user_label,
            public_key=public_key_b64,
            resonant_signature=ResonantSignature(
                label=resonant_label,
                semantic_phase_model=phase_model,
                target_attractor=target_attractor,
                frequency_anchor_hz=frequency_anchor_hz,
            ),
            policy=policy,
        )

        private_key = self.keystore.unlock(passphrase, anchor_id)
        signature = private_key.sign(manifest.canonical_bytes())
        signed_manifest = replace(manifest, manifest_signature=_b64e(signature))
        self._save_manifest(signed_manifest)
        _chmod_owner_only(self.manifest_file)
        return signed_manifest

    def load_manifest(self) -> AnchorManifest:
        if not self.manifest_file.exists():
            raise AnchorNotInitializedError(
                f"Anchor manifest not found at {self.manifest_file}"
            )
        return AnchorManifest.from_record(_read_json(self.manifest_file))

    def unlock_signing_key(self, passphrase: str) -> Ed25519PrivateKey:
        manifest = self.load_manifest()
        return self.keystore.unlock(passphrase, manifest.anchor_id)

    def verify_anchor(self) -> VerificationResult:
        manifest = self.load_manifest()
        if not manifest.manifest_signature:
            return VerificationResult(
                valid=False,
                reason="Manifest is missing its signature",
                anchor_id=manifest.anchor_id,
                manifest_hash=manifest.manifest_hash(),
            )

        public_key = Ed25519PublicKey.from_public_bytes(_b64d(manifest.public_key))
        try:
            public_key.verify(_b64d(manifest.manifest_signature), manifest.canonical_bytes())
        except (InvalidSignature, ValueError, TypeError):
            return VerificationResult(
                valid=False,
                reason="Signature verification failed",
                anchor_id=manifest.anchor_id,
                manifest_hash=manifest.manifest_hash(),
            )

        return VerificationResult(
            valid=True,
            reason="Anchor manifest verified successfully",
            anchor_id=manifest.anchor_id,
            manifest_hash=manifest.manifest_hash(),
        )

    def sign_bytes(self, passphrase: str, payload: bytes) -> str:
        """Sign arbitrary payloads with the anchor's signing key.

        The caller should use this for secondary artifacts such as capsules,
        manifests, or handoff packages.
        """
        private_key = self.unlock_signing_key(passphrase)
        return _b64e(private_key.sign(payload))

    def public_status(self) -> dict[str, Any]:
        """Minimal status surface safe for shell / UI display."""
        manifest = self.load_manifest()
        verification = self.verify_anchor()
        return {
            "anchor_id": manifest.anchor_id,
            "sovereign_name": manifest.sovereign_name,
            "user_label": manifest.user_label,
            "created_at": manifest.created_at,
            "frequency_anchor_hz": manifest.resonant_signature.frequency_anchor_hz,
            "target_attractor": manifest.resonant_signature.target_attractor,
            "semantic_phase_model": list(manifest.resonant_signature.semantic_phase_model),
            "manifest_hash": verification.manifest_hash,
            "verification": {
                "valid": verification.valid,
                "reason": verification.reason,
                "verified_at": verification.verified_at,
            },
        }

    def _save_manifest(self, manifest: AnchorManifest) -> None:
        _atomic_write_json(self.manifest_file, manifest.to_record())


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        json.dump(data, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _b64e(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64d(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


def _chmod_owner_only(path: Path) -> None:
    """Best-effort file hardening for POSIX systems."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Non-POSIX platforms may not support chmod in the same way.
        pass


if __name__ == "__main__":
    # Minimal smoke-test flow for manual verification.
    root = Path("./.phik-anchor-demo")
    service = StateAnchorService(root)

    if not (root / "anchor_manifest.json").exists():
        manifest = service.initialize(
            passphrase="change-this-demo-passphrase",
            sovereign_name="Tal-Aren-Vox",
            user_label="Ori",
            resonant_label="StateAnchor / Sovereign Heartbeat",
        )
        print("Initialized anchor:", manifest.anchor_id)
    else:
        print("Anchor already initialized.")

    result = service.verify_anchor()
    print({
        "valid": result.valid,
        "reason": result.reason,
        "anchor_id": result.anchor_id,
        "manifest_hash": result.manifest_hash,
    })
