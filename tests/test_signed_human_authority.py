from dataclasses import replace
import json
from pathlib import Path

from phikernel.anchor import StateAnchorService
from phikernel.constitutional_authority import (
    verify_persisted_constitutional_authority,
)
from phikernel.constitutional_store import PersistedConstitutionalState
from phikernel.mutability import HumanAuthoritySeal
from phikernel.witness_bench import (
    ADVISE,
    SHADOW,
    PromotionAuthorizationReceipt,
    PromotionState,
)


def _anchor(root: Path, label: str):
    service = StateAnchorService(root)
    passphrase = "resonance-is-the-key-369"
    service.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label=label,
    )
    return service, passphrase


def _signed_advise_state(anchor, passphrase):
    proposal_id = "proposal:signed-advise"
    report_hash = "a" * 64
    seal = HumanAuthoritySeal.create_signed(
        anchor_service=anchor,
        passphrase=passphrase,
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=200.0,
        metadata={
            "promotion_proposal_id": proposal_id,
            "witness_report_hash": report_hash,
            "target_mode": ADVISE,
        },
    )
    receipt = PromotionAuthorizationReceipt(
        receipt_id="receipt:signed-advise",
        proposal_id=proposal_id,
        witness_report_id="report:signed-advise",
        witness_report_hash=report_hash,
        prior_mode=SHADOW,
        resulting_mode=ADVISE,
        prior_revision=0,
        resulting_revision=1,
        human_seal_id=seal.seal_id,
        human_actor_id=seal.actor_id,
        authority_ref=seal.authority_ref,
        applied_at=201.0,
        human_seal_record_json=json.dumps(
            seal.to_record(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    )
    return PersistedConstitutionalState(
        promotion_state=PromotionState(
            mode=ADVISE,
            revision=1,
            last_receipt_id=receipt.receipt_id,
            authorized_by_seal_id=seal.seal_id,
        ),
        advise_receipt=receipt,
    ), seal


def test_signed_human_seal_verifies_against_exact_anchor(tmp_path: Path) -> None:
    anchor, passphrase = _anchor(tmp_path / "anchor-a", "A")
    other, _ = _anchor(tmp_path / "anchor-b", "B")

    seal = HumanAuthoritySeal.create_signed(
        anchor_service=anchor,
        passphrase=passphrase,
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=100.0,
        metadata={"purpose": "fixture"},
    )

    valid, _ = seal.verify_anchor(anchor)
    wrong, _ = seal.verify_anchor(other)

    assert seal.is_signed is True
    assert valid is True
    assert wrong is False
    assert seal.payload_hash()
    assert seal.to_record()["signature"]


def test_signed_human_seal_metadata_tamper_breaks_signature(tmp_path: Path) -> None:
    anchor, passphrase = _anchor(tmp_path / "anchor", "A")
    seal = HumanAuthoritySeal.create_signed(
        anchor_service=anchor,
        passphrase=passphrase,
        actor_id="human:mikey",
        authority_ref="anchor:human",
        issued_at=100.0,
        metadata={"target_mode": ADVISE},
    )

    tampered = replace(
        seal,
        metadata={"target_mode": "BOUNDED_CONTROL"},
    )
    valid, _ = tampered.verify_anchor(anchor)

    assert valid is False


def test_structural_seal_is_explicitly_unsigned(tmp_path: Path) -> None:
    anchor, _ = _anchor(tmp_path / "anchor", "A")
    seal = HumanAuthoritySeal.create(
        actor_id="human:mikey",
        authority_ref="anchor:human",
        metadata={"target_mode": ADVISE},
    )

    valid, reason = seal.verify_anchor(anchor)

    assert seal.is_signed is False
    assert valid is False
    assert "not cryptographically signed" in reason


def test_persisted_signed_advise_authority_verifies(tmp_path: Path) -> None:
    anchor, passphrase = _anchor(tmp_path / "anchor", "A")
    state, seal = _signed_advise_state(anchor, passphrase)

    result = verify_persisted_constitutional_authority(
        state,
        anchor_service=anchor,
    )

    assert result.required is True
    assert result.valid is True
    assert result.advise_valid is True
    assert result.advise_seal_id == seal.seal_id


def test_legacy_unsigned_advise_state_remains_auditable_but_unusable(
    tmp_path: Path,
) -> None:
    anchor, _ = _anchor(tmp_path / "anchor", "A")
    receipt = PromotionAuthorizationReceipt(
        receipt_id="receipt:legacy",
        proposal_id="proposal:legacy",
        witness_report_id="report:legacy",
        witness_report_hash="a" * 64,
        prior_mode=SHADOW,
        resulting_mode=ADVISE,
        prior_revision=0,
        resulting_revision=1,
        human_seal_id="seal:legacy",
        human_actor_id="human:mikey",
        authority_ref="anchor:legacy",
        applied_at=100.0,
    )
    state = PersistedConstitutionalState(
        promotion_state=PromotionState(
            mode=ADVISE,
            revision=1,
            last_receipt_id=receipt.receipt_id,
            authorized_by_seal_id=receipt.human_seal_id,
        ),
        advise_receipt=receipt,
    )

    result = verify_persisted_constitutional_authority(
        state,
        anchor_service=anchor,
    )

    assert result.required is True
    assert result.valid is False
    assert result.advise_valid is False
    assert any("absent" in reason for reason in result.reasons)


def test_persisted_seal_record_tamper_is_rejected(tmp_path: Path) -> None:
    anchor, passphrase = _anchor(tmp_path / "anchor", "A")
    state, _ = _signed_advise_state(anchor, passphrase)
    record = json.loads(state.advise_receipt.human_seal_record_json)
    record["metadata"]["target_mode"] = "BOUNDED_CONTROL"

    tampered_receipt = replace(
        state.advise_receipt,
        human_seal_record_json=json.dumps(
            record,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    tampered_state = replace(
        state,
        advise_receipt=tampered_receipt,
    )

    result = verify_persisted_constitutional_authority(
        tampered_state,
        anchor_service=anchor,
    )

    assert result.valid is False
    assert result.advise_valid is False


def test_shadow_needs_no_privilege_signature(tmp_path: Path) -> None:
    anchor, _ = _anchor(tmp_path / "anchor", "A")

    result = verify_persisted_constitutional_authority(
        PersistedConstitutionalState.genesis(),
        anchor_service=anchor,
    )

    assert result.required is False
    assert result.valid is True
