from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any
import importlib
import importlib.util
from pathlib import Path

ADAPTER_NAME = "tiekat_v50"
SUBSTRATE_NAME = "tiekat"


class TiekatV50AdapterError(Exception):
    """Raised when the TIEKAT v50 adapter cannot execute safely."""


def _load_v50_module() -> Any | None:
    try:
        return importlib.import_module("tiekat_v500")
    except ModuleNotFoundError:
        module_path = Path(__file__).resolve().parents[2] / "tiekat_v500.py"
        if not module_path.exists():
            raise TiekatV50AdapterError(
                "tiekat_v500 module is unavailable; ensure the substrate engine is present"
            )
        spec = importlib.util.spec_from_file_location("tiekat_v500", module_path)
        if spec is None or spec.loader is None:
            raise TiekatV50AdapterError("Unable to load tiekat_v500 substrate module")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ModuleNotFoundError:
            return None
        return module


def _session_from_legacy_payload(payload: dict[str, Any], v50: Any) -> Any:
    member_ids = list(payload.get("member_ids") or payload.get("members") or [])
    if not member_ids:
        for key in ("member_CI", "member_dS"):
            raw = payload.get(key)
            if isinstance(raw, dict):
                member_ids = list(raw.keys())
                break

    session_kwargs: dict[str, Any] = {
        "cohort_id": str(payload.get("cohort_id", "C0")),
        "member_ids": member_ids,
        "session_n": int(payload.get("session_n", 1)),
        "date": str(payload.get("date", "")),
        "copresent": bool(payload.get("copresent", True)),
        "member_CI": dict(payload.get("member_CI") or {}),
        "member_dS": dict(payload.get("member_dS") or {}),
        "member_tiekat": dict(payload.get("member_tiekat") or {}),
        "member_P": dict(payload.get("member_P") or {}),
        "member_crossed": dict(payload.get("member_crossed") or {}),
        "member_traced": dict(payload.get("member_traced") or {}),
        "member_path_modes": dict(payload.get("member_path_modes") or {}),
        "subgroup_labels": dict(payload.get("subgroup_labels") or {}),
        "active_pair_ids": list(payload.get("active_pair_ids") or []),
        "cohort_C_H": float(payload.get("cohort_C_H", 0.0)),
        "shared_basin_candidate": bool(payload.get("shared_basin_candidate", False)),
        "is_simulation": bool(payload.get("is_simulation", True)),
        "data_origin": str(payload.get("data_origin", "MIGRATED_LEGACY")),
        "notes": str(payload.get("notes", "")),
    }
    return v50.OversoulSession(**session_kwargs)


def _normalize_input(input_payload: dict[str, Any], v50: Any) -> tuple[list[Any], str]:
    sessions_payload = input_payload.get("sessions")
    if isinstance(sessions_payload, list) and sessions_payload:
        sessions = [
            item if hasattr(item, "report") else _session_from_legacy_payload(dict(item), v50)
            for item in sessions_payload
        ]
        return sessions, "session_list"

    if "field_session" in input_payload:
        sessions = [_session_from_legacy_payload(dict(input_payload["field_session"]), v50)]
        return sessions, "field_session_bridge"

    sessions = [_session_from_legacy_payload(input_payload, v50)]
    return sessions, "legacy_bridge"


def _to_record(value: Any) -> Any:
    if hasattr(value, "report"):
        try:
            return value.report()
        except TypeError:
            pass
    if is_dataclass(value):
        return asdict(value)
    return value


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    v50 = _load_v50_module()
    if v50 is None:
        return {
            "adapter": ADAPTER_NAME,
            "substrate": SUBSTRATE_NAME,
            "substrate_version": "50.0.0",
            "mode": "degraded_no_numpy",
            "profile": {
                "oversoul_verdict": "OVERSOUL_UNREADY",
                "evidence_level": "L1_PREDICTION",
                "oversoul_coherence": 0.0,
                "manifold_stability": 0.0,
            },
            "state": {
                "manifold_readiness": 0.0,
                "manifold_risk": 1.0,
            },
            "recommendation": {"cohort_guidance": "Install numpy to enable full TIEKAT v50 analysis."},
            "null_result": {},
            "debug": {
                "degraded": True,
                "reason": "numpy unavailable",
                "input_keys": sorted(payload.keys()),
                "total_sessions": len(payload.get("sessions") or [payload.get("field_session") or payload]),
            },
        }

    sessions, mode = _normalize_input(payload, v50)
    cohort_id = str(payload.get("cohort_id") or (sessions[0].cohort_id if sessions else "C0"))
    profile = v50.OversoulAnalyzer.analyze(sessions=sessions, cohort_id=cohort_id)

    return {
        "adapter": ADAPTER_NAME,
        "substrate": SUBSTRATE_NAME,
        "substrate_version": getattr(v50, "TIEKAT_VERSION", "50.0.0"),
        "mode": mode,
        "profile": _to_record(profile),
        "state": _to_record(getattr(profile, "current_state", None)),
        "recommendation": _to_record(getattr(profile, "recommendation", None)),
        "null_result": _to_record(getattr(profile, "null_result", {})),
        "debug": {
            "total_sessions": len(sessions),
            "cohort_id": cohort_id,
            "input_keys": sorted(payload.keys()),
        },
    }
