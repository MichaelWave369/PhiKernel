from __future__ import annotations

"""PhiKernel stratified mutability.

PhiKernel separates mutable runtime state into three constitutional layers:

L0 CONSTITUTION
    Runtime may read and petition. Runtime may never commit an amendment.
    An amendment requires an explicit external human authority seal.

L1 POLICY
    Runtime may propose changes. Runtime may commit only when operating under
    a bounded human-issued PolicyMutationGrant.

L2 ROUTING WEATHER
    Runtime may write ephemeral/decaying routing state directly. L2 state has
    no authority to mutate L1, L0, warrants, or citation law.

HumanAuthoritySeal supports both legacy structural seals and Anchor-signed
seals. Privilege-expanding routing/control authorization requires the signed
form and verifies it against the existing StateAnchor identity.
"""

from dataclasses import dataclass, field, replace
from math import pow
from typing import Any
import hashlib
import json
import time
import uuid


MUTABILITY_VERSION = "0.2.0"
HUMAN_AUTHORITY_SEAL_DOMAIN = "phikernel:human-authority-seal:v0.2.0"

L0 = "L0_CONSTITUTION"
L1 = "L1_POLICY"
L2 = "L2_ROUTING_WEATHER"
VALID_LAYERS = {L0, L1, L2}

HUMAN = "HUMAN"


