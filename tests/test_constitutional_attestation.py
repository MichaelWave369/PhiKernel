import hashlib
import json
from pathlib import Path

import pytest

from phikernel.anchor import StateAnchorService
from phikernel.constitutional_action import (
    REFUSED_EVENT,
    ConstitutionalActionJournal,
)
from phikernel.constitutional_attestation import (
    ConstitutionalAttestationSigningError,
    ConstitutionalAttestationStore,
)
from phikernel.constitutional_store import (
    ConstitutionalStateStore,
    PersistedConstitutionalState,
)


def _anchor(root: Path, *, label: str = "Anchor"):
    service = StateAnchorService(root)
    passphrase = "resonance-is-the-key-369"
    service.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label=label,
    )
    return service, passphrase


def _seed_state(runtime_root: Path, *, written_at: float = 100.0):
    store = ConstitutionalStateStore(runtime_root)
    snapshot = store.save(
        PersistedConstitutionalState.genesis(),
        written_at=written_at,
        now=written_at,
    )
    return store, snapshot


def _append_action(runtime_root: Path, *, recorded_at: float = 101.0):
    journal = ConstitutionalActionJournal(runtime_root)
    event = journal.append(
        transaction_id="tx:fixture",
        event_type=REFUSED_EVENT,
        payload={"reason": "fixture refusal"},
        recorded_at=recorded_at,
    )
    return journal, event


def _attestation_hash(record: dict) -> str:
    payload = {
        "version": record["version"],
        "domain": record["domain"],
        "attestation_id": record["attestation_id"],
        "anchor_id": record["anchor_id"],
        "anchor_manifest_hash": record["anchor_manifest_hash"],
        "heads": record["heads"],
        "attested_at": record["attested_at"],
        "previous_attestation_hash": record["previous_attestation_hash"],
    }
    material = {
        "payload": payload,
        "signature": record["signature"],
    }
    raw = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def test_no_attestation_reports_unbound_current_heads(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, _ = _anchor(tmp_path / "anchor")
    _seed_state(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)

    result = attest.verify()

    assert result.valid is False
    assert result.current is False
    assert result.authenticated_current_state is False
    assert result.attestation_count == 0
    assert result.current_heads.state_history_count == 1
    assert "No constitutional Anchor attestation exists" in result.reason


def test_existing_unsigned_state_and_action_heads_can_be_checkpoint_attested(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, passphrase = _anchor(tmp_path / "anchor")
    state_store, state_snapshot = _seed_state(runtime_root)
    journal, action_event = _append_action(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)

    signed = attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=102.0,
    )
    result = attest.verify()

    assert signed.heads.state_snapshot_hash == state_snapshot.snapshot_hash
    assert signed.heads.state_history_count == len(state_store.history())
    assert signed.heads.action_event_hash == action_event.event_hash
    assert signed.heads.action_event_count == len(journal.history())
    assert signed.previous_attestation_hash is None
    assert signed.signature
    assert signed.attestation_hash

    assert result.valid is True
    assert result.current is True
    assert result.authenticated_current_state is True
    assert result.attestation_count == 1
    assert result.latest_attestation_hash == signed.attestation_hash
    assert "current" in result.reason.lower()


def test_state_advance_after_attestation_is_valid_but_stale(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, passphrase = _anchor(tmp_path / "anchor")
    state_store, _ = _seed_state(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)
    first = attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=101.0,
    )

    second_snapshot = state_store.save(
        PersistedConstitutionalState.genesis(),
        written_at=102.0,
        now=102.0,
    )
    result = attest.verify()

    assert result.valid is True
    assert result.current is False
    assert result.authenticated_current_state is False
    assert result.attested_heads.state_snapshot_hash == first.heads.state_snapshot_hash
    assert result.current_heads.state_snapshot_hash == second_snapshot.snapshot_hash
    assert result.current_heads.state_history_count == 2
    assert "stale" in result.reason.lower()


def test_action_advance_after_attestation_is_valid_but_stale(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, passphrase = _anchor(tmp_path / "anchor")
    _seed_state(runtime_root)
    journal, _ = _append_action(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)
    first = attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=102.0,
    )

    next_event = journal.append(
        transaction_id="tx:fixture-2",
        event_type=REFUSED_EVENT,
        payload={"reason": "later fixture"},
        recorded_at=103.0,
    )
    result = attest.verify()

    assert result.valid is True
    assert result.current is False
    assert result.attested_heads.action_event_hash == first.heads.action_event_hash
    assert result.current_heads.action_event_hash == next_event.event_hash
    assert result.current_heads.action_event_count == 2


def test_rebinding_stale_heads_extends_signed_attestation_chain(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, passphrase = _anchor(tmp_path / "anchor")
    state_store, _ = _seed_state(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)

    first = attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=101.0,
    )
    state_store.save(
        PersistedConstitutionalState.genesis(),
        written_at=102.0,
        now=102.0,
    )
    stale = attest.verify()
    assert stale.valid is True
    assert stale.current is False

    second = attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=103.0,
    )
    current = attest.verify()
    history = attest.history()

    assert second.previous_attestation_hash == first.attestation_hash
    assert len(history) == 2
    assert current.valid is True
    assert current.current is True
    assert current.attestation_count == 2
    assert current.latest_attestation_hash == second.attestation_hash


