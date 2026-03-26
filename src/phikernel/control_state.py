from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


CONTROL_DIRNAME = "control"
STATE_FILENAME = "runtime_control_state.json"
ACTIONS_FILENAME = "runtime_control_actions.jsonl"


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

    accepted = True
    message = ""
    review_required = prior_state.review_required
    quarantined = prior_state.quarantined
    sealed = prior_state.sealed

    if normalized_action == "refresh":
        message = "Runtime control state refreshed from local persistence."
    elif normalized_action == "review":
        review_required = True
        message = "Runtime marked for operator review."
    elif normalized_action == "quarantine":
        quarantined = True
        review_required = True
        message = "Runtime quarantined; execution and commits are restricted."
    elif normalized_action == "seal":
        sealed = True
        quarantined = True
        review_required = True
        message = "Runtime sealed; recovery boundary enforced."
    elif normalized_action == "approve":
        if prior_state.sealed or prior_state.quarantined:
            accepted = False
            message = "Approve did not clear sealed/quarantined state. Explicit recovery flow required."
        elif prior_state.review_required:
            review_required = False
            message = "Review requirement cleared by operator approval."
        else:
            message = "Runtime already approved; no review requirement present."
    else:
        raise ValueError(f"Unsupported runtime control action '{action}'")

    trust_posture, next_step = _derive_posture_and_next_step(
        review_required=review_required,
        quarantined=quarantined,
        sealed=sealed,
    )
    applied_metadata = dict(prior_state.metadata)
    if metadata:
        applied_metadata.update(metadata)

    resulting_state = RuntimeControlState(
        review_required=review_required,
        quarantined=quarantined,
        sealed=sealed,
        last_operator_action=normalized_action,
        last_operator_note=operator_note,
        last_action_timestamp=now,
        action_history_count=prior_state.action_history_count + 1,
        trust_posture=trust_posture,
        next_step=next_step,
        metadata=applied_metadata,
    )
    save_runtime_control_state(runtime_root, resulting_state)

    record = RuntimeControlActionRecord(
        action=normalized_action,
        accepted=accepted,
        operator_note=operator_note,
        timestamp=now,
        prior_state=prior_state.to_record(),
        resulting_state=resulting_state.to_record(),
        message=message,
        metadata=dict(metadata or {}),
    )
    append_runtime_control_action(runtime_root, record)

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
            "sealed" if sealed else "unsealed",
            "quarantined" if quarantined else "clear",
            "review_required" if review_required else "review_clear",
        ],
        "incident_summary": message if sealed or quarantined else None,
        "runtime_control_state": resulting_state.to_record(),
        "metadata": {
            "action_recorded": True,
            "action_history_count": resulting_state.action_history_count,
            **dict(metadata or {}),
        },
    }


def _derive_posture_and_next_step(*, review_required: bool, quarantined: bool, sealed: bool) -> tuple[str, str]:
    if sealed:
        return "critical", "recover_from_seal"
    if quarantined:
        return "critical", "operator_quarantine_review"
    if review_required:
        return "degraded", "operator_review"
    return "healthy", "proceed"


def _control_root(runtime_root: str | Path) -> Path:
    return Path(runtime_root) / CONTROL_DIRNAME


def _state_path(runtime_root: str | Path) -> Path:
    return _control_root(runtime_root) / STATE_FILENAME


def _actions_path(runtime_root: str | Path) -> Path:
    return _control_root(runtime_root) / ACTIONS_FILENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
