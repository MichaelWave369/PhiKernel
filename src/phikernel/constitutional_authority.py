from __future__ import annotations

"""Cryptographic verification of persisted human constitutional authority.

This module separates two questions:

1. Is persisted constitutional state structurally readable?
2. Is its privilege-expanding human authorization cryptographically valid?

Legacy unsigned receipts remain auditable, but promoted runtime modes may not
be actively used through the shell unless their exact HumanAuthoritySeal proof
verifies against the current StateAnchor identity.
"""

from dataclasses import dataclass
from typing import Any
import json

from phikernel.mutability import HumanAuthoritySeal, MutabilityError
from phikernel.constitutional_store import PersistedConstitutionalState
from phikernel.witness_bench import ADVISE, BOUNDED_CONTROL, SHADOW


AUTHORITY_VERIFICATION_VERSION = "0.2.0"


class ConstitutionalAuthorityError(Exception):
    """Raised when persisted signed authority proof is malformed."""


@dataclass(frozen=True)
class ConstitutionalAuthorityVerification:
    mode: str
    required: bool
    valid: bool
    advise_valid: bool | None
    control_valid: bool | None
    advise_seal_id: str | None
    control_seal_id: str | None
    reasons: tuple[str, ...]
    version: str = AUTHORITY_VERIFICATION_VERSION

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "mode": self.mode,
            "required": self.required,
            "valid": self.valid,
            "advise_valid": self.advise_valid,
            "control_valid": self.control_valid,
            "advise_seal_id": self.advise_seal_id,
            "control_seal_id": self.control_seal_id,
            "reasons": list(self.reasons),
        }


def verify_persisted_constitutional_authority(
    state: PersistedConstitutionalState,
    *,
    anchor_service: Any,
) -> ConstitutionalAuthorityVerification:
    """Verify signed human authorization for active promoted modes."""

    mode = state.promotion_state.mode
    if mode == SHADOW:
        return ConstitutionalAuthorityVerification(
            mode=SHADOW,
            required=False,
            valid=True,
            advise_valid=None,
            control_valid=None,
            advise_seal_id=None,
            control_seal_id=None,
            reasons=("SHADOW requires no privilege-expanding human signature",),
        )

    reasons: list[str] = []

    advise_valid = False
    advise_seal_id: str | None = None
    advise_receipt = state.advise_receipt
    if advise_receipt is None:
        reasons.append("ADVISE authorization receipt is missing")
    else:
        advise_seal_id = advise_receipt.human_seal_id
        ok, reason = _verify_seal_record(
            advise_receipt.human_seal_record_json,
            anchor_service=anchor_service,
            expected_seal_id=advise_receipt.human_seal_id,
            expected_actor_id=advise_receipt.human_actor_id,
            expected_authority_ref=advise_receipt.authority_ref,
            expected_metadata={
                "promotion_proposal_id": advise_receipt.proposal_id,
                "witness_report_hash": advise_receipt.witness_report_hash,
                "target_mode": ADVISE,
            },
        )
        advise_valid = ok
        if not ok:
            reasons.append(f"ADVISE seal invalid: {reason}")

    if mode == ADVISE:
        return ConstitutionalAuthorityVerification(
            mode=ADVISE,
            required=True,
            valid=advise_valid,
            advise_valid=advise_valid,
            control_valid=None,
            advise_seal_id=advise_seal_id,
            control_seal_id=None,
            reasons=(
                tuple(reasons)
                if reasons
                else ("ADVISE human authorization signature verified",)
            ),
        )

    if mode != BOUNDED_CONTROL:
        raise ConstitutionalAuthorityError(
            f"unsupported constitutional mode '{mode}'"
        )

    control_valid = False
    control_seal_id: str | None = None
    receipt = state.control_receipt
    grant = state.control_grant
    if receipt is None or grant is None:
        reasons.append(
            "BOUNDED_CONTROL receipt/grant signed authority lineage is missing"
        )
    else:
        control_seal_id = receipt.human_seal_id
        receipt_ok, receipt_reason = _verify_seal_record(
            receipt.human_seal_record_json,
            anchor_service=anchor_service,
            expected_seal_id=receipt.human_seal_id,
            expected_actor_id=receipt.human_actor_id,
            expected_authority_ref=receipt.authority_ref,
            expected_metadata={
                "control_promotion_proposal_id": receipt.proposal_id,
                "control_witness_report_hash": receipt.witness_report_hash,
                "control_contract_hash": receipt.contract_hash,
                "target_mode": BOUNDED_CONTROL,
            },
        )
        grant_ok, grant_reason = _verify_seal_record(
            grant.human_seal_record_json,
            anchor_service=anchor_service,
            expected_seal_id=grant.human_seal_id,
            expected_actor_id=grant.human_actor_id,
            expected_authority_ref=grant.authority_ref,
            expected_metadata={
                "control_promotion_proposal_id": grant.promotion_proposal_id,
                "control_witness_report_hash": grant.witness_report_hash,
                "control_contract_hash": grant.contract_hash,
                "target_mode": BOUNDED_CONTROL,
            },
        )
        if receipt_ok and grant_ok:
            try:
                receipt_record = json.loads(
                    receipt.human_seal_record_json
                )
                grant_record = json.loads(
                    grant.human_seal_record_json
                )
            except json.JSONDecodeError:
                receipt_record = None
                grant_record = None
            if receipt_record != grant_record:
                reasons.append(
                    "control receipt and grant carry different signed seal proof"
                )
            else:
                control_valid = True
        else:
            if not receipt_ok:
                reasons.append(
                    f"control receipt seal invalid: {receipt_reason}"
                )
            if not grant_ok:
                reasons.append(
                    f"control grant seal invalid: {grant_reason}"
                )

    valid = advise_valid and control_valid
    return ConstitutionalAuthorityVerification(
        mode=BOUNDED_CONTROL,
        required=True,
        valid=valid,
        advise_valid=advise_valid,
        control_valid=control_valid,
        advise_seal_id=advise_seal_id,
        control_seal_id=control_seal_id,
        reasons=(
            tuple(reasons)
            if reasons
            else (
                "ADVISE and BOUNDED_CONTROL human authorization "
                "signatures verified",
            )
        ),
    )


def _verify_seal_record(
    record_json: str,
    *,
    anchor_service: Any,
    expected_seal_id: str,
    expected_actor_id: str,
    expected_authority_ref: str,
    expected_metadata: dict[str, Any],
) -> tuple[bool, str]:
    if not record_json.strip():
        return False, "signed human seal proof is absent"

    try:
        record = json.loads(record_json)
        if not isinstance(record, dict):
            return False, "signed human seal proof is not a JSON object"
        seal = HumanAuthoritySeal.from_record(record)
    except (json.JSONDecodeError, MutabilityError) as exc:
        return False, f"signed human seal proof is malformed: {exc}"

    if not seal.is_signed:
        return False, "human authority seal is structural/unsigned"
    if seal.seal_id != expected_seal_id:
        return False, "human authority seal id mismatch"
    if seal.actor_id != expected_actor_id:
        return False, "human authority seal actor mismatch"
    if seal.authority_ref != expected_authority_ref:
        return False, "human authority seal authority_ref mismatch"

    for key, expected in expected_metadata.items():
        if seal.metadata.get(key) != expected:
            return False, f"human authority seal metadata mismatch for '{key}'"

    valid, reason = seal.verify_anchor(anchor_service)
    if not valid:
        return False, reason
    return True, "signed human authority seal verified"
