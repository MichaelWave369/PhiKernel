from __future__ import annotations

"""Runtime control and recovery substrate.

Recovery actions are explicit and conservative:
- approve is not a recovery shortcut
- release_quarantine and recover_from_seal are distinct flows
- seal remains a stronger boundary that requires explicit recovery action
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


CONTROL_DIRNAME = "control"
STATE_FILENAME = "runtime_control_state.json"
ACTIONS_FILENAME = "runtime_control_actions.jsonl"
RECOVERY_ACTIONS_FILENAME = "runtime_recovery_actions.jsonl"


RECOVERY_ACTIONS = {
    "clear_review",
    "release_quarantine",
    "recover_from_seal",
    "begin_recovery",
}


@dataclass(frozen=True)
class RuntimeControlState:
    review_required: bool = False
    quarantined: bool = False
    sealed: bool = False
    last_operator_action: str | None = None
    last_operator_note: str | None = None
    last_action_timestamp: str | None = None
    action_history_count: int = 0
    trust_posture: str | None = "healthy"
    next_step: str | None = "proceed"
    recovery_required: bool = False
    recovery_state: str | None = "none"
    last_recovery_action: str | None = None
    last_recovery_note: str | None = None
    last_recovery_timestamp: str | None = None
    recovery_history_count: int = 0
    recovery_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> "RuntimeControlState":
        return cls(
            review_required=bool(payload.get("review_required", False)),
            quarantined=bool(payload.get("quarantined", False)),
            sealed=bool(payload.get("sealed", False)),
            last_operator_action=payload.get("last_operator_action"),
            last_operator_note=payload.get("last_operator_note"),
            last_action_timestamp=payload.get("last_action_timestamp"),
            action_history_count=int(payload.get("action_history_count", 0)),
            trust_posture=payload.get("trust_posture"),
            next_step=payload.get("next_step"),
            recovery_required=bool(payload.get("recovery_required", False)),
            recovery_state=payload.get("recovery_state", "none"),
            last_recovery_action=payload.get("last_recovery_action"),
            last_recovery_note=payload.get("last_recovery_note"),
            last_recovery_timestamp=payload.get("last_recovery_timestamp"),
            recovery_history_count=int(payload.get("recovery_history_count", 0)),
            recovery_message=payload.get("recovery_message"),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RuntimeControlActionRecord:
    action: str
    accepted: bool
    operator_note: str | None
    timestamp: str
    prior_state: dict[str, Any]
    resulting_state: dict[str, Any]
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> "RuntimeControlActionRecord":
        return cls(
            action=str(payload.get("action", "refresh")),
            accepted=bool(payload.get("accepted", False)),
            operator_note=payload.get("operator_note"),
            timestamp=str(payload.get("timestamp", "")),
            prior_state=dict(payload.get("prior_state") or {}),
            resulting_state=dict(payload.get("resulting_state") or {}),
            message=str(payload.get("message", "")),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RuntimeRecoveryActionRecord:
    action: str
    accepted: bool
    operator_note: str | None
    timestamp: str
    prior_state: dict[str, Any]
    resulting_state: dict[str, Any]
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, payload: dict[str, Any]) -> "RuntimeRecoveryActionRecord":
        return cls(
            action=str(payload.get("action", "refresh")),
            accepted=bool(payload.get("accepted", False)),
            operator_note=payload.get("operator_note"),
            timestamp=str(payload.get("timestamp", "")),
            prior_state=dict(payload.get("prior_state") or {}),
            resulting_state=dict(payload.get("resulting_state") or {}),
            message=str(payload.get("message", "")),
            metadata=dict(payload.get("metadata") or {}),
        )


def load_runtime_control_state(runtime_root: str | Path) -> RuntimeControlState:
    state_file = _state_path(runtime_root)
    if not state_file.exists():
        return RuntimeControlState()

    with state_file.open("r", encoding="utf-8") as fh:
        return RuntimeControlState.from_record(json.load(fh))


def save_runtime_control_state(runtime_root: str | Path, state: RuntimeControlState) -> RuntimeControlState:
    state_file = _state_path(runtime_root)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with state_file.open("w", encoding="utf-8") as fh:
        json.dump(state.to_record(), fh, indent=2, sort_keys=True)
    return state


def append_runtime_control_action(runtime_root: str | Path, record: RuntimeControlActionRecord) -> RuntimeControlActionRecord:
    actions_file = _actions_path(runtime_root)
    actions_file.parent.mkdir(parents=True, exist_ok=True)
    with actions_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_record(), sort_keys=True) + "\n")
    return record


def append_runtime_recovery_action(runtime_root: str | Path, record: RuntimeRecoveryActionRecord) -> RuntimeRecoveryActionRecord:
    actions_file = _recovery_actions_path(runtime_root)
    actions_file.parent.mkdir(parents=True, exist_ok=True)
    with actions_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_record(), sort_keys=True) + "\n")
    return record


def list_recent_runtime_control_actions(
    runtime_root: str | Path,
    *,
    limit: int = 20,
) -> list[RuntimeControlActionRecord]:
    actions_file = _actions_path(runtime_root)
    if not actions_file.exists() or limit <= 0:
        return []

    records: list[RuntimeControlActionRecord] = []
    with actions_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            row = line.strip()
            if not row:
                continue
            records.append(RuntimeControlActionRecord.from_record(json.loads(row)))

    return records[-limit:]


def list_recent_runtime_recovery_actions(
    runtime_root: str | Path,
    *,
    limit: int = 20,
) -> list[RuntimeRecoveryActionRecord]:
    actions_file = _recovery_actions_path(runtime_root)
    if not actions_file.exists() or limit <= 0:
        return []

    records: list[RuntimeRecoveryActionRecord] = []
    with actions_file.open("r", encoding="utf-8") as fh:
        for line in fh:
            row = line.strip()
            if not row:
                continue
            records.append(RuntimeRecoveryActionRecord.from_record(json.loads(row)))

    return records[-limit:]


def refresh_runtime_control_state(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(
        runtime_root,
        action="refresh",
        operator_note=operator_note,
        metadata=metadata,
    )


def refresh_recovery_state(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(
        runtime_root,
        action="refresh",
        operator_note=operator_note,
        metadata=metadata,
    )


def approve_runtime(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(
        runtime_root,
        action="approve",
        operator_note=operator_note,
        metadata=metadata,
    )


def require_review(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(
        runtime_root,
        action="review",
        operator_note=operator_note,
        metadata=metadata,
    )


def quarantine_runtime(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(
        runtime_root,
        action="quarantine",
        operator_note=operator_note,
        metadata=metadata,
    )


def seal_runtime(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(
        runtime_root,
        action="seal",
        operator_note=operator_note,
        metadata=metadata,
    )


def clear_review(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(runtime_root, action="clear_review", operator_note=operator_note, metadata=metadata)


def release_quarantine(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(runtime_root, action="release_quarantine", operator_note=operator_note, metadata=metadata)


def recover_from_seal(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(runtime_root, action="recover_from_seal", operator_note=operator_note, metadata=metadata)


def begin_recovery(
    runtime_root: str | Path,
    *,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return apply_operator_action(runtime_root, action="begin_recovery", operator_note=operator_note, metadata=metadata)


def apply_operator_action(
    runtime_root: str | Path,
    *,
    action: str,
    operator_note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_action = action.strip().lower()
    now = _utc_now()
    prior_state = load_runtime_control_state(runtime_root)

    accepted, message, staged = _apply_transition(
        action=normalized_action,
        prior_state=prior_state,
        operator_note=operator_note,
    )

    trust_posture, next_step = _derive_posture_and_next_step(
        review_required=staged["review_required"],
        quarantined=staged["quarantined"],
        sealed=staged["sealed"],
        recovery_state=staged["recovery_state"],
    )
    applied_metadata = dict(prior_state.metadata)
    if metadata:
        applied_metadata.update(metadata)

    recovery_action = normalized_action in RECOVERY_ACTIONS
    recovery_history_count = prior_state.recovery_history_count + (1 if recovery_action else 0)
    resulting_state = RuntimeControlState(
        review_required=staged["review_required"],
        quarantined=staged["quarantined"],
        sealed=staged["sealed"],
        last_operator_action=normalized_action,
        last_operator_note=operator_note,
        last_action_timestamp=now,
        action_history_count=prior_state.action_history_count + 1,
        trust_posture=trust_posture,
        next_step=next_step,
        recovery_required=staged["recovery_required"],
        recovery_state=staged["recovery_state"],
        last_recovery_action=normalized_action if recovery_action else prior_state.last_recovery_action,
        last_recovery_note=operator_note if recovery_action else prior_state.last_recovery_note,
        last_recovery_timestamp=now if recovery_action else prior_state.last_recovery_timestamp,
        recovery_history_count=recovery_history_count,
        recovery_message=staged["recovery_message"],
        metadata=applied_metadata,
    )
    save_runtime_control_state(runtime_root, resulting_state)

    control_record = RuntimeControlActionRecord(
        action=normalized_action,
        accepted=accepted,
        operator_note=operator_note,
        timestamp=now,
        prior_state=prior_state.to_record(),
        resulting_state=resulting_state.to_record(),
        message=message,
        metadata=dict(metadata or {}),
    )
    append_runtime_control_action(runtime_root, control_record)

    if recovery_action:
        recovery_record = RuntimeRecoveryActionRecord(
            action=normalized_action,
            accepted=accepted,
            operator_note=operator_note,
            timestamp=now,
            prior_state=prior_state.to_record(),
            resulting_state=resulting_state.to_record(),
            message=message,
            metadata=dict(metadata or {}),
        )
        append_runtime_recovery_action(runtime_root, recovery_record)

    return {
        "action": normalized_action,
        "accepted": accepted,
        "operator_message": message,
        "trust_posture": trust_posture,
        "next_step": next_step,
        "weakest_face": "operator_control",
        "topology_flags": [
            "runtime_control",
            normalized_action,
            "sealed" if staged["sealed"] else "unsealed",
            "quarantined" if staged["quarantined"] else "clear",
            "review_required" if staged["review_required"] else "review_clear",
            f"recovery:{staged['recovery_state']}",
        ],
        "incident_summary": message if staged["sealed"] or staged["quarantined"] else None,
        "runtime_control_state": resulting_state.to_record(),
        "recovery_state": resulting_state.recovery_state,
        "recovery_message": resulting_state.recovery_message,
        "metadata": {
            "action_recorded": True,
            "action_history_count": resulting_state.action_history_count,
            "recovery_history_count": resulting_state.recovery_history_count,
            **dict(metadata or {}),
        },
    }


def _apply_transition(
    *,
    action: str,
    prior_state: RuntimeControlState,
    operator_note: str | None,
) -> tuple[bool, str, dict[str, Any]]:
    accepted = True
    message = ""
    staged: dict[str, Any] = {
        "review_required": prior_state.review_required,
        "quarantined": prior_state.quarantined,
        "sealed": prior_state.sealed,
        "recovery_required": prior_state.recovery_required,
        "recovery_state": prior_state.recovery_state or "none",
        "recovery_message": prior_state.recovery_message,
    }

    if action == "refresh":
        message = "Runtime control and recovery state refreshed from local persistence."
    elif action == "review":
        staged["review_required"] = True
        staged["recovery_state"] = "review_pending"
        staged["recovery_required"] = False
        staged["recovery_message"] = "Review is required before normal confidence is restored."
        message = "Runtime marked for operator review."
    elif action == "quarantine":
        staged["quarantined"] = True
        staged["review_required"] = True
        staged["recovery_required"] = True
        staged["recovery_state"] = "quarantine_pending"
        staged["recovery_message"] = "Runtime quarantined pending explicit release."
        message = "Runtime quarantined; execution and commits are restricted."
    elif action == "seal":
        staged["sealed"] = True
        staged["quarantined"] = True
        staged["review_required"] = True
        staged["recovery_required"] = True
        staged["recovery_state"] = "seal_recovery_required"
        staged["recovery_message"] = "Seal boundary active; explicit recover_from_seal is required."
        message = "Runtime sealed; recovery boundary enforced."
    elif action == "approve":
        # Approve remains narrow: not a blanket recovery action.
        if prior_state.sealed or prior_state.quarantined:
            accepted = False
            message = "Approve did not clear sealed/quarantined state. Use explicit recovery actions."
        elif prior_state.review_required:
            staged["review_required"] = False
            staged["recovery_state"] = "recovered"
            staged["recovery_required"] = False
            staged["recovery_message"] = "Review cleared via operator approval."
            message = "Review requirement cleared by operator approval."
        else:
            message = "Runtime already approved; no review requirement present."
    elif action == "clear_review":
        if prior_state.sealed or prior_state.quarantined:
            accepted = False
            staged["recovery_required"] = True
            staged["recovery_state"] = "recovery_blocked"
            staged["recovery_message"] = "Cannot clear review while sealed/quarantined state is active."
            message = "clear_review rejected: stronger state is active."
        elif not prior_state.review_required:
            accepted = False
            staged["recovery_state"] = "recovery_blocked"
            staged["recovery_message"] = "clear_review requested but review_required is already false."
            message = "clear_review rejected: no review requirement present."
        else:
            staged["review_required"] = False
            staged["recovery_required"] = False
            staged["recovery_state"] = "recovered"
            staged["recovery_message"] = "Review requirement cleared through explicit recovery action."
            message = "Review requirement cleared."
    elif action == "release_quarantine":
        if prior_state.sealed:
            accepted = False
            staged["recovery_required"] = True
            staged["recovery_state"] = "recovery_blocked"
            staged["recovery_message"] = "Cannot release quarantine while sealed. Use recover_from_seal first."
            message = "release_quarantine rejected: runtime is sealed."
        elif not prior_state.quarantined:
            accepted = False
            staged["recovery_state"] = "recovery_blocked"
            staged["recovery_message"] = "release_quarantine requested but runtime is not quarantined."
            message = "release_quarantine rejected: runtime is not quarantined."
        else:
            staged["quarantined"] = False
            staged["recovery_required"] = False
            if prior_state.review_required:
                staged["review_required"] = True
                staged["recovery_state"] = "review_pending"
                staged["recovery_message"] = "Quarantine released; review requirement remains active."
            else:
                staged["recovery_state"] = "recovered"
                staged["recovery_message"] = "Quarantine released; runtime moved to recovered posture."
            message = "Quarantine released with conservative review posture."
    elif action == "begin_recovery":
        if not (prior_state.sealed or prior_state.quarantined or prior_state.review_required):
            accepted = False
            staged["recovery_state"] = "recovery_blocked"
            staged["recovery_message"] = "No active control boundary requires recovery."
            message = "begin_recovery rejected: runtime is already in normal posture."
        else:
            staged["recovery_required"] = True
            staged["recovery_state"] = "recovery_in_progress"
            staged["recovery_message"] = "Recovery workflow is in progress; strong boundaries remain enforced."
            message = "Recovery workflow marked in progress."
    elif action == "recover_from_seal":
        if not prior_state.sealed:
            accepted = False
            staged["recovery_state"] = "recovery_blocked"
            staged["recovery_message"] = "recover_from_seal requested but runtime is not sealed."
            message = "recover_from_seal rejected: runtime is not sealed."
        elif not operator_note:
            accepted = False
            staged["recovery_required"] = True
            staged["recovery_state"] = "recovery_blocked"
            staged["recovery_message"] = "recover_from_seal requires explicit operator note for auditability."
            message = "recover_from_seal rejected: operator note is required."
        else:
            # Explicitly move from seal boundary into quarantine/review recovery path.
            staged["sealed"] = False
            staged["quarantined"] = True
            staged["review_required"] = True
            staged["recovery_required"] = True
            staged["recovery_state"] = "recovery_in_progress"
            staged["recovery_message"] = "Seal lifted to quarantine/review recovery path; release_quarantine is still required."
            message = "Seal recovery initiated; runtime remains restricted under quarantine/review."
    else:
        raise ValueError(f"Unsupported runtime control action '{action}'")

    return accepted, message, staged


def _derive_posture_and_next_step(
    *,
    review_required: bool,
    quarantined: bool,
    sealed: bool,
    recovery_state: str | None,
) -> tuple[str, str]:
    if sealed:
        return "critical", "recover_from_seal"
    if quarantined:
        return "critical", "release_quarantine"
    if recovery_state == "recovery_in_progress":
        return "degraded", "complete_recovery_review"
    if review_required:
        return "degraded", "clear_review"
    return "healthy", "proceed"


def _control_root(runtime_root: str | Path) -> Path:
    return Path(runtime_root) / CONTROL_DIRNAME


def _state_path(runtime_root: str | Path) -> Path:
    return _control_root(runtime_root) / STATE_FILENAME


def _actions_path(runtime_root: str | Path) -> Path:
    return _control_root(runtime_root) / ACTIONS_FILENAME


def _recovery_actions_path(runtime_root: str | Path) -> Path:
    return _control_root(runtime_root) / RECOVERY_ACTIONS_FILENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
