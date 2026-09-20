from __future__ import annotations

"""Persistent constitutional state for PhiKernel v0.2.

The store persists promotion/control state without manufacturing authority.

Persistence model
-----------------
- one canonical snapshot: constitutional/state.json
- append-only snapshot history: constitutional/history.jsonl
- deterministic SHA-256 hash chaining between snapshots
- atomic replacement of the canonical snapshot
- structural reconstruction of typed PhiKernel objects on load
- fail-closed validation of promotion, receipt, grant, warrant, contract, and
  control-session lineage

The SHA-256 chain is an integrity mechanism, not a human-authentication
mechanism. Cryptographic signing by the Anchor identity is future hardening.

Missing state means genesis SHADOW. Corrupt/tampered state does not silently
fall back to genesis.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import time
import uuid

from phikernel.control_witness import (
    ActionUsage,
    BoundedControlGrant,
    ControlActionRule,
    ControlContract,
    ControlPromotionReceipt,
)
from phikernel.warrant import ResourceBudget, Warrant
from phikernel.witness_bench import (
    ADVISE,
    BOUNDED_CONTROL,
    SHADOW,
    PromotionAuthorizationReceipt,
    PromotionCollapseReceipt,
    PromotionState,
)


PERSISTENCE_VERSION = "0.2.0"


class ConstitutionalPersistenceError(Exception):
    """Base exception for persistent constitutional-state failures."""


class ConstitutionalPersistenceIntegrityError(ConstitutionalPersistenceError):
    """Raised when persisted bytes fail deterministic integrity checks."""


class ConstitutionalPersistenceLineageError(ConstitutionalPersistenceError):
    """Raised when reconstructed authority lineage is inconsistent."""


class ConstitutionalPersistenceExpiredError(ConstitutionalPersistenceError):
    """Raised when persisted bounded authority has expired."""


@dataclass(frozen=True)
class PersistedConstitutionalState:
    promotion_state: PromotionState
    advise_receipt: PromotionAuthorizationReceipt | None = None
    collapse_receipt: PromotionCollapseReceipt | None = None
    control_contract: ControlContract | None = None
    control_grant: BoundedControlGrant | None = None
    control_receipt: ControlPromotionReceipt | None = None
    control_session: Any | None = None

    def __post_init__(self) -> None:
        # Import lazily only for runtime type checking to avoid a circular hint.
        from phikernel.control_witness import ControlSession

        if self.control_session is not None and not isinstance(
            self.control_session, ControlSession
        ):
            raise ConstitutionalPersistenceLineageError(
                "control_session must be a ControlSession"
            )

    @classmethod
    def genesis(cls) -> "PersistedConstitutionalState":
        return cls(promotion_state=PromotionState.genesis())


@dataclass(frozen=True)
class ConstitutionalSnapshot:
    snapshot_id: str
    written_at: float
    previous_snapshot_hash: str | None
    state: PersistedConstitutionalState
    snapshot_hash: str
    version: str = PERSISTENCE_VERSION

    def to_record(self) -> dict[str, Any]:
        payload = _snapshot_payload(
            snapshot_id=self.snapshot_id,
            written_at=self.written_at,
            state=self.state,
        )
        return {
            "version": self.version,
            "previous_snapshot_hash": self.previous_snapshot_hash,
            "payload": payload,
            "snapshot_hash": self.snapshot_hash,
        }


class ConstitutionalStateStore:
    """Filesystem-backed constitutional state with hash-chained history."""

    def __init__(self, runtime_root: str | Path) -> None:
        self.runtime_root = Path(runtime_root)
        self.root = self.runtime_root / "constitutional"
        self.state_file = self.root / "state.json"
        self.history_file = self.root / "history.jsonl"

    def exists(self) -> bool:
        return self.state_file.exists()

    def load(
        self,
        *,
        now: float | None = None,
    ) -> PersistedConstitutionalState:
        """Load and validate current state.

        Missing state returns genesis SHADOW.
        Existing corrupt state raises instead of silently resetting.
        """
        if not self.state_file.exists():
            return PersistedConstitutionalState.genesis()

        snapshot = self.load_snapshot(now=now)
        return snapshot.state

    def load_snapshot(
        self,
        *,
        now: float | None = None,
    ) -> ConstitutionalSnapshot:
        if not self.state_file.exists():
            raise ConstitutionalPersistenceError(
                "no constitutional state snapshot exists"
            )

        record = _read_json_object(self.state_file)
        snapshot = _snapshot_from_record(record, now=now)

        history_records = self._read_history_records()
        if not history_records:
            raise ConstitutionalPersistenceIntegrityError(
                "canonical snapshot exists but constitutional history is missing"
            )

        last = history_records[-1]
        history_snapshot = _snapshot_from_record(last, now=now)
        if history_snapshot.snapshot_hash != snapshot.snapshot_hash:
            raise ConstitutionalPersistenceIntegrityError(
                "canonical snapshot does not match latest history entry"
            )

        _verify_history_chain(history_records)
        return snapshot

    def save(
        self,
        state: PersistedConstitutionalState,
        *,
        written_at: float | None = None,
        now: float | None = None,
    ) -> ConstitutionalSnapshot:
        timestamp = time.time() if written_at is None else float(written_at)
        validation_time = timestamp if now is None else float(now)
        _validate_state(state, now=validation_time)

        prior: ConstitutionalSnapshot | None = None
        if self.state_file.exists():
            prior = self.load_snapshot(now=validation_time)
            _validate_successor(prior.state, state)

        snapshot_id = str(uuid.uuid4())
        previous_hash = None if prior is None else prior.snapshot_hash
        payload = _snapshot_payload(
            snapshot_id=snapshot_id,
            written_at=timestamp,
            state=state,
        )
        digest = _snapshot_digest(
            version=PERSISTENCE_VERSION,
            previous_snapshot_hash=previous_hash,
            payload=payload,
        )
        snapshot = ConstitutionalSnapshot(
            snapshot_id=snapshot_id,
            written_at=timestamp,
            previous_snapshot_hash=previous_hash,
            state=state,
            snapshot_hash=digest,
        )

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

        record = snapshot.to_record()
        serialized = _canonical_json(record)

        # History is append-first. If a crash occurs before state replacement,
        # the old state remains authoritative and the orphan history tail will
        # be detected on the next save/load rather than silently accepted.
        with self.history_file.open("a", encoding="utf-8") as fh:
            fh.write(serialized + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(self.history_file, 0o600)
        except OSError:
            pass

        tmp = self.state_file.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(serialized + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.state_file)
        return snapshot

    def history(self) -> tuple[ConstitutionalSnapshot, ...]:
        records = self._read_history_records()
        _verify_history_chain(records)
        return tuple(_snapshot_from_record(record, check_expiry=False) for record in records)

    def _read_history_records(self) -> list[dict[str, Any]]:
        if not self.history_file.exists():
            return []

        rows: list[dict[str, Any]] = []
        with self.history_file.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ConstitutionalPersistenceIntegrityError(
                        f"constitutional history line {line_no} is invalid JSON"
                    ) from exc
                if not isinstance(value, dict):
                    raise ConstitutionalPersistenceIntegrityError(
                        f"constitutional history line {line_no} is not an object"
                    )
                rows.append(value)
        return rows


def _validate_successor(
    prior: PersistedConstitutionalState,
    current: PersistedConstitutionalState,
) -> None:
    before = prior.promotion_state
    after = current.promotion_state

    if after.revision < before.revision:
        raise ConstitutionalPersistenceLineageError(
            "promotion revision may not move backward"
        )
    if after.revision > before.revision + 1:
        raise ConstitutionalPersistenceLineageError(
            "promotion revision may advance by at most one per persisted transition"
        )

    if after.revision == before.revision + 1:
        allowed = {
            (SHADOW, ADVISE),
            (ADVISE, BOUNDED_CONTROL),
            (ADVISE, SHADOW),
            (BOUNDED_CONTROL, SHADOW),
        }
        if (before.mode, after.mode) not in allowed:
            raise ConstitutionalPersistenceLineageError(
                f"illegal promotion transition {before.mode} -> {after.mode}"
            )

    if after.revision == before.revision:
        if after.mode != before.mode:
            raise ConstitutionalPersistenceLineageError(
                "promotion mode may not change without revision advance"
            )
        if after.authorized_by_seal_id != before.authorized_by_seal_id:
            raise ConstitutionalPersistenceLineageError(
                "same-revision state may not replace human authorization lineage"
            )
        if after.last_receipt_id != before.last_receipt_id:
            raise ConstitutionalPersistenceLineageError(
                "same-revision state may not replace promotion receipt lineage"
            )

        if after.mode == BOUNDED_CONTROL:
            if prior.control_grant is None or current.control_grant is None:
                raise ConstitutionalPersistenceLineageError(
                    "same-revision bounded update requires control grants"
                )
            if prior.control_grant.grant_id != current.control_grant.grant_id:
                raise ConstitutionalPersistenceLineageError(
                    "same-revision bounded update may not swap control grant"
                )
            if prior.control_session is None or current.control_session is None:
                raise ConstitutionalPersistenceLineageError(
                    "same-revision bounded update requires control sessions"
                )
            if prior.control_session.session_id != current.control_session.session_id:
                raise ConstitutionalPersistenceLineageError(
                    "same-revision bounded update may not swap control session"
                )
            if current.control_session.actions_used < prior.control_session.actions_used:
                raise ConstitutionalPersistenceLineageError(
                    "control session action count may not move backward"
                )
            if (
                current.control_session.clock_ticks_used
                < prior.control_session.clock_ticks_used
            ):
                raise ConstitutionalPersistenceLineageError(
                    "control session clock usage may not move backward"
                )
            _validate_budget_monotonic(
                prior.control_session.warrant,
                current.control_session.warrant,
            )


def _validate_state(
    state: PersistedConstitutionalState,
    *,
    now: float,
) -> None:
    mode = state.promotion_state.mode
    promotion = state.promotion_state

    if mode == SHADOW:
        if promotion.revision == 0:
            if promotion.last_receipt_id is not None:
                raise ConstitutionalPersistenceLineageError(
                    "genesis SHADOW may not carry a last receipt"
                )
            if promotion.authorized_by_seal_id is not None:
                raise ConstitutionalPersistenceLineageError(
                    "genesis SHADOW may not carry a human seal"
                )
            _require_no_active_control(state)
            if state.advise_receipt is not None or state.collapse_receipt is not None:
                raise ConstitutionalPersistenceLineageError(
                    "genesis SHADOW may not carry promotion/collapse receipts"
                )
            return

        collapse = state.collapse_receipt
        if collapse is None:
            raise ConstitutionalPersistenceLineageError(
                "non-genesis SHADOW requires collapse receipt"
            )
        if collapse.prior_mode not in {ADVISE, BOUNDED_CONTROL}:
            raise ConstitutionalPersistenceLineageError(
                "collapse receipt must begin from promoted mode"
            )
        if collapse.resulting_revision != collapse.prior_revision + 1:
            raise ConstitutionalPersistenceLineageError(
                "collapse receipt must advance revision exactly once"
            )
        if collapse.resulting_mode != SHADOW:
            raise ConstitutionalPersistenceLineageError(
                "collapse receipt must result in SHADOW"
            )
        if collapse.resulting_revision != promotion.revision:
            raise ConstitutionalPersistenceLineageError(
                "collapse receipt revision does not match SHADOW state"
            )
        if collapse.receipt_id != promotion.last_receipt_id:
            raise ConstitutionalPersistenceLineageError(
                "SHADOW last_receipt_id does not match collapse receipt"
            )
        if promotion.authorized_by_seal_id is not None:
            raise ConstitutionalPersistenceLineageError(
                "collapsed SHADOW may not retain authorization seal"
            )
        _require_no_active_control(state)
        return

    if mode == ADVISE:
        receipt = state.advise_receipt
        if receipt is None:
            raise ConstitutionalPersistenceLineageError(
                "ADVISE requires promotion authorization receipt"
            )
        _validate_advise_receipt(promotion, receipt)
        if state.collapse_receipt is not None:
            raise ConstitutionalPersistenceLineageError(
                "active ADVISE may not carry collapse receipt"
            )
        _require_no_active_control(state)
        return

    if mode != BOUNDED_CONTROL:
        raise ConstitutionalPersistenceLineageError(
            f"unsupported promotion mode '{mode}'"
        )

    if state.collapse_receipt is not None:
        raise ConstitutionalPersistenceLineageError(
            "active BOUNDED_CONTROL may not carry collapse receipt"
        )
    if state.advise_receipt is None:
        raise ConstitutionalPersistenceLineageError(
            "BOUNDED_CONTROL requires prior ADVISE authorization receipt"
        )
    if state.control_contract is None:
        raise ConstitutionalPersistenceLineageError(
            "BOUNDED_CONTROL requires control contract"
        )
    if state.control_grant is None:
        raise ConstitutionalPersistenceLineageError(
            "BOUNDED_CONTROL requires bounded-control grant"
        )
    if state.control_receipt is None:
        raise ConstitutionalPersistenceLineageError(
            "BOUNDED_CONTROL requires control promotion receipt"
        )
    if state.control_session is None:
        raise ConstitutionalPersistenceLineageError(
            "BOUNDED_CONTROL requires live control session"
        )

    _validate_bounded_lineage(
        promotion=promotion,
        advise_receipt=state.advise_receipt,
        contract=state.control_contract,
        grant=state.control_grant,
        control_receipt=state.control_receipt,
        session=state.control_session,
        now=now,
    )


def _require_no_active_control(state: PersistedConstitutionalState) -> None:
    if any(
        item is not None
        for item in (
            state.control_contract,
            state.control_grant,
            state.control_receipt,
            state.control_session,
        )
    ):
        raise ConstitutionalPersistenceLineageError(
            f"{state.promotion_state.mode} may not carry active bounded-control objects"
        )


def _validate_prior_advise_receipt(
    receipt: PromotionAuthorizationReceipt,
) -> None:
    if receipt.prior_mode != SHADOW:
        raise ConstitutionalPersistenceLineageError(
            "ADVISE authorization receipt must begin from SHADOW"
        )
    if receipt.prior_revision < 0:
        raise ConstitutionalPersistenceLineageError(
            "ADVISE authorization prior revision must be >= 0"
        )
    if receipt.resulting_revision != receipt.prior_revision + 1:
        raise ConstitutionalPersistenceLineageError(
            "ADVISE authorization receipt must advance revision exactly once"
        )
    if receipt.resulting_mode != ADVISE:
        raise ConstitutionalPersistenceLineageError(
            "promotion receipt does not result in ADVISE"
        )


def _validate_advise_receipt(
    promotion: PromotionState,
    receipt: PromotionAuthorizationReceipt,
) -> None:
    _validate_prior_advise_receipt(receipt)
    if receipt.resulting_revision != promotion.revision:
        raise ConstitutionalPersistenceLineageError(
            "ADVISE receipt revision does not match promotion state"
        )
    if receipt.receipt_id != promotion.last_receipt_id:
        raise ConstitutionalPersistenceLineageError(
            "ADVISE last_receipt_id does not match authorization receipt"
        )
    if receipt.human_seal_id != promotion.authorized_by_seal_id:
        raise ConstitutionalPersistenceLineageError(
            "ADVISE human seal lineage does not match authorization receipt"
        )


def _validate_bounded_lineage(
    *,
    promotion: PromotionState,
    advise_receipt: PromotionAuthorizationReceipt,
    contract: ControlContract,
    grant: BoundedControlGrant,
    control_receipt: ControlPromotionReceipt,
    session: Any,
    now: float,
) -> None:
    _validate_prior_advise_receipt(advise_receipt)
    if control_receipt.prior_mode != ADVISE:
        raise ConstitutionalPersistenceLineageError(
            "control promotion receipt must begin from ADVISE"
        )
    if control_receipt.prior_revision < 0:
        raise ConstitutionalPersistenceLineageError(
            "control promotion prior revision must be >= 0"
        )
    if control_receipt.resulting_revision != control_receipt.prior_revision + 1:
        raise ConstitutionalPersistenceLineageError(
            "control promotion receipt must advance revision exactly once"
        )
    if advise_receipt.resulting_revision != control_receipt.prior_revision:
        raise ConstitutionalPersistenceLineageError(
            "ADVISE receipt does not chain into control promotion revision"
        )
    if control_receipt.resulting_mode != BOUNDED_CONTROL:
        raise ConstitutionalPersistenceLineageError(
            "control receipt does not result in BOUNDED_CONTROL"
        )
    if control_receipt.resulting_revision != promotion.revision:
        raise ConstitutionalPersistenceLineageError(
            "control receipt revision does not match promotion state"
        )
    if control_receipt.receipt_id != promotion.last_receipt_id:
        raise ConstitutionalPersistenceLineageError(
            "BOUNDED_CONTROL last receipt does not match control receipt"
        )
    if control_receipt.human_seal_id != promotion.authorized_by_seal_id:
        raise ConstitutionalPersistenceLineageError(
            "promotion state seal does not match control receipt"
        )
    if grant.human_seal_id != promotion.authorized_by_seal_id:
        raise ConstitutionalPersistenceLineageError(
            "control grant human seal does not match promotion state"
        )
    if grant.promotion_proposal_id != control_receipt.proposal_id:
        raise ConstitutionalPersistenceLineageError(
            "control grant proposal id does not match control receipt"
        )
    if grant.witness_report_id != control_receipt.witness_report_id:
        raise ConstitutionalPersistenceLineageError(
            "control grant witness report id does not match control receipt"
        )
    if grant.witness_report_hash != control_receipt.witness_report_hash:
        raise ConstitutionalPersistenceLineageError(
            "control grant witness report hash does not match control receipt"
        )
    if grant.grant_id != control_receipt.grant_id:
        raise ConstitutionalPersistenceLineageError(
            "control receipt grant id does not match control grant"
        )
    if grant.warrant.warrant_id != control_receipt.warrant_id:
        raise ConstitutionalPersistenceLineageError(
            "control receipt warrant id does not match control grant"
        )
    if grant.contract_id != contract.contract_id:
        raise ConstitutionalPersistenceLineageError(
            "control grant references different contract"
        )
    if grant.contract_hash != contract.contract_hash:
        raise ConstitutionalPersistenceLineageError(
            "control grant contract hash mismatch"
        )
    if control_receipt.contract_id != contract.contract_id:
        raise ConstitutionalPersistenceLineageError(
            "control receipt references different contract"
        )
    if control_receipt.contract_hash != contract.contract_hash:
        raise ConstitutionalPersistenceLineageError(
            "control receipt contract hash mismatch"
        )
    if session.grant_id != grant.grant_id:
        raise ConstitutionalPersistenceLineageError(
            "control session grant id does not match control grant"
        )
    if session.contract_id != contract.contract_id:
        raise ConstitutionalPersistenceLineageError(
            "control session references different contract"
        )
    if session.contract_hash != contract.contract_hash:
        raise ConstitutionalPersistenceLineageError(
            "control session contract hash mismatch"
        )
    if session.actor_id != grant.actor_id or session.actor_id != contract.actor_id:
        raise ConstitutionalPersistenceLineageError(
            "control actor lineage mismatch"
        )
    if session.warrant.warrant_id != grant.warrant.warrant_id:
        raise ConstitutionalPersistenceLineageError(
            "control session warrant identity does not match grant"
        )
    if session.warrant.bearer != grant.warrant.bearer:
        raise ConstitutionalPersistenceLineageError(
            "control session warrant bearer does not match grant"
        )
    if tuple(session.warrant.scopes) != tuple(grant.warrant.scopes):
        raise ConstitutionalPersistenceLineageError(
            "control session warrant scopes do not match grant"
        )
    if tuple(grant.warrant.scopes) != contract.scopes:
        raise ConstitutionalPersistenceLineageError(
            "control grant warrant scopes do not match contract"
        )
    if grant.warrant.issuer != grant.human_actor_id:
        raise ConstitutionalPersistenceLineageError(
            "control grant warrant issuer does not match human actor"
        )
    expected_metadata = {
        "control_contract_id": contract.contract_id,
        "control_contract_hash": contract.contract_hash,
        "control_promotion_proposal_id": grant.promotion_proposal_id,
        "control_witness_report_hash": grant.witness_report_hash,
        "human_seal_id": grant.human_seal_id,
    }
    for key, expected in expected_metadata.items():
        if grant.warrant.metadata.get(key) != expected:
            raise ConstitutionalPersistenceLineageError(
                f"control grant warrant metadata mismatch for '{key}'"
            )
    if session.warrant.issuer != grant.warrant.issuer:
        raise ConstitutionalPersistenceLineageError(
            "control session warrant issuer changed from grant"
        )
    if session.warrant.issued_at != grant.warrant.issued_at:
        raise ConstitutionalPersistenceLineageError(
            "control session warrant issued_at changed from grant"
        )
    if session.warrant.metadata != grant.warrant.metadata:
        raise ConstitutionalPersistenceLineageError(
            "control session warrant metadata changed from grant"
        )
    if session.warrant.expires_at != grant.expires_at:
        raise ConstitutionalPersistenceLineageError(
            "control session warrant expiry does not match grant"
        )
    if session.started_at < grant.granted_at:
        raise ConstitutionalPersistenceLineageError(
            "control session may not predate control grant"
        )
    if session.stopped:
        raise ConstitutionalPersistenceLineageError(
            "active BOUNDED_CONTROL may not persist stopped session"
        )
    if grant.warrant.revoked or session.warrant.revoked:
        raise ConstitutionalPersistenceLineageError(
            "active BOUNDED_CONTROL may not persist revoked warrant"
        )
    if now >= grant.expires_at or session.warrant.is_expired(now=now):
        raise ConstitutionalPersistenceExpiredError(
            "persisted bounded-control grant has expired"
        )

    grant_limits = {budget.kind: budget.limit for budget in grant.warrant.budgets}
    contract_limits = {
        budget.kind: budget.limit for budget in contract.resource_limits
    }
    if grant_limits != contract_limits:
        raise ConstitutionalPersistenceLineageError(
            "control grant warrant budgets do not match contract"
        )
    if any(budget.spent != 0 for budget in grant.warrant.budgets):
        raise ConstitutionalPersistenceLineageError(
            "persisted control grant must retain original unspent warrant"
        )

    usage = {item.rule_id: item.count for item in session.action_usage}
    rule_limits = {rule.rule_id: rule.max_count for rule in contract.action_rules}
    if set(usage) != set(rule_limits):
        raise ConstitutionalPersistenceLineageError(
            "control session action usage does not match contract rules"
        )
    if any(usage[rule_id] > limit for rule_id, limit in rule_limits.items()):
        raise ConstitutionalPersistenceLineageError(
            "control session action usage exceeds contract rule limit"
        )
    if sum(usage.values()) != session.actions_used:
        raise ConstitutionalPersistenceLineageError(
            "control session action count does not match per-rule usage"
        )
    if session.actions_used > contract.max_total_actions:
        raise ConstitutionalPersistenceLineageError(
            "control session exceeds total action limit"
        )
    if session.clock_ticks_used > contract.max_clock_ticks:
        raise ConstitutionalPersistenceLineageError(
            "control session exceeds clock budget"
        )

    _validate_budget_lineage(grant.warrant, session.warrant)


def _validate_budget_lineage(grant: Warrant, session: Warrant) -> None:
    grant_budgets = {budget.kind: budget for budget in grant.budgets}
    session_budgets = {budget.kind: budget for budget in session.budgets}
    if set(grant_budgets) != set(session_budgets):
        raise ConstitutionalPersistenceLineageError(
            "control session warrant budget kinds do not match grant"
        )
    for kind, original in grant_budgets.items():
        current = session_budgets[kind]
        if current.limit != original.limit:
            raise ConstitutionalPersistenceLineageError(
                f"control session budget limit changed for '{kind}'"
            )
        if current.spent < original.spent:
            raise ConstitutionalPersistenceLineageError(
                f"control session budget spend moved backward for '{kind}'"
            )
        if current.spent > current.limit:
            raise ConstitutionalPersistenceLineageError(
                f"control session budget exceeds limit for '{kind}'"
            )


def _validate_budget_monotonic(before: Warrant, after: Warrant) -> None:
    before_map = {budget.kind: budget for budget in before.budgets}
    after_map = {budget.kind: budget for budget in after.budgets}
    if set(before_map) != set(after_map):
        raise ConstitutionalPersistenceLineageError(
            "same-session update changed warrant budget kinds"
        )
    for kind, prior in before_map.items():
        current = after_map[kind]
        if current.limit != prior.limit:
            raise ConstitutionalPersistenceLineageError(
                f"same-session update changed budget limit for '{kind}'"
            )
        if current.spent < prior.spent:
            raise ConstitutionalPersistenceLineageError(
                f"same-session update decreased spent budget for '{kind}'"
            )


def _snapshot_payload(
    *,
    snapshot_id: str,
    written_at: float,
    state: PersistedConstitutionalState,
) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "written_at": written_at,
        "promotion_state": _promotion_state_to_record(state.promotion_state),
        "advise_receipt": (
            None
            if state.advise_receipt is None
            else _advise_receipt_to_record(state.advise_receipt)
        ),
        "collapse_receipt": (
            None
            if state.collapse_receipt is None
            else _collapse_receipt_to_record(state.collapse_receipt)
        ),
        "control_contract": (
            None
            if state.control_contract is None
            else state.control_contract.to_record()
        ),
        "control_grant": (
            None
            if state.control_grant is None
            else _control_grant_to_record(state.control_grant)
        ),
        "control_receipt": (
            None
            if state.control_receipt is None
            else _control_receipt_to_record(state.control_receipt)
        ),
        "control_session": (
            None
            if state.control_session is None
            else _control_session_to_record(state.control_session)
        ),
    }


def _snapshot_from_record(
    record: dict[str, Any],
    *,
    now: float | None = None,
    check_expiry: bool = True,
) -> ConstitutionalSnapshot:
    if record.get("version") != PERSISTENCE_VERSION:
        raise ConstitutionalPersistenceIntegrityError(
            "unsupported constitutional persistence version"
        )
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ConstitutionalPersistenceIntegrityError(
            "constitutional snapshot payload is missing or invalid"
        )
    previous = record.get("previous_snapshot_hash")
    if previous is not None:
        _require_sha256("previous_snapshot_hash", previous)

    expected = _snapshot_digest(
        version=record["version"],
        previous_snapshot_hash=previous,
        payload=payload,
    )
    actual = record.get("snapshot_hash")
    if not isinstance(actual, str) or actual != expected:
        raise ConstitutionalPersistenceIntegrityError(
            "constitutional snapshot hash mismatch"
        )

    try:
        state = _state_from_payload(payload)
    except ConstitutionalPersistenceError:
        raise
    except Exception as exc:
        raise ConstitutionalPersistenceIntegrityError(
            "constitutional snapshot contains malformed typed state"
        ) from exc

    validation_time = (
        time.time() if now is None else float(now)
    )
    if check_expiry:
        _validate_state(state, now=validation_time)
    else:
        # Historical snapshots may contain authority that has since expired.
        _validate_state_without_expiry(state)

    snapshot_id = payload.get("snapshot_id")
    written_at = payload.get("written_at")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ConstitutionalPersistenceIntegrityError(
            "snapshot_id is missing"
        )
    if not isinstance(written_at, (int, float)):
        raise ConstitutionalPersistenceIntegrityError(
            "written_at is invalid"
        )
    return ConstitutionalSnapshot(
        snapshot_id=snapshot_id,
        written_at=float(written_at),
        previous_snapshot_hash=previous,
        state=state,
        snapshot_hash=actual,
    )


def _validate_state_without_expiry(state: PersistedConstitutionalState) -> None:
    if state.promotion_state.mode != BOUNDED_CONTROL:
        _validate_state(state, now=0.0)
        return
    assert state.control_grant is not None
    # Pick a time inside the grant interval for structural validation only.
    validation_time = state.control_grant.granted_at
    _validate_state(state, now=validation_time)


def _verify_history_chain(records: list[dict[str, Any]]) -> None:
    previous_hash: str | None = None
    for index, record in enumerate(records):
        snapshot = _snapshot_from_record(record, check_expiry=False)
        if snapshot.previous_snapshot_hash != previous_hash:
            raise ConstitutionalPersistenceIntegrityError(
                f"constitutional history chain broken at index {index}"
            )
        previous_hash = snapshot.snapshot_hash


def _state_from_payload(payload: dict[str, Any]) -> PersistedConstitutionalState:
    return PersistedConstitutionalState(
        promotion_state=_promotion_state_from_record(
            _require_object(payload, "promotion_state")
        ),
        advise_receipt=_optional(
            payload.get("advise_receipt"),
            _advise_receipt_from_record,
        ),
        collapse_receipt=_optional(
            payload.get("collapse_receipt"),
            _collapse_receipt_from_record,
        ),
        control_contract=_optional(
            payload.get("control_contract"),
            _control_contract_from_record,
        ),
        control_grant=_optional(
            payload.get("control_grant"),
            _control_grant_from_record,
        ),
        control_receipt=_optional(
            payload.get("control_receipt"),
            _control_receipt_from_record,
        ),
        control_session=_optional(
            payload.get("control_session"),
            _control_session_from_record,
        ),
    )


def _promotion_state_to_record(state: PromotionState) -> dict[str, Any]:
    return {
        "version": state.version,
        "mode": state.mode,
        "revision": state.revision,
        "last_receipt_id": state.last_receipt_id,
        "authorized_by_seal_id": state.authorized_by_seal_id,
    }


def _promotion_state_from_record(record: dict[str, Any]) -> PromotionState:
    return PromotionState(
        mode=str(record["mode"]),
        revision=int(record["revision"]),
        last_receipt_id=record.get("last_receipt_id"),
        authorized_by_seal_id=record.get("authorized_by_seal_id"),
        version=str(record.get("version", PERSISTENCE_VERSION)),
    )


def _advise_receipt_to_record(
    receipt: PromotionAuthorizationReceipt,
) -> dict[str, Any]:
    return {
        "version": receipt.version,
        "receipt_id": receipt.receipt_id,
        "proposal_id": receipt.proposal_id,
        "witness_report_id": receipt.witness_report_id,
        "witness_report_hash": receipt.witness_report_hash,
        "prior_mode": receipt.prior_mode,
        "resulting_mode": receipt.resulting_mode,
        "prior_revision": receipt.prior_revision,
        "resulting_revision": receipt.resulting_revision,
        "human_seal_id": receipt.human_seal_id,
        "human_actor_id": receipt.human_actor_id,
        "authority_ref": receipt.authority_ref,
        "applied_at": receipt.applied_at,
        "authority_change": receipt.authority_change,
        "routing_mode_change": receipt.routing_mode_change,
        "steering_authority_change": receipt.steering_authority_change,
        "constitutional_change": receipt.constitutional_change,
    }


def _advise_receipt_from_record(
    record: dict[str, Any],
) -> PromotionAuthorizationReceipt:
    return PromotionAuthorizationReceipt(
        receipt_id=str(record["receipt_id"]),
        proposal_id=str(record["proposal_id"]),
        witness_report_id=str(record["witness_report_id"]),
        witness_report_hash=str(record["witness_report_hash"]),
        prior_mode=str(record["prior_mode"]),
        resulting_mode=str(record["resulting_mode"]),
        prior_revision=int(record["prior_revision"]),
        resulting_revision=int(record["resulting_revision"]),
        human_seal_id=str(record["human_seal_id"]),
        human_actor_id=str(record["human_actor_id"]),
        authority_ref=str(record["authority_ref"]),
        applied_at=float(record["applied_at"]),
        authority_change=str(record.get("authority_change", "NONE")),
        routing_mode_change=str(record.get("routing_mode_change", "HUMAN_AUTHORIZED_APPLIED")),
        steering_authority_change=str(record.get("steering_authority_change", "NONE")),
        constitutional_change=str(record.get("constitutional_change", "NONE")),
        version=str(record.get("version", PERSISTENCE_VERSION)),
    )


def _collapse_receipt_to_record(
    receipt: PromotionCollapseReceipt,
) -> dict[str, Any]:
    return {
        "version": receipt.version,
        "receipt_id": receipt.receipt_id,
        "prior_mode": receipt.prior_mode,
        "resulting_mode": receipt.resulting_mode,
        "prior_revision": receipt.prior_revision,
        "resulting_revision": receipt.resulting_revision,
        "source_ref": receipt.source_ref,
        "reason": receipt.reason,
        "collapsed_at": receipt.collapsed_at,
        "authority_change": receipt.authority_change,
        "routing_mode_change": receipt.routing_mode_change,
        "steering_authority_change": receipt.steering_authority_change,
        "constitutional_change": receipt.constitutional_change,
    }


def _collapse_receipt_from_record(
    record: dict[str, Any],
) -> PromotionCollapseReceipt:
    return PromotionCollapseReceipt(
        receipt_id=str(record["receipt_id"]),
        prior_mode=str(record["prior_mode"]),
        resulting_mode=str(record["resulting_mode"]),
        prior_revision=int(record["prior_revision"]),
        resulting_revision=int(record["resulting_revision"]),
        source_ref=str(record["source_ref"]),
        reason=str(record["reason"]),
        collapsed_at=float(record["collapsed_at"]),
        authority_change=str(record.get("authority_change", "NONE")),
        routing_mode_change=str(record.get("routing_mode_change", "COLLAPSED_TO_SHADOW")),
        steering_authority_change=str(record.get("steering_authority_change", "NONE")),
        constitutional_change=str(record.get("constitutional_change", "NONE")),
        version=str(record.get("version", PERSISTENCE_VERSION)),
    )


def _control_contract_from_record(record: dict[str, Any]) -> ControlContract:
    rules = tuple(
        ControlActionRule(
            rule_id=str(row["rule_id"]),
            operation=str(row["operation"]),
            target=str(row["target"]),
            max_count=int(row["max_count"]),
            rollback_required=bool(row.get("rollback_required", True)),
        )
        for row in record.get("action_rules", [])
    )
    limits = tuple(
        ResourceBudget(
            kind=str(row["kind"]),
            limit=float(row["limit"]),
            spent=float(row.get("spent", 0.0)),
        )
        for row in record.get("resource_limits", [])
    )
    return ControlContract(
        contract_id=str(record["contract_id"]),
        actor_id=str(record["actor_id"]),
        action_rules=rules,
        resource_limits=limits,
        max_total_actions=int(record["max_total_actions"]),
        max_clock_ticks=float(record["max_clock_ticks"]),
        lifetime_seconds=float(record["lifetime_seconds"]),
        required_evidence_refs=tuple(record.get("required_evidence_refs", [])),
        rollback_required=bool(record.get("rollback_required", True)),
        interruptible=bool(record.get("interruptible", True)),
        human_veto_required=bool(record.get("human_veto_required", True)),
        post_action_receipt_required=bool(
            record.get("post_action_receipt_required", True)
        ),
        terminal_failure_on_violation=bool(
            record.get("terminal_failure_on_violation", True)
        ),
        terminal_failure_on_action_failure=bool(
            record.get("terminal_failure_on_action_failure", True)
        ),
        created_at=float(record["created_at"]),
        version=str(record.get("version", PERSISTENCE_VERSION)),
    )


def _control_grant_to_record(grant: BoundedControlGrant) -> dict[str, Any]:
    return {
        "version": grant.version,
        "grant_id": grant.grant_id,
        "contract_id": grant.contract_id,
        "contract_hash": grant.contract_hash,
        "promotion_proposal_id": grant.promotion_proposal_id,
        "witness_report_id": grant.witness_report_id,
        "witness_report_hash": grant.witness_report_hash,
        "actor_id": grant.actor_id,
        "warrant": grant.warrant.to_record(),
        "human_seal_id": grant.human_seal_id,
        "human_actor_id": grant.human_actor_id,
        "authority_ref": grant.authority_ref,
        "granted_at": grant.granted_at,
        "expires_at": grant.expires_at,
        "authority_change": grant.authority_change,
        "routing_mode_change": grant.routing_mode_change,
        "steering_authority_change": grant.steering_authority_change,
        "constitutional_change": grant.constitutional_change,
    }


def _control_grant_from_record(record: dict[str, Any]) -> BoundedControlGrant:
    return BoundedControlGrant(
        grant_id=str(record["grant_id"]),
        contract_id=str(record["contract_id"]),
        contract_hash=str(record["contract_hash"]),
        promotion_proposal_id=str(record["promotion_proposal_id"]),
        witness_report_id=str(record["witness_report_id"]),
        witness_report_hash=str(record["witness_report_hash"]),
        actor_id=str(record["actor_id"]),
        warrant=_warrant_from_record(_require_object(record, "warrant")),
        human_seal_id=str(record["human_seal_id"]),
        human_actor_id=str(record["human_actor_id"]),
        authority_ref=str(record["authority_ref"]),
        granted_at=float(record["granted_at"]),
        expires_at=float(record["expires_at"]),
        authority_change=str(record["authority_change"]),
        routing_mode_change=str(record["routing_mode_change"]),
        steering_authority_change=str(record["steering_authority_change"]),
        constitutional_change=str(record.get("constitutional_change", "NONE")),
        version=str(record.get("version", PERSISTENCE_VERSION)),
    )


def _control_receipt_to_record(
    receipt: ControlPromotionReceipt,
) -> dict[str, Any]:
    return {
        "version": receipt.version,
        "receipt_id": receipt.receipt_id,
        "proposal_id": receipt.proposal_id,
        "contract_id": receipt.contract_id,
        "contract_hash": receipt.contract_hash,
        "witness_report_id": receipt.witness_report_id,
        "witness_report_hash": receipt.witness_report_hash,
        "prior_mode": receipt.prior_mode,
        "resulting_mode": receipt.resulting_mode,
        "prior_revision": receipt.prior_revision,
        "resulting_revision": receipt.resulting_revision,
        "grant_id": receipt.grant_id,
        "warrant_id": receipt.warrant_id,
        "human_seal_id": receipt.human_seal_id,
        "human_actor_id": receipt.human_actor_id,
        "authority_ref": receipt.authority_ref,
        "applied_at": receipt.applied_at,
        "authority_change": receipt.authority_change,
        "routing_mode_change": receipt.routing_mode_change,
        "steering_authority_change": receipt.steering_authority_change,
        "constitutional_change": receipt.constitutional_change,
    }


def _control_receipt_from_record(
    record: dict[str, Any],
) -> ControlPromotionReceipt:
    return ControlPromotionReceipt(
        receipt_id=str(record["receipt_id"]),
        proposal_id=str(record["proposal_id"]),
        contract_id=str(record["contract_id"]),
        contract_hash=str(record["contract_hash"]),
        witness_report_id=str(record["witness_report_id"]),
        witness_report_hash=str(record["witness_report_hash"]),
        prior_mode=str(record["prior_mode"]),
        resulting_mode=str(record["resulting_mode"]),
        prior_revision=int(record["prior_revision"]),
        resulting_revision=int(record["resulting_revision"]),
        grant_id=str(record["grant_id"]),
        warrant_id=str(record["warrant_id"]),
        human_seal_id=str(record["human_seal_id"]),
        human_actor_id=str(record["human_actor_id"]),
        authority_ref=str(record["authority_ref"]),
        applied_at=float(record["applied_at"]),
        authority_change=str(record["authority_change"]),
        routing_mode_change=str(record["routing_mode_change"]),
        steering_authority_change=str(record["steering_authority_change"]),
        constitutional_change=str(record.get("constitutional_change", "NONE")),
        version=str(record.get("version", PERSISTENCE_VERSION)),
    )


def _control_session_to_record(session: Any) -> dict[str, Any]:
    return {
        "version": session.version,
        "session_id": session.session_id,
        "grant_id": session.grant_id,
        "contract_id": session.contract_id,
        "contract_hash": session.contract_hash,
        "actor_id": session.actor_id,
        "warrant": session.warrant.to_record(),
        "started_at": session.started_at,
        "actions_used": session.actions_used,
        "clock_ticks_used": session.clock_ticks_used,
        "action_usage": [
            {"rule_id": usage.rule_id, "count": usage.count}
            for usage in session.action_usage
        ],
        "pending_action_id": session.pending_action_id,
        "pending_rollback_ref": session.pending_rollback_ref,
        "stopped": session.stopped,
        "stop_reason": session.stop_reason,
        "last_receipt_id": session.last_receipt_id,
    }


def _control_session_from_record(record: dict[str, Any]) -> Any:
    from phikernel.control_witness import ControlSession

    return ControlSession(
        session_id=str(record["session_id"]),
        grant_id=str(record["grant_id"]),
        contract_id=str(record["contract_id"]),
        contract_hash=str(record["contract_hash"]),
        actor_id=str(record["actor_id"]),
        warrant=_warrant_from_record(_require_object(record, "warrant")),
        started_at=float(record["started_at"]),
        actions_used=int(record["actions_used"]),
        clock_ticks_used=float(record["clock_ticks_used"]),
        action_usage=tuple(
            ActionUsage(
                rule_id=str(row["rule_id"]),
                count=int(row["count"]),
            )
            for row in record.get("action_usage", [])
        ),
        pending_action_id=record.get("pending_action_id"),
        pending_rollback_ref=record.get("pending_rollback_ref"),
        stopped=bool(record.get("stopped", False)),
        stop_reason=record.get("stop_reason"),
        last_receipt_id=record.get("last_receipt_id"),
        version=str(record.get("version", PERSISTENCE_VERSION)),
    )


def _warrant_from_record(record: dict[str, Any]) -> Warrant:
    return Warrant(
        warrant_id=str(record["warrant_id"]),
        issuer=str(record["issuer"]),
        bearer=str(record["bearer"]),
        scopes=tuple(record.get("scopes", [])),
        budgets=tuple(
            ResourceBudget(
                kind=str(row["kind"]),
                limit=float(row["limit"]),
                spent=float(row.get("spent", 0.0)),
            )
            for row in record.get("budgets", [])
        ),
        issued_at=float(record["issued_at"]),
        expires_at=(
            None
            if record.get("expires_at") is None
            else float(record["expires_at"])
        ),
        revoked=bool(record.get("revoked", False)),
        revocation_reason=record.get("revocation_reason"),
        parent_warrant_id=record.get("parent_warrant_id"),
        metadata=dict(record.get("metadata", {})),
        version=str(record.get("version", PERSISTENCE_VERSION)),
    )


def _snapshot_digest(
    *,
    version: str,
    previous_snapshot_hash: str | None,
    payload: dict[str, Any],
) -> str:
    material = {
        "version": version,
        "previous_snapshot_hash": previous_snapshot_hash,
        "payload": payload,
    }
    return hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()


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
        raise ConstitutionalPersistenceIntegrityError(
            "constitutional state must be deterministically JSON-serializable"
        ) from exc


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            value = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConstitutionalPersistenceIntegrityError(
            f"constitutional state file is invalid JSON: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise ConstitutionalPersistenceIntegrityError(
            "constitutional state file must contain a JSON object"
        )
    return value


def _require_object(record: dict[str, Any], key: str) -> dict[str, Any]:
    value = record.get(key)
    if not isinstance(value, dict):
        raise ConstitutionalPersistenceIntegrityError(
            f"'{key}' must be a JSON object"
        )
    return value


def _optional(value: Any, loader):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConstitutionalPersistenceIntegrityError(
            "optional constitutional record must be an object or null"
        )
    return loader(value)


def _require_sha256(name: str, value: str) -> None:
    if len(value) != 64:
        raise ConstitutionalPersistenceIntegrityError(
            f"{name} must be SHA-256 hex"
        )
    try:
        int(value, 16)
    except ValueError as exc:
        raise ConstitutionalPersistenceIntegrityError(
            f"{name} must be hexadecimal"
        ) from exc