def test_wrong_passphrase_cannot_create_attestation(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, _ = _anchor(tmp_path / "anchor")
    _seed_state(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)

    with pytest.raises(ConstitutionalAttestationSigningError):
        attest.bind_current_heads(
            passphrase="wrong-passphrase",
            attested_at=101.0,
        )

    assert not attest.path.exists()


def test_empty_constitutional_runtime_cannot_be_attested(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, passphrase = _anchor(tmp_path / "anchor")
    attest = ConstitutionalAttestationStore(runtime_root, anchor)

    with pytest.raises(ConstitutionalAttestationSigningError):
        attest.bind_current_heads(
            passphrase=passphrase,
            attested_at=100.0,
        )


def test_attestation_is_bound_to_exact_anchor_identity(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    anchor_a, passphrase = _anchor(
        tmp_path / "anchor-a",
        label="Anchor A",
    )
    _seed_state(runtime_root)
    attest_a = ConstitutionalAttestationStore(
        runtime_root,
        anchor_a,
    )
    attest_a.bind_current_heads(
        passphrase=passphrase,
        attested_at=101.0,
    )

    anchor_b, _ = _anchor(
        tmp_path / "anchor-b",
        label="Anchor B",
    )
    attest_b = ConstitutionalAttestationStore(
        runtime_root,
        anchor_b,
    )
    result = attest_b.verify()

    assert result.valid is False
    assert result.current is False
    assert "anchor_id does not match" in result.reason.lower()


def test_signature_tamper_with_recomputed_plain_hash_still_fails_crypto(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, passphrase = _anchor(tmp_path / "anchor")
    _seed_state(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)
    attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=101.0,
    )

    rows = attest.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(rows[0])
    signature = record["signature"]
    record["signature"] = (
        ("A" if signature[0] != "A" else "B")
        + signature[1:]
    )
    record["attestation_hash"] = _attestation_hash(record)
    attest.path.write_text(
        json.dumps(record, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = attest.verify()

    assert result.valid is False
    assert result.current is False
    assert "signature verification failed" in result.reason.lower()


def test_invalid_existing_attestation_chain_cannot_be_extended(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, passphrase = _anchor(tmp_path / "anchor")
    state_store, _ = _seed_state(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)
    attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=101.0,
    )

    rows = attest.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(rows[0])
    record["signature"] = "AAAA" + record["signature"][4:]
    record["attestation_hash"] = _attestation_hash(record)
    attest.path.write_text(
        json.dumps(record, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    state_store.save(
        PersistedConstitutionalState.genesis(),
        written_at=102.0,
        now=102.0,
    )

    with pytest.raises(ConstitutionalAttestationSigningError):
        attest.bind_current_heads(
            passphrase=passphrase,
            attested_at=103.0,
        )


def test_attestation_chain_link_tamper_is_detected(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    anchor, passphrase = _anchor(tmp_path / "anchor")
    state_store, _ = _seed_state(runtime_root)
    attest = ConstitutionalAttestationStore(runtime_root, anchor)
    attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=101.0,
    )
    state_store.save(
        PersistedConstitutionalState.genesis(),
        written_at=102.0,
        now=102.0,
    )
    attest.bind_current_heads(
        passphrase=passphrase,
        attested_at=103.0,
    )

    rows = attest.path.read_text(encoding="utf-8").splitlines()
    second = json.loads(rows[1])
    second["previous_attestation_hash"] = "0" * 64
    second["attestation_hash"] = _attestation_hash(second)
    rows[1] = json.dumps(second, sort_keys=True)
    attest.path.write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )

    result = attest.verify()

    assert result.valid is False
    assert "chain broken" in result.reason.lower()
