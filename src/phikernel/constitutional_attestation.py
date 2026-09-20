from __future__ import annotations

"""Anchor-backed attestations for PhiKernel constitutional persistence.

This module binds the *heads* of the constitutional state/history chain and the
constitutional action journal to the existing StateAnchor Ed25519 identity.

A current-head attestation authenticates the earlier structural hash chains
behind those heads without rewriting prior records.

Important distinctions:
- hash chaining proves structural linkage
- Anchor signatures bind that linked state to the current Anchor identity
- attestation does not itself grant runtime authority
- a valid but stale attestation does not authenticate newer unsigned writes
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import time
import uuid

from phikernel.anchor import (
    AnchorUnlockError,
    StateAnchorError,
    StateAnchorService,
)
from phikernel.constitutional_action import (
    ConstitutionalActionError,
    ConstitutionalActionJournal,
)
from phikernel.constitutional_store import (
    ConstitutionalPersistenceError,
    ConstitutionalStateStore,
)


ATTESTATION_VERSION = "0.2.0"
ATTESTATION_DOMAIN = "phikernel:constitutional-attestation:v0.2.0"


class ConstitutionalAttestationError(Exception):
    """Base exception for constitutional Anchor attestation failures."""


class ConstitutionalAttestationIntegrityError(
    ConstitutionalAttestationError
):
    """Raised when the attestation chain is malformed or tampered."""


class ConstitutionalAttestationSigningError(
    ConstitutionalAttestationError
):
    """Raised when an attestation cannot be signed by the current Anchor."""


@dataclass(frozen=True)
class ConstitutionalHeads:
    state_snapshot_hash: str | None
    state_history_count: int
    action_event_hash: str | None
    action_event_count: int

    def __post_init__(self) -> None:
        if self.state_history_count < 0 or self.action_event_count < 0:
            raise ConstitutionalAttestationIntegrityError(
                "constitutional head counts must be >= 0"
            )
        if self.state_history_count == 0:
            if self.state_snapshot_hash is not None:
                raise ConstitutionalAttestationIntegrityError(
                    "state hash requires non-empty state history"
                )
        else:
            _require_sha256(
                "state_snapshot_hash",
                self.state_snapshot_hash,
            )
        if self.action_event_count == 0:
            if self.action_event_hash is not None:
                raise ConstitutionalAttestationIntegrityError(
                    "action hash requires non-empty action journal"
                )
        else:
            _require_sha256(
                "action_event_hash",
                self.action_event_hash,
            )

    @property
    def empty(self) -> bool:
        return (
            self.state_history_count == 0
            and self.action_event_count == 0
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "state_snapshot_hash": self.state_snapshot_hash,
            "state_history_count": self.state_history_count,
            "action_event_hash": self.action_event_hash,
            "action_event_count": self.action_event_count,
        }


@dataclass(frozen=True)
class ConstitutionalAttestation:
    attestation_id: str
    anchor_id: str
    anchor_manifest_hash: str
    heads: ConstitutionalHeads
    attested_at: float
    previous_attestation_hash: str | None
    signature: str
    attestation_hash: str
    domain: str = ATTESTATION_DOMAIN
    version: str = ATTESTATION_VERSION

    def __post_init__(self) -> None:
        if not self.attestation_id.strip():
            raise ConstitutionalAttestationIntegrityError(
                "attestation_id must be non-empty"
            )
        if not self.anchor_id.strip():
            raise ConstitutionalAttestationIntegrityError(
                "anchor_id must be non-empty"
            )
        _require_sha256(
            "anchor_manifest_hash",
            self.anchor_manifest_hash,
        )
        if self.previous_attestation_hash is not None:
            _require_sha256(
                "previous_attestation_hash",
                self.previous_attestation_hash,
            )
        if not self.signature.strip():
            raise ConstitutionalAttestationIntegrityError(
                "attestation signature must be non-empty"
            )
        _require_sha256(
            "attestation_hash",
            self.attestation_hash,
        )
        if self.domain != ATTESTATION_DOMAIN:
            raise ConstitutionalAttestationIntegrityError(
                "constitutional attestation domain mismatch"
            )
        if self.version != ATTESTATION_VERSION:
            raise ConstitutionalAttestationIntegrityError(
                "unsupported constitutional attestation version"
            )

    def payload_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "domain": self.domain,
            "attestation_id": self.attestation_id,
            "anchor_id": self.anchor_id,
            "anchor_manifest_hash": self.anchor_manifest_hash,
            "heads": self.heads.to_record(),
            "attested_at": self.attested_at,
            "previous_attestation_hash": self.previous_attestation_hash,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.payload_dict()).encode("utf-8")

    def hash_material(self) -> dict[str, Any]:
        return {
            "payload": self.payload_dict(),
            "signature": self.signature,
        }

    def computed_hash(self) -> str:
        return _hash_json(self.hash_material())

    def to_record(self) -> dict[str, Any]:
        return {
            **self.payload_dict(),
            "signature": self.signature,
            "attestation_hash": self.attestation_hash,
        }


@dataclass(frozen=True)
class ConstitutionalAttestationVerification:
    valid: bool
    current: bool
    reason: str
    anchor_id: str | None
    anchor_manifest_hash: str | None
    attestation_count: int
    latest_attestation_hash: str | None
    attested_heads: ConstitutionalHeads | None
    current_heads: ConstitutionalHeads
    verified_at: float = field(default_factory=time.time)

    @property
    def authenticated_current_state(self) -> bool:
        return self.valid and self.current

    def to_record(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "current": self.current,
            "authenticated_current_state": (
                self.authenticated_current_state
            ),
            "reason": self.reason,
            "anchor_id": self.anchor_id,
            "anchor_manifest_hash": self.anchor_manifest_hash,
            "attestation_count": self.attestation_count,
            "latest_attestation_hash": self.latest_attestation_hash,
            "attested_heads": (
                None
                if self.attested_heads is None
                else self.attested_heads.to_record()
            ),
            "current_heads": self.current_heads.to_record(),
            "verified_at": self.verified_at,
        }


class ConstitutionalAttestationStore:
    """Append-only detached Anchor attestation chain."""

    def __init__(
        self,
        runtime_root: str | Path,
        anchor_service: StateAnchorService,
    ) -> None:
        self.runtime_root = Path(runtime_root)
        self.root = self.runtime_root / "constitutional"
        self.path = self.root / "anchor_attestations.jsonl"
        self.anchor_service = anchor_service
        self.state_store = ConstitutionalStateStore(self.runtime_root)
        self.action_journal = ConstitutionalActionJournal(
            self.runtime_root
        )

    def current_heads(self) -> ConstitutionalHeads:
        state_hash: str | None = None
        state_count = 0
        if self.state_store.exists():
            snapshot = self.state_store.load_snapshot_for_recovery()
            history = self.state_store.history()
            if not history:
                raise ConstitutionalAttestationIntegrityError(
                    "constitutional state exists without history"
                )
            if history[-1].snapshot_hash != snapshot.snapshot_hash:
                raise ConstitutionalAttestationIntegrityError(
                    "constitutional state head does not match history"
                )
            state_hash = snapshot.snapshot_hash
            state_count = len(history)

        action_history = self.action_journal.history()
        action_hash = (
            None
            if not action_history
            else action_history[-1].event_hash
        )
        action_count = len(action_history)

        return ConstitutionalHeads(
            state_snapshot_hash=state_hash,
            state_history_count=state_count,
            action_event_hash=action_hash,
            action_event_count=action_count,
        )

    def bind_current_heads(
        self,
        *,
        passphrase: str,
        attested_at: float | None = None,
    ) -> ConstitutionalAttestation:
        """Sign the current constitutional heads with the Anchor key."""

        timestamp = (
            time.time()
            if attested_at is None
            else float(attested_at)
        )
        try:
            anchor_verification = self.anchor_service.verify_anchor()
        except StateAnchorError as exc:
            raise ConstitutionalAttestationSigningError(
                f"cannot attest constitutional state because Anchor is unavailable: {exc}"
            ) from exc
        if not anchor_verification.valid:
            raise ConstitutionalAttestationSigningError(
                "cannot attest constitutional state because Anchor "
                f"verification failed: {anchor_verification.reason}"
            )

        heads = self.current_heads()
        if heads.empty:
            raise ConstitutionalAttestationSigningError(
                "no constitutional state or action journal exists to attest"
            )

        history = self.history()
        if history:
            existing = self.verify()
            if not existing.valid:
                raise ConstitutionalAttestationSigningError(
                    "refusing to extend invalid constitutional "
                    f"attestation chain: {existing.reason}"
                )
        previous_hash = (
            None
            if not history
            else history[-1].attestation_hash
        )
        try:
            manifest = self.anchor_service.load_manifest()
        except StateAnchorError as exc:
            raise ConstitutionalAttestationSigningError(
                f"cannot load Anchor manifest for attestation: {exc}"
            ) from exc

        unsigned = ConstitutionalAttestation(
            attestation_id=str(uuid.uuid4()),
            anchor_id=manifest.anchor_id,
            anchor_manifest_hash=manifest.manifest_hash(),
            heads=heads,
            attested_at=timestamp,
            previous_attestation_hash=previous_hash,
            signature="pending",
            attestation_hash="0" * 64,
        )

        try:
            signature = self.anchor_service.sign_bytes(
                passphrase,
                unsigned.canonical_bytes(),
            )
        except (AnchorUnlockError, StateAnchorError) as exc:
            raise ConstitutionalAttestationSigningError(
                f"Anchor signing failed: {exc}"
            ) from exc

        signed = ConstitutionalAttestation(
            attestation_id=unsigned.attestation_id,
            anchor_id=unsigned.anchor_id,
            anchor_manifest_hash=unsigned.anchor_manifest_hash,
            heads=unsigned.heads,
            attested_at=unsigned.attested_at,
            previous_attestation_hash=unsigned.previous_attestation_hash,
            signature=signature,
            attestation_hash="0" * 64,
        )
        final = ConstitutionalAttestation(
            attestation_id=signed.attestation_id,
            anchor_id=signed.anchor_id,
            anchor_manifest_hash=signed.anchor_manifest_hash,
            heads=signed.heads,
            attested_at=signed.attested_at,
            previous_attestation_hash=signed.previous_attestation_hash,
            signature=signed.signature,
            attestation_hash=signed.computed_hash(),
        )

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(
                _canonical_json(final.to_record()) + "\n"
            )
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return final

    def history(self) -> tuple[ConstitutionalAttestation, ...]:
        if not self.path.exists():
            return ()

        attestations: list[ConstitutionalAttestation] = []
        previous_hash: str | None = None
        with self.path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ConstitutionalAttestationIntegrityError(
                        f"attestation line {line_no} is invalid JSON"
                    ) from exc
                if not isinstance(record, dict):
                    raise ConstitutionalAttestationIntegrityError(
                        f"attestation line {line_no} is not an object"
                    )

                attestation = _attestation_from_record(record)
                if (
                    attestation.previous_attestation_hash
                    != previous_hash
                ):
                    raise ConstitutionalAttestationIntegrityError(
                        "constitutional attestation chain broken at "
                        f"line {line_no}"
                    )
                if attestation.computed_hash() != attestation.attestation_hash:
                    raise ConstitutionalAttestationIntegrityError(
                        "constitutional attestation hash mismatch at "
                        f"line {line_no}"
                    )
                previous_hash = attestation.attestation_hash
                attestations.append(attestation)

        return tuple(attestations)

    def verify(self) -> ConstitutionalAttestationVerification:
        """Verify Anchor identity, every signature, chain, and current heads."""

        current_heads = self.current_heads()
        try:
            anchor_verification = self.anchor_service.verify_anchor()
            manifest = self.anchor_service.load_manifest()
        except StateAnchorError as exc:
            return ConstitutionalAttestationVerification(
                valid=False,
                current=False,
                reason=f"Anchor is unavailable: {exc}",
                anchor_id=None,
                anchor_manifest_hash=None,
                attestation_count=0,
                latest_attestation_hash=None,
                attested_heads=None,
                current_heads=current_heads,
            )

        if not anchor_verification.valid:
            return ConstitutionalAttestationVerification(
                valid=False,
                current=False,
                reason=(
                    "Anchor manifest verification failed: "
                    f"{anchor_verification.reason}"
                ),
                anchor_id=manifest.anchor_id,
                anchor_manifest_hash=manifest.manifest_hash(),
                attestation_count=0,
                latest_attestation_hash=None,
                attested_heads=None,
                current_heads=current_heads,
            )

        try:
            history = self.history()
        except ConstitutionalAttestationError as exc:
            return ConstitutionalAttestationVerification(
                valid=False,
                current=False,
                reason=str(exc),
                anchor_id=manifest.anchor_id,
                anchor_manifest_hash=manifest.manifest_hash(),
                attestation_count=0,
                latest_attestation_hash=None,
                attested_heads=None,
                current_heads=current_heads,
            )

        if not history:
            return ConstitutionalAttestationVerification(
                valid=False,
                current=False,
                reason="No constitutional Anchor attestation exists",
                anchor_id=manifest.anchor_id,
                anchor_manifest_hash=manifest.manifest_hash(),
                attestation_count=0,
                latest_attestation_hash=None,
                attested_heads=None,
                current_heads=current_heads,
            )

        for index, attestation in enumerate(history):
            if attestation.anchor_id != manifest.anchor_id:
                return ConstitutionalAttestationVerification(
                    valid=False,
                    current=False,
                    reason=(
                        "Attestation anchor_id does not match current "
                        f"Anchor at index {index}"
                    ),
                    anchor_id=manifest.anchor_id,
                    anchor_manifest_hash=manifest.manifest_hash(),
                    attestation_count=len(history),
                    latest_attestation_hash=history[-1].attestation_hash,
                    attested_heads=history[-1].heads,
                    current_heads=current_heads,
                )
            if (
                attestation.anchor_manifest_hash
                != manifest.manifest_hash()
            ):
                return ConstitutionalAttestationVerification(
                    valid=False,
                    current=False,
                    reason=(
                        "Attestation manifest hash does not match current "
                        f"Anchor at index {index}"
                    ),
                    anchor_id=manifest.anchor_id,
                    anchor_manifest_hash=manifest.manifest_hash(),
                    attestation_count=len(history),
                    latest_attestation_hash=history[-1].attestation_hash,
                    attested_heads=history[-1].heads,
                    current_heads=current_heads,
                )

            verification = self.anchor_service.verify_bytes(
                attestation.canonical_bytes(),
                attestation.signature,
            )
            if not verification.valid:
                return ConstitutionalAttestationVerification(
                    valid=False,
                    current=False,
                    reason=(
                        "Anchor signature verification failed at "
                        f"attestation index {index}: "
                        f"{verification.reason}"
                    ),
                    anchor_id=manifest.anchor_id,
                    anchor_manifest_hash=manifest.manifest_hash(),
                    attestation_count=len(history),
                    latest_attestation_hash=history[-1].attestation_hash,
                    attested_heads=history[-1].heads,
                    current_heads=current_heads,
                )

        latest = history[-1]
        is_current = latest.heads == current_heads
        return ConstitutionalAttestationVerification(
            valid=True,
            current=is_current,
            reason=(
                "Constitutional heads are Anchor-attested and current"
                if is_current
                else (
                    "Anchor attestation chain is valid but stale; "
                    "newer constitutional writes are not attested"
                )
            ),
            anchor_id=manifest.anchor_id,
            anchor_manifest_hash=manifest.manifest_hash(),
            attestation_count=len(history),
            latest_attestation_hash=latest.attestation_hash,
            attested_heads=latest.heads,
            current_heads=current_heads,
        )


def _attestation_from_record(
    record: dict[str, Any],
) -> ConstitutionalAttestation:
    try:
        heads_record = record["heads"]
        if not isinstance(heads_record, dict):
            raise TypeError("heads must be an object")
        heads = ConstitutionalHeads(
            state_snapshot_hash=heads_record.get(
                "state_snapshot_hash"
            ),
            state_history_count=int(
                heads_record["state_history_count"]
            ),
            action_event_hash=heads_record.get(
                "action_event_hash"
            ),
            action_event_count=int(
                heads_record["action_event_count"]
            ),
        )
        return ConstitutionalAttestation(
            version=str(record["version"]),
            domain=str(record["domain"]),
            attestation_id=str(record["attestation_id"]),
            anchor_id=str(record["anchor_id"]),
            anchor_manifest_hash=str(
                record["anchor_manifest_hash"]
            ),
            heads=heads,
            attested_at=float(record["attested_at"]),
            previous_attestation_hash=record.get(
                "previous_attestation_hash"
            ),
            signature=str(record["signature"]),
            attestation_hash=str(record["attestation_hash"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConstitutionalAttestationIntegrityError(
            "constitutional attestation record is malformed"
        ) from exc


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConstitutionalAttestationIntegrityError(
            "constitutional attestation must be deterministic JSON"
        ) from exc


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _require_sha256(name: str, value: str | None) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ConstitutionalAttestationIntegrityError(
            f"{name} must be SHA-256 hex"
        )
    try:
        int(value, 16)
    except ValueError as exc:
        raise ConstitutionalAttestationIntegrityError(
            f"{name} must be hexadecimal"
        ) from exc