class MutabilityError(Exception):
    """Base exception for stratified mutability failures."""


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
        raise MutabilityError("value must be deterministically JSON-serializable") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HumanAuthoritySeal:
    seal_id: str
    actor_id: str
    actor_kind: str
    authority_ref: str
    issued_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    anchor_id: str = ""
    anchor_manifest_hash: str = ""
    signature: str = ""
    domain: str = HUMAN_AUTHORITY_SEAL_DOMAIN
    version: str = MUTABILITY_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("seal_id", self.seal_id),
            ("actor_id", self.actor_id),
            ("actor_kind", self.actor_kind),
            ("authority_ref", self.authority_ref),
        ):
            if not value.strip():
                raise MutabilityError(f"{name} must be non-empty")
        if self.actor_kind != HUMAN:
            raise MutabilityError("constitutional/policy authority seal must be HUMAN")
        if self.domain != HUMAN_AUTHORITY_SEAL_DOMAIN:
            raise MutabilityError("human authority seal domain mismatch")

        crypto_values = (
            bool(self.anchor_id.strip()),
            bool(self.anchor_manifest_hash.strip()),
            bool(self.signature.strip()),
        )
        if any(crypto_values) and not all(crypto_values):
            raise MutabilityError(
                "signed human authority seal requires anchor_id, "
                "anchor_manifest_hash, and signature together"
            )
        if self.anchor_manifest_hash:
            if len(self.anchor_manifest_hash) != 64:
                raise MutabilityError(
                    "anchor_manifest_hash must be SHA-256 hex"
                )
            try:
                int(self.anchor_manifest_hash, 16)
            except ValueError as exc:
                raise MutabilityError(
                    "anchor_manifest_hash must be hexadecimal"
                ) from exc

    @property
    def is_signed(self) -> bool:
        return bool(
            self.anchor_id
            and self.anchor_manifest_hash
            and self.signature
        )

    def payload_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "domain": self.domain,
            "seal_id": self.seal_id,
            "actor_id": self.actor_id,
            "actor_kind": self.actor_kind,
            "authority_ref": self.authority_ref,
            "issued_at": self.issued_at,
            "metadata": self.metadata,
            "anchor_id": self.anchor_id,
            "anchor_manifest_hash": self.anchor_manifest_hash,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.payload_dict()).encode("utf-8")

    def payload_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_record(self) -> dict[str, Any]:
        return {
            **self.payload_dict(),
            "signature": self.signature,
            "payload_hash": self.payload_hash(),
            "is_signed": self.is_signed,
        }

    @classmethod
    def create(
        cls,
        *,
        actor_id: str,
        authority_ref: str,
        issued_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "HumanAuthoritySeal":
        """Create a legacy structural seal with no cryptographic proof."""
        return cls(
            seal_id=str(uuid.uuid4()),
            actor_id=actor_id,
            actor_kind=HUMAN,
            authority_ref=authority_ref,
            issued_at=time.time() if issued_at is None else float(issued_at),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def create_signed(
        cls,
        *,
        anchor_service: Any,
        passphrase: str,
        actor_id: str,
        authority_ref: str,
        issued_at: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "HumanAuthoritySeal":
        """Create an Anchor-signed human authority seal.

        The existing encrypted StateAnchor signing key is reused. No second
        constitutional keypair is created.
        """
        timestamp = time.time() if issued_at is None else float(issued_at)
        manifest = anchor_service.load_manifest()
        seal_id = str(uuid.uuid4())
        fields = {
            "version": MUTABILITY_VERSION,
            "domain": HUMAN_AUTHORITY_SEAL_DOMAIN,
            "seal_id": seal_id,
            "actor_id": actor_id,
            "actor_kind": HUMAN,
            "authority_ref": authority_ref,
            "issued_at": timestamp,
            "metadata": dict(metadata or {}),
            "anchor_id": manifest.anchor_id,
            "anchor_manifest_hash": manifest.manifest_hash(),
        }
        payload = _canonical_json(fields).encode("utf-8")
        signature = anchor_service.sign_bytes(passphrase, payload)
        return cls(
            seal_id=seal_id,
            actor_id=actor_id,
            actor_kind=HUMAN,
            authority_ref=authority_ref,
            issued_at=timestamp,
            metadata=dict(metadata or {}),
            anchor_id=manifest.anchor_id,
            anchor_manifest_hash=manifest.manifest_hash(),
            signature=signature,
        )

    def verify_anchor(self, anchor_service: Any) -> tuple[bool, str]:
        """Verify exact seal bytes against the current StateAnchor."""
        if not self.is_signed:
            return False, "human authority seal is not cryptographically signed"

        try:
            manifest = anchor_service.load_manifest()
            if self.anchor_id != manifest.anchor_id:
                return False, "human authority seal anchor_id mismatch"
            if self.anchor_manifest_hash != manifest.manifest_hash():
                return False, "human authority seal Anchor manifest hash mismatch"
            verification = anchor_service.verify_bytes(
                self.canonical_bytes(),
                self.signature,
            )
        except Exception as exc:
            return False, f"human authority seal Anchor verification failed: {exc}"

        if not verification.valid:
            return False, verification.reason
        return True, "human authority seal signature verified successfully"

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "HumanAuthoritySeal":
        try:
            seal = cls(
                version=str(record.get("version", MUTABILITY_VERSION)),
                domain=str(
                    record.get(
                        "domain",
                        HUMAN_AUTHORITY_SEAL_DOMAIN,
                    )
                ),
                seal_id=str(record["seal_id"]),
                actor_id=str(record["actor_id"]),
                actor_kind=str(record["actor_kind"]),
                authority_ref=str(record["authority_ref"]),
                issued_at=float(record["issued_at"]),
                metadata=dict(record.get("metadata", {})),
                anchor_id=str(record.get("anchor_id", "")),
                anchor_manifest_hash=str(
                    record.get("anchor_manifest_hash", "")
                ),
                signature=str(record.get("signature", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MutabilityError(
                "human authority seal record is malformed"
            ) from exc

        expected_hash = record.get("payload_hash")
        if expected_hash is not None and expected_hash != seal.payload_hash():
            raise MutabilityError(
                "human authority seal payload hash mismatch"
            )
        return seal


@dataclass(frozen=True)
class ConstitutionClause:
    clause_id: str
    value_json: str

    def __post_init__(self) -> None:
        if not self.clause_id.strip():
            raise MutabilityError("clause_id must be non-empty")
        try:
            json.loads(self.value_json)
        except json.JSONDecodeError as exc:
            raise MutabilityError("value_json must contain valid JSON") from exc

    @classmethod
    def create(cls, clause_id: str, value: Any) -> "ConstitutionClause":
        return cls(clause_id=clause_id, value_json=_canonical_json(value))

    @property
    def value(self) -> Any:
        return json.loads(self.value_json)

    def to_record(self) -> dict[str, Any]:
        return {"clause_id": self.clause_id, "value": self.value}


@dataclass(frozen=True)
class ConstitutionSnapshot:
    revision: int
    clauses: tuple[ConstitutionClause, ...]
    parent_snapshot_hash: str | None
    snapshot_hash: str
    created_at: float
    amended_by_seal_id: str | None = None
    version: str = MUTABILITY_VERSION

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise MutabilityError("revision must be >= 0")
        clause_ids = [clause.clause_id for clause in self.clauses]
        if len(clause_ids) != len(set(clause_ids)):
            raise MutabilityError("constitution clause ids must be unique")
        if len(self.snapshot_hash) != 64:
            raise MutabilityError("snapshot_hash must be SHA-256 hex")

    @classmethod
    def genesis(
        cls,
        clauses: tuple[ConstitutionClause, ...] | list[ConstitutionClause],
        *,
        created_at: float | None = None,
    ) -> "ConstitutionSnapshot":
        timestamp = time.time() if created_at is None else float(created_at)
        ordered = tuple(sorted(clauses, key=lambda item: item.clause_id))
        digest = _constitution_hash(
            revision=0,
            clauses=ordered,
            parent_snapshot_hash=None,
            amended_by_seal_id=None,
        )
        return cls(
            revision=0,
            clauses=ordered,
            parent_snapshot_hash=None,
            snapshot_hash=digest,
            created_at=timestamp,
        )

    def clause(self, clause_id: str) -> ConstitutionClause | None:
        return next((item for item in self.clauses if item.clause_id == clause_id), None)

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "revision": self.revision,
            "clauses": [clause.to_record() for clause in self.clauses],
            "parent_snapshot_hash": self.parent_snapshot_hash,
            "snapshot_hash": self.snapshot_hash,
            "created_at": self.created_at,
            "amended_by_seal_id": self.amended_by_seal_id,
        }


@dataclass(frozen=True)
class ConstitutionPetition:
    petition_id: str
    base_snapshot_hash: str
    clause_id: str
    proposed_value_json: str
    petitioner_id: str
    reason: str
    evidence_refs: tuple[str, ...]
    created_at: float
    authority_change: str = "NONE"
    constitutional_change: str = "PROPOSED_ONLY"
    version: str = MUTABILITY_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("petition_id", self.petition_id),
            ("base_snapshot_hash", self.base_snapshot_hash),
            ("clause_id", self.clause_id),
            ("petitioner_id", self.petitioner_id),
            ("reason", self.reason),
        ):
            if not value.strip():
                raise MutabilityError(f"{name} must be non-empty")
        if self.authority_change != "NONE":
            raise MutabilityError("petition may not change authority")
        if self.constitutional_change != "PROPOSED_ONLY":
            raise MutabilityError("petition may not apply constitutional changes")

    @property
    def proposed_value(self) -> Any:
        return json.loads(self.proposed_value_json)


@dataclass(frozen=True)
class ConstitutionAmendmentReceipt:
    receipt_id: str
    petition_id: str
    prior_snapshot_hash: str
    resulting_snapshot_hash: str
    clause_id: str
    human_seal_id: str
    authority_ref: str
    applied_at: float
    authority_change: str = "NONE"
    constitutional_change: str = "HUMAN_AUTHORIZED_APPLIED"
    version: str = MUTABILITY_VERSION


def petition_constitution_change(
    snapshot: ConstitutionSnapshot,
    *,
    clause_id: str,
    proposed_value: Any,
    petitioner_id: str,
    reason: str,
    evidence_refs: tuple[str, ...] | list[str] = (),
    created_at: float | None = None,
) -> ConstitutionPetition:
    if snapshot.clause(clause_id) is None:
        raise MutabilityError("v0.2 petitions may amend only existing clauses")
    return ConstitutionPetition(
        petition_id=str(uuid.uuid4()),
        base_snapshot_hash=snapshot.snapshot_hash,
        clause_id=clause_id,
        proposed_value_json=_canonical_json(proposed_value),
        petitioner_id=petitioner_id,
        reason=reason,
        evidence_refs=tuple(evidence_refs),
        created_at=time.time() if created_at is None else float(created_at),
    )


def apply_constitution_petition(
    snapshot: ConstitutionSnapshot,
    petition: ConstitutionPetition,
    *,
    human_seal: HumanAuthoritySeal,
    applied_at: float | None = None,
) -> tuple[ConstitutionSnapshot, ConstitutionAmendmentReceipt]:
    if petition.base_snapshot_hash != snapshot.snapshot_hash:
        raise MutabilityError("petition base snapshot does not match current constitution")
    if snapshot.clause(petition.clause_id) is None:
        raise MutabilityError("petition references unknown constitution clause")
    if human_seal.actor_kind != HUMAN:
        raise MutabilityError("L0 amendment requires HUMAN authority seal")

    clauses = tuple(
        ConstitutionClause.create(item.clause_id, petition.proposed_value)
        if item.clause_id == petition.clause_id
        else item
        for item in snapshot.clauses
    )
    revision = snapshot.revision + 1
    digest = _constitution_hash(
        revision=revision,
        clauses=clauses,
        parent_snapshot_hash=snapshot.snapshot_hash,
        amended_by_seal_id=human_seal.seal_id,
    )
    timestamp = time.time() if applied_at is None else float(applied_at)
    updated = ConstitutionSnapshot(
        revision=revision,
        clauses=clauses,
        parent_snapshot_hash=snapshot.snapshot_hash,
        snapshot_hash=digest,
        created_at=timestamp,
        amended_by_seal_id=human_seal.seal_id,
    )
    receipt = ConstitutionAmendmentReceipt(
        receipt_id=str(uuid.uuid4()),
        petition_id=petition.petition_id,
        prior_snapshot_hash=snapshot.snapshot_hash,
        resulting_snapshot_hash=updated.snapshot_hash,
        clause_id=petition.clause_id,
        human_seal_id=human_seal.seal_id,
        authority_ref=human_seal.authority_ref,
        applied_at=timestamp,
    )
    return updated, receipt


@dataclass(frozen=True)
class PolicyParameter:
    parameter_id: str
    value: float
    minimum: float
    maximum: float
    revision: int = 0
    last_receipt_id: str | None = None
    version: str = MUTABILITY_VERSION

    def __post_init__(self) -> None:
        if not self.parameter_id.strip():
            raise MutabilityError("parameter_id must be non-empty")
        if self.minimum > self.maximum:
            raise MutabilityError("policy minimum may not exceed maximum")
        if not (self.minimum <= self.value <= self.maximum):
            raise MutabilityError("policy value must remain inside declared bounds")
        if self.revision < 0:
            raise MutabilityError("policy revision must be >= 0")


@dataclass(frozen=True)
class PolicyChangeProposal:
    proposal_id: str
    parameter_id: str
    base_revision: int
    proposed_value: float
    proposer_id: str
    source_ref: str
    reason: str
    created_at: float
    authority_change: str = "NONE"
    policy_change: str = "PROPOSED_ONLY"
    version: str = MUTABILITY_VERSION


@dataclass(frozen=True)
class PolicyMutationGrant:
    grant_id: str
    parameter_id: str
    grantee_id: str
    minimum: float
    maximum: float
    human_seal_id: str
    authority_ref: str
    issued_at: float
    expires_at: float | None = None
    revoked: bool = False
    version: str = MUTABILITY_VERSION

    def __post_init__(self) -> None:
        if self.minimum > self.maximum:
            raise MutabilityError("grant minimum may not exceed maximum")

    def active_at(self, now: float) -> bool:
        return not self.revoked and (
            self.expires_at is None or now < self.expires_at
        )


@dataclass(frozen=True)
class PolicyMutationReceipt:
    receipt_id: str
    proposal_id: str
    parameter_id: str
    prior_revision: int
    resulting_revision: int
    prior_value: float
    resulting_value: float
    grant_id: str
    actor_id: str
    source_ref: str
    applied_at: float
    authority_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = MUTABILITY_VERSION


def propose_policy_change(
    policy: PolicyParameter,
    *,
    proposed_value: float,
    proposer_id: str,
    source_ref: str,
    reason: str,
    created_at: float | None = None,
) -> PolicyChangeProposal:
    if not proposer_id.strip() or not source_ref.strip() or not reason.strip():
        raise MutabilityError("policy proposal fields must be non-empty")
    return PolicyChangeProposal(
        proposal_id=str(uuid.uuid4()),
        parameter_id=policy.parameter_id,
        base_revision=policy.revision,
        proposed_value=float(proposed_value),
        proposer_id=proposer_id,
        source_ref=source_ref,
        reason=reason,
        created_at=time.time() if created_at is None else float(created_at),
    )


def issue_policy_mutation_grant(
    policy: PolicyParameter,
    *,
    grantee_id: str,
    minimum: float,
    maximum: float,
    human_seal: HumanAuthoritySeal,
    lifetime_seconds: float | None = None,
    issued_at: float | None = None,
) -> PolicyMutationGrant:
    if not grantee_id.strip():
        raise MutabilityError("grantee_id must be non-empty")
    lower = float(minimum)
    upper = float(maximum)
    if lower < policy.minimum or upper > policy.maximum:
        raise MutabilityError("grant may not exceed policy parameter bounds")
    if lower > upper:
        raise MutabilityError("grant minimum may not exceed maximum")
    if lifetime_seconds is not None and lifetime_seconds < 0:
        raise MutabilityError("lifetime_seconds must be >= 0")
    timestamp = time.time() if issued_at is None else float(issued_at)
    return PolicyMutationGrant(
        grant_id=str(uuid.uuid4()),
        parameter_id=policy.parameter_id,
        grantee_id=grantee_id,
        minimum=lower,
        maximum=upper,
        human_seal_id=human_seal.seal_id,
        authority_ref=human_seal.authority_ref,
        issued_at=timestamp,
        expires_at=None if lifetime_seconds is None else timestamp + lifetime_seconds,
    )


def apply_policy_proposal(
    policy: PolicyParameter,
    proposal: PolicyChangeProposal,
    *,
    actor_id: str,
    grant: PolicyMutationGrant,
    applied_at: float | None = None,
) -> tuple[PolicyParameter, PolicyMutationReceipt]:
    timestamp = time.time() if applied_at is None else float(applied_at)

    if proposal.parameter_id != policy.parameter_id:
        raise MutabilityError("proposal targets a different policy parameter")
    if proposal.base_revision != policy.revision:
        raise MutabilityError("policy proposal is stale")
    if grant.parameter_id != policy.parameter_id:
        raise MutabilityError("grant targets a different policy parameter")
    if grant.grantee_id != actor_id:
        raise MutabilityError("policy mutation grant belongs to a different actor")
    if not grant.active_at(timestamp):
        raise MutabilityError("policy mutation grant is expired or revoked")
    if not (policy.minimum <= proposal.proposed_value <= policy.maximum):
        raise MutabilityError("proposed value exceeds policy parameter bounds")
    if not (grant.minimum <= proposal.proposed_value <= grant.maximum):
        raise MutabilityError("proposed value exceeds mutation grant bounds")

    receipt_id = str(uuid.uuid4())
    updated = replace(
        policy,
        value=proposal.proposed_value,
        revision=policy.revision + 1,
        last_receipt_id=receipt_id,
    )
    receipt = PolicyMutationReceipt(
        receipt_id=receipt_id,
        proposal_id=proposal.proposal_id,
        parameter_id=policy.parameter_id,
        prior_revision=policy.revision,
        resulting_revision=updated.revision,
        prior_value=policy.value,
        resulting_value=updated.value,
        grant_id=grant.grant_id,
        actor_id=actor_id,
        source_ref=proposal.source_ref,
        applied_at=timestamp,
    )
    return updated, receipt


@dataclass(frozen=True)
class RoutingWeatherEntry:
    key: str
    value: float
    observed_at: float
    half_life_seconds: float
    source_ref: str

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.source_ref.strip():
            raise MutabilityError("routing weather key/source_ref must be non-empty")
        if self.half_life_seconds <= 0:
            raise MutabilityError("routing weather half_life_seconds must be > 0")

    def value_at(self, now: float) -> float:
        age = max(0.0, now - self.observed_at)
        return self.value * pow(0.5, age / self.half_life_seconds)


@dataclass(frozen=True)
class RoutingWeatherState:
    entries: tuple[RoutingWeatherEntry, ...] = ()
    revision: int = 0
    version: str = MUTABILITY_VERSION

    def entry(self, key: str) -> RoutingWeatherEntry | None:
        return next((item for item in self.entries if item.key == key), None)


@dataclass(frozen=True)
class RoutingWeatherReceipt:
    receipt_id: str
    key: str
    prior_revision: int
    resulting_revision: int
    actor_id: str
    source_ref: str
    applied_at: float
    authority_change: str = "NONE"
    policy_change: str = "NONE"
    constitutional_change: str = "NONE"
    version: str = MUTABILITY_VERSION


def apply_routing_weather_update(
    state: RoutingWeatherState,
    *,
    key: str,
    value: float,
    actor_id: str,
    source_ref: str,
    half_life_seconds: float,
    applied_at: float | None = None,
) -> tuple[RoutingWeatherState, RoutingWeatherReceipt]:
    if not actor_id.strip():
        raise MutabilityError("actor_id must be non-empty")
    timestamp = time.time() if applied_at is None else float(applied_at)
    new_entry = RoutingWeatherEntry(
        key=key,
        value=float(value),
        observed_at=timestamp,
        half_life_seconds=float(half_life_seconds),
        source_ref=source_ref,
    )
    entries = tuple(item for item in state.entries if item.key != key) + (new_entry,)
    updated = RoutingWeatherState(
        entries=tuple(sorted(entries, key=lambda item: item.key)),
        revision=state.revision + 1,
    )
    receipt = RoutingWeatherReceipt(
        receipt_id=str(uuid.uuid4()),
        key=key,
        prior_revision=state.revision,
        resulting_revision=updated.revision,
        actor_id=actor_id,
        source_ref=source_ref,
        applied_at=timestamp,
    )
    return updated, receipt


def _constitution_hash(
    *,
    revision: int,
    clauses: tuple[ConstitutionClause, ...],
    parent_snapshot_hash: str | None,
    amended_by_seal_id: str | None,
) -> str:
    payload = {
        "revision": revision,
        "clauses": [clause.to_record() for clause in clauses],
        "parent_snapshot_hash": parent_snapshot_hash,
        "amended_by_seal_id": amended_by_seal_id,
    }
    return _sha256_text(_canonical_json(payload))
