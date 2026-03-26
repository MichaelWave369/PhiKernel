from __future__ import annotations

"""
phik_capsule_v0_1_1.py

Corrected reference implementation for PhiKernel's second substrate module:
`phik-capsule` (ContinuityCapsule store).

This module is designed to sit directly on top of `phik_anchor_v0_1_1.py`.

What this version provides:
- Immutable capsule dataclass with a canonical signing payload.
- Per-capsule symmetric encryption key derived from passphrase + salt.
- Anchor-backed signature generation and verification.
- Atomic JSON writes to a local capsule store.
- Restore / rehydrate flow with signature verification before decryption.
- Structured verification results for shell / policy / heart consumers.

Dependencies:
- cryptography
- argon2-cffi
- phik_anchor_v0_1_1.py

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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from phikernel.anchor import (
    AnchorManifest,
    AnchorVerificationError,
    StateAnchorService,
)
from phikernel.anc_bridge import guard_memory_write
from phikernel.control_state import load_runtime_control_state
from phikernel.trust_runtime import map_enforcement_to_guard_outcome


DEFAULT_CAPSULE_VERSION = "0.1.1"
DEFAULT_CAPSULE_TYPE = "working"
DEFAULT_SEMANTIC_PHASE = 9
DEFAULT_CAPSULE_MEMORY_COST_KIB = 65536
DEFAULT_CAPSULE_TIME_COST = 3
DEFAULT_CAPSULE_PARALLELISM = 4
CAPSULE_PURPOSE = b"phik-capsule-v0.1.1"
VALID_CAPSULE_TYPES = {"canonical", "working", "checkpoint", "journal", "handoff"}
VALID_PHASES = {3, 6, 9}


class ContinuityCapsuleError(Exception):
    """Base exception for all phik-capsule failures."""


class CapsuleNotFoundError(ContinuityCapsuleError):
    """Raised when a requested capsule file does not exist."""


class CapsuleVerificationError(ContinuityCapsuleError):
    """Raised when a capsule is malformed or fails trust checks."""


class CapsuleDecryptionError(ContinuityCapsuleError):
    """Raised when capsule ciphertext cannot be opened."""


@dataclass(frozen=True)
class ContinuityCapsule:
    """Sealed continuity object backed by anchor trust.

    Notes:
    - `ciphertext` is the encrypted JSON state.
    - `manifest_hash` binds the capsule to the exact anchor manifest payload
      present at seal time.
    - `signature` signs the capsule payload, not the decrypted state.
    """

    version: str = DEFAULT_CAPSULE_VERSION
    capsule_id: str = ""
    anchor_id: str = ""
    parent_capsule_id: str | None = None
    capsule_type: str = DEFAULT_CAPSULE_TYPE
    created_at: float = field(default_factory=time.time)
    semantic_phase: int = DEFAULT_SEMANTIC_PHASE
    tags: tuple[str, ...] = ()
    summary: str = ""
    manifest_hash: str = ""
    kdf: str = "Argon2id"
    algorithm: str = "AES-256-GCM"
    memory_cost_kib: int = DEFAULT_CAPSULE_MEMORY_COST_KIB
    time_cost: int = DEFAULT_CAPSULE_TIME_COST
    parallelism: int = DEFAULT_CAPSULE_PARALLELISM
    encryption_salt_b64: str = ""
    data_nonce_b64: str = ""
    ciphertext_b64: str = ""
    signature: str = ""

    def __post_init__(self) -> None:
        if self.capsule_type not in VALID_CAPSULE_TYPES:
            raise CapsuleVerificationError(
                f"Invalid capsule_type '{self.capsule_type}'. Expected one of {sorted(VALID_CAPSULE_TYPES)}"
            )
        if self.semantic_phase not in VALID_PHASES:
            raise CapsuleVerificationError(
                f"Invalid semantic_phase '{self.semantic_phase}'. Expected one of {sorted(VALID_PHASES)}"
            )

    def payload_dict(self) -> dict[str, Any]:
        """Return canonical payload fields excluding the signature."""
        return {
            "version": self.version,
            "capsule_id": self.capsule_id,
            "anchor_id": self.anchor_id,
            "parent_capsule_id": self.parent_capsule_id,
            "capsule_type": self.capsule_type,
            "created_at": self.created_at,
            "semantic_phase": self.semantic_phase,
            "tags": list(self.tags),
            "summary": self.summary,
            "manifest_hash": self.manifest_hash,
            "kdf": self.kdf,
            "algorithm": self.algorithm,
            "memory_cost_kib": self.memory_cost_kib,
            "time_cost": self.time_cost,
            "parallelism": self.parallelism,
            "encryption_salt_b64": self.encryption_salt_b64,
            "data_nonce_b64": self.data_nonce_b64,
            "ciphertext_b64": self.ciphertext_b64,
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.payload_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def capsule_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_record(self) -> dict[str, Any]:
        return {
            **self.payload_dict(),
            "signature": self.signature,
        }

    @classmethod
    def from_record(cls, data: dict[str, Any]) -> "ContinuityCapsule":
        try:
            return cls(
                version=data["version"],
                capsule_id=data["capsule_id"],
                anchor_id=data["anchor_id"],
                parent_capsule_id=data.get("parent_capsule_id"),
                capsule_type=data["capsule_type"],
                created_at=float(data["created_at"]),
                semantic_phase=int(data["semantic_phase"]),
                tags=tuple(data.get("tags", [])),
                summary=data.get("summary", ""),
                manifest_hash=data["manifest_hash"],
                kdf=data["kdf"],
                algorithm=data["algorithm"],
                memory_cost_kib=int(data["memory_cost_kib"]),
                time_cost=int(data["time_cost"]),
                parallelism=int(data["parallelism"]),
                encryption_salt_b64=data["encryption_salt_b64"],
                data_nonce_b64=data["data_nonce_b64"],
                ciphertext_b64=data["ciphertext_b64"],
                signature=data.get("signature", ""),
            )
        except KeyError as exc:
            raise CapsuleVerificationError(f"Capsule missing required field: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise CapsuleVerificationError(f"Capsule contains invalid structure: {exc}") from exc


@dataclass(frozen=True)
class CapsuleVerificationResult:
    valid: bool
    reason: str
    capsule_id: str
    anchor_id: str
    capsule_hash: str
    verified_at: float = field(default_factory=time.time)


class ContinuityCapsuleStore:
    """Anchor-backed capsule service.

    The store derives a dedicated symmetric key per capsule using:
        Argon2id(passphrase, random_salt, purpose + anchor_id + capsule_id)

    The decrypted state is arbitrary JSON-serializable data.
    """

    def __init__(
        self,
        root: str | Path,
        anchor_service: StateAnchorService,
        *,
        memory_cost_kib: int = DEFAULT_CAPSULE_MEMORY_COST_KIB,
        time_cost: int = DEFAULT_CAPSULE_TIME_COST,
        parallelism: int = DEFAULT_CAPSULE_PARALLELISM,
        control_root: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.anchor_service = anchor_service
        self.memory_cost_kib = memory_cost_kib
        self.time_cost = time_cost
        self.parallelism = parallelism
        self.control_root = Path(control_root) if control_root is not None else None
        self.capsule_dir = self.root / "capsules"
        self.capsule_dir.mkdir(parents=True, exist_ok=True)

    def seal(
        self,
        *,
        passphrase: str,
        state: dict[str, Any],
        capsule_type: str = DEFAULT_CAPSULE_TYPE,
        semantic_phase: int = DEFAULT_SEMANTIC_PHASE,
        tags: list[str] | tuple[str, ...] | None = None,
        summary: str = "",
        parent_capsule_id: str | None = None,
    ) -> ContinuityCapsule:
        """Create, sign, encrypt, and persist a continuity capsule."""
        if self.control_root is not None:
            control_state = load_runtime_control_state(self.control_root)
            if control_state.sealed:
                raise CapsuleVerificationError(
                    "Memory write blocked: runtime is sealed and requires recovery before writes."
                )
            if control_state.quarantined:
                raise CapsuleVerificationError(
                    "Memory write blocked: runtime is quarantined pending operator review."
                )
            if control_state.recovery_state == "recovery_in_progress":
                raise CapsuleVerificationError(
                    "Memory write blocked: runtime recovery is in progress; complete recovery flow first."
                )

        if os.getenv("PHIKERNEL_TRUST_ENABLED", "0") == "1":
            enforcement = guard_memory_write(
                {
                    "capsule_type": capsule_type,
                    "summary": summary,
                    "tags": list(tags or ()),
                    "state_keys": sorted(state.keys()) if isinstance(state, dict) else [],
                    "contamination_load": state.get("contamination_load", 0.0) if isinstance(state, dict) else 0.0,
                }
            )
            guard_outcome = map_enforcement_to_guard_outcome(enforcement)
            if guard_outcome.deny_memory_write or not guard_outcome.allowed:
                raise CapsuleVerificationError(
                    f"Memory write blocked by trust guard: {guard_outcome.operator_message}"
                )

        manifest = self.anchor_service.load_manifest()
        anchor_verification = self.anchor_service.verify_anchor()
        if not anchor_verification.valid:
            raise CapsuleVerificationError(
                f"Cannot seal capsule because anchor is invalid: {anchor_verification.reason}"
            )

        capsule_id = str(uuid.uuid4())
        state_bytes = self._canonicalize_state_bytes(state)
        salt = os.urandom(16)
        nonce = os.urandom(12)
        key = self._derive_capsule_key(
            passphrase=passphrase,
            salt=salt,
            anchor_id=manifest.anchor_id,
            capsule_id=capsule_id,
        )
        aad = self._aad(manifest.anchor_id, capsule_id)
        ciphertext = AESGCM(key).encrypt(nonce, state_bytes, aad)

        capsule = ContinuityCapsule(
            capsule_id=capsule_id,
            anchor_id=manifest.anchor_id,
            parent_capsule_id=parent_capsule_id,
            capsule_type=capsule_type,
            semantic_phase=semantic_phase,
            tags=tuple(tags or ()),
            summary=summary,
            manifest_hash=manifest.manifest_hash(),
            memory_cost_kib=self.memory_cost_kib,
            time_cost=self.time_cost,
            parallelism=self.parallelism,
            encryption_salt_b64=_b64e(salt),
            data_nonce_b64=_b64e(nonce),
            ciphertext_b64=_b64e(ciphertext),
        )
        signature = self.anchor_service.sign_bytes(passphrase, capsule.canonical_bytes())
        signed_capsule = replace(capsule, signature=signature)
        self._write_capsule(signed_capsule)
        return signed_capsule

    def list_capsules(self) -> list[dict[str, Any]]:
        """Return lightweight metadata for all capsule files."""
        rows: list[dict[str, Any]] = []
        for path in sorted(self.capsule_dir.glob("*.json")):
            capsule = self.load_capsule(path.stem)
            rows.append(
                {
                    "capsule_id": capsule.capsule_id,
                    "capsule_type": capsule.capsule_type,
                    "created_at": capsule.created_at,
                    "semantic_phase": capsule.semantic_phase,
                    "summary": capsule.summary,
                    "tags": list(capsule.tags),
                    "anchor_id": capsule.anchor_id,
                }
            )
        return rows

    def load_capsule(self, capsule_id: str) -> ContinuityCapsule:
        path = self._capsule_path(capsule_id)
        if not path.exists():
            raise CapsuleNotFoundError(f"Capsule not found at {path}")
        return ContinuityCapsule.from_record(_read_json(path))

    def verify_capsule(self, capsule_id: str) -> CapsuleVerificationResult:
        capsule = self.load_capsule(capsule_id)
        manifest = self.anchor_service.load_manifest()

        if not capsule.signature:
            return CapsuleVerificationResult(
                valid=False,
                reason="Capsule is missing its signature",
                capsule_id=capsule.capsule_id,
                anchor_id=capsule.anchor_id,
                capsule_hash=capsule.capsule_hash(),
            )
        if capsule.anchor_id != manifest.anchor_id:
            return CapsuleVerificationResult(
                valid=False,
                reason="Capsule anchor_id does not match current anchor",
                capsule_id=capsule.capsule_id,
                anchor_id=capsule.anchor_id,
                capsule_hash=capsule.capsule_hash(),
            )
        if capsule.manifest_hash != manifest.manifest_hash():
            return CapsuleVerificationResult(
                valid=False,
                reason="Capsule manifest_hash does not match current anchor manifest",
                capsule_id=capsule.capsule_id,
                anchor_id=capsule.anchor_id,
                capsule_hash=capsule.capsule_hash(),
            )

        try:
            public_key = Ed25519PublicKey.from_public_bytes(_b64d(manifest.public_key))
            public_key.verify(_b64d(capsule.signature), capsule.canonical_bytes())
        except (InvalidSignature, ValueError, TypeError, AnchorVerificationError):
            return CapsuleVerificationResult(
                valid=False,
                reason="Capsule signature verification failed",
                capsule_id=capsule.capsule_id,
                anchor_id=capsule.anchor_id,
                capsule_hash=capsule.capsule_hash(),
            )

        return CapsuleVerificationResult(
            valid=True,
            reason="Capsule verified successfully",
            capsule_id=capsule.capsule_id,
            anchor_id=capsule.anchor_id,
            capsule_hash=capsule.capsule_hash(),
        )

    def rehydrate(self, *, capsule_id: str, passphrase: str) -> dict[str, Any]:
        """Verify, decrypt, and restore a capsule's state payload."""
        verification = self.verify_capsule(capsule_id)
        if not verification.valid:
            raise CapsuleVerificationError(
                f"Cannot rehydrate capsule because verification failed: {verification.reason}"
            )

        capsule = self.load_capsule(capsule_id)
        salt = _b64d(capsule.encryption_salt_b64)
        nonce = _b64d(capsule.data_nonce_b64)
        ciphertext = _b64d(capsule.ciphertext_b64)
        key = self._derive_capsule_key(
            passphrase=passphrase,
            salt=salt,
            anchor_id=capsule.anchor_id,
            capsule_id=capsule.capsule_id,
            memory_cost_kib=capsule.memory_cost_kib,
            time_cost=capsule.time_cost,
            parallelism=capsule.parallelism,
        )
        aad = self._aad(capsule.anchor_id, capsule.capsule_id)

        try:
            state_bytes = AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise CapsuleDecryptionError(
                "Capsule decryption failed: incorrect passphrase, corrupted ciphertext, or AAD mismatch"
            ) from exc

        try:
            state = json.loads(state_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CapsuleDecryptionError("Capsule plaintext is not valid JSON") from exc

        if not isinstance(state, dict):
            raise CapsuleDecryptionError("Capsule payload must decode to a JSON object")
        return state

    def public_capsule_status(self, capsule_id: str) -> dict[str, Any]:
        capsule = self.load_capsule(capsule_id)
        verification = self.verify_capsule(capsule_id)
        return {
            "capsule_id": capsule.capsule_id,
            "capsule_type": capsule.capsule_type,
            "created_at": capsule.created_at,
            "semantic_phase": capsule.semantic_phase,
            "summary": capsule.summary,
            "tags": list(capsule.tags),
            "anchor_id": capsule.anchor_id,
            "manifest_hash": capsule.manifest_hash,
            "capsule_hash": verification.capsule_hash,
            "verification": {
                "valid": verification.valid,
                "reason": verification.reason,
                "verified_at": verification.verified_at,
            },
        }

    def _capsule_path(self, capsule_id: str) -> Path:
        return self.capsule_dir / f"{capsule_id}.json"

    def _write_capsule(self, capsule: ContinuityCapsule) -> None:
        path = self._capsule_path(capsule.capsule_id)
        _atomic_write_json(path, capsule.to_record())
        _chmod_owner_only(path)

    def _canonicalize_state_bytes(self, state: dict[str, Any]) -> bytes:
        if not isinstance(state, dict):
            raise ContinuityCapsuleError("Capsule state must be a JSON-serializable dict")
        return json.dumps(
            state,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def _derive_capsule_key(
        self,
        *,
        passphrase: str,
        salt: bytes,
        anchor_id: str,
        capsule_id: str,
        memory_cost_kib: int | None = None,
        time_cost: int | None = None,
        parallelism: int | None = None,
    ) -> bytes:
        memory_cost_kib = memory_cost_kib if memory_cost_kib is not None else self.memory_cost_kib
        time_cost = time_cost if time_cost is not None else self.time_cost
        parallelism = parallelism if parallelism is not None else self.parallelism

        secret = (
            passphrase.encode("utf-8")
            + b"|"
            + CAPSULE_PURPOSE
            + b"|"
            + anchor_id.encode("utf-8")
            + b"|"
            + capsule_id.encode("utf-8")
        )
        return hash_secret_raw(
            secret=secret,
            salt=salt,
            time_cost=time_cost,
            memory_cost=memory_cost_kib,
            parallelism=parallelism,
            hash_len=32,
            type=Type.ID,
        )

    def _aad(self, anchor_id: str, capsule_id: str) -> bytes:
        return f"{anchor_id}:{capsule_id}".encode("utf-8")




def export_result_payload(payload: dict[str, Any]) -> str:
    """Serialize a runtime result payload in a stable capsule-safe form."""
    if not isinstance(payload, dict):
        raise ContinuityCapsuleError("Result payload must be a dict")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def import_result_payload(payload_text: str) -> dict[str, Any]:
    """Deserialize a runtime result payload from capsule-safe serialized text."""
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ContinuityCapsuleError("Result payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ContinuityCapsuleError("Result payload must decode to a dict")
    return payload

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
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


if __name__ == "__main__":
    # Minimal smoke-test flow for manual verification.
    anchor_root = Path("./.phik-anchor-demo")
    capsule_root = Path("./.phik-capsule-demo")
    passphrase = "change-this-demo-passphrase"

    anchor_service = StateAnchorService(anchor_root)
    if not (anchor_root / "anchor_manifest.json").exists():
        anchor_service.initialize(
            passphrase=passphrase,
            sovereign_name="Tal-Aren-Vox",
            user_label="Ori",
            resonant_label="StateAnchor / Sovereign Heartbeat",
        )

    store = ContinuityCapsuleStore(capsule_root, anchor_service)
    capsule = store.seal(
        passphrase=passphrase,
        state={
            "thread_summary": "Capsule smoke test",
            "semantic_phase": 9,
            "notes": ["anchor loaded", "capsule sealed"],
        },
        capsule_type="checkpoint",
        semantic_phase=9,
        tags=["smoke-test", "checkpoint"],
        summary="Initial continuity snapshot",
    )

    print("Sealed capsule:", capsule.capsule_id)
    print(store.verify_capsule(capsule.capsule_id))
    print(store.rehydrate(capsule_id=capsule.capsule_id, passphrase=passphrase))
