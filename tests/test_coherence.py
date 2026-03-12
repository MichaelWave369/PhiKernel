from pathlib import Path
import json

import pytest

from phikernel.coherence import (
    CoherenceFrame,
    CoherenceService,
    CoherenceThresholds,
    DEFAULT_C_STAR,
    RuntimeObservation,
)


@pytest.fixture
def coherence_root(tmp_path: Path) -> Path:
    """Temporary storage for coherence frames."""
    return tmp_path / ".phik-coherence-vault"


@pytest.fixture
def service(coherence_root: Path) -> CoherenceService:
    return CoherenceService(coherence_root)



def test_stable_state_scores_near_attractor(service: CoherenceService) -> None:
    """PROVE: A healthy runtime stays in the stable observation band near C*."""
    frame = service.observe(
        {
            "anchor_id": "anchor-stable",
            "anchor_valid": True,
            "heartbeat_running": True,
            "capsule_store_configured": True,
            "capsule_count": 6,
            "active_threads": 1,
            "pending_events": 0,
            "unresolved_alerts": 0,
            "last_checkpoint_age_seconds": 45.0,
            "checkpoint_due": False,
            "notes": ["stable test"],
        }
    )

    assert isinstance(frame, CoherenceFrame)
    assert frame.anchor_id == "anchor-stable"
    assert frame.anchor_valid is True
    assert frame.recommended_action == "observe"
    assert frame.drift_band == "stable"
    assert frame.semantic_phase == 6
    assert frame.C_star == pytest.approx(DEFAULT_C_STAR, abs=1e-6)
    assert frame.C_current == pytest.approx(DEFAULT_C_STAR, abs=0.12)
    assert frame.distance_to_C_star < 0.12
    assert 0.0 <= frame.lambda_node <= 1.0
    assert 0.0 <= frame.phi_flow <= 1.0
    assert 0.0 <= frame.sigma_feedback <= 1.0
    assert 0.0 <= frame.fragmentation_score < 0.20



def test_warning_threshold_triggers_checkpoint(service: CoherenceService) -> None:
    """PROVE: Moderate drift produces a checkpoint recommendation."""
    frame = service.observe(
        RuntimeObservation(
            anchor_id="anchor-warning",
            anchor_valid=True,
            heartbeat_running=True,
            capsule_store_configured=True,
            capsule_count=1,
            active_threads=4,
            pending_events=8,
            unresolved_alerts=1,
            last_checkpoint_age_seconds=1800.0,
            checkpoint_due=True,
            notes=("warning test",),
        )
    )

    assert frame.recommended_action == "checkpoint"
    assert frame.drift_band == "warning"
    assert frame.semantic_phase == 9
    assert frame.fragmentation_score >= 0.20 or frame.distance_to_C_star >= 0.12
    assert any("checkpoint" in note.lower() for note in frame.notes)



def test_restore_threshold_triggers_restore(service: CoherenceService) -> None:
    """PROVE: Strong drift produces a restore recommendation."""
    frame = service.observe(
        {
            "anchor_id": "anchor-restore",
            "anchor_valid": True,
            "heartbeat_running": False,
            "capsule_store_configured": True,
            "capsule_count": 0,
            "active_threads": 10,
            "pending_events": 20,
            "unresolved_alerts": 4,
            "last_checkpoint_age_seconds": 5400.0,
            "checkpoint_due": True,
            "external_fragmentation_hint": 0.55,
            "notes": ["restore test"],
        }
    )

    assert frame.recommended_action == "restore"
    assert frame.drift_band == "restore"
    assert frame.semantic_phase == 9
    assert frame.fragmentation_score >= 0.42 or frame.distance_to_C_star >= 0.24 or frame.sigma_feedback >= 0.65
    assert any("restore threshold" in note.lower() for note in frame.notes)



def test_invalid_anchor_triggers_alert(service: CoherenceService) -> None:
    """PROVE: Anchor failure becomes a critical coherence alert."""
    frame = service.observe(
        {
            "anchor_id": "anchor-invalid",
            "anchor_valid": False,
            "heartbeat_running": True,
            "capsule_store_configured": True,
            "capsule_count": 3,
            "active_threads": 1,
            "pending_events": 0,
            "unresolved_alerts": 0,
            "last_checkpoint_age_seconds": 30.0,
            "checkpoint_due": False,
            "notes": ["invalid anchor test"],
        }
    )

    assert frame.anchor_valid is False
    assert frame.recommended_action == "alert"
    assert frame.drift_band == "critical"
    assert frame.semantic_phase == 3
    assert any("anchor verification failed" in note.lower() for note in frame.notes)



def test_custom_c_current_hint_is_respected(service: CoherenceService) -> None:
    """PROVE: An explicit TIEKAT-side coherence hint is honored by the scorer."""
    frame = service.observe(
        {
            "anchor_id": "anchor-hint",
            "anchor_valid": True,
            "heartbeat_running": True,
            "capsule_store_configured": True,
            "capsule_count": 2,
            "active_threads": 2,
            "pending_events": 1,
            "unresolved_alerts": 0,
            "C_current_hint": 0.777,
            "notes": ["hint test"],
        }
    )

    assert frame.C_current == pytest.approx(0.777, abs=1e-6)
    assert frame.distance_to_C_star == pytest.approx(abs(0.777 - DEFAULT_C_STAR), abs=1e-6)



def test_heart_provider_returns_frame_record(service: CoherenceService) -> None:
    """PROVE: The provider bridge emits a heart-consumable frame dict."""
    def state_provider() -> dict:
        return {
            "anchor_id": "anchor-provider",
            "anchor_valid": True,
            "heartbeat_running": True,
            "capsule_store_configured": True,
            "capsule_count": 4,
            "active_threads": 2,
            "pending_events": 2,
            "unresolved_alerts": 0,
            "last_checkpoint_age_seconds": 300.0,
            "checkpoint_due": False,
            "notes": ["provider test"],
        }

    provider = service.make_heart_provider(state_provider)
    record = provider()

    assert isinstance(record, dict)
    assert record["anchor_id"] == "anchor-provider"
    assert record["recommended_action"] in {"observe", "checkpoint", "restore", "alert"}
    assert record["semantic_phase"] in {3, 6, 9}
    assert service.latest_frame() is not None
    assert service.latest_frame().frame_id == record["frame_id"]



def test_persisted_frame_matches_latest_observation(service: CoherenceService, coherence_root: Path) -> None:
    """PROVE: The saved frame reflects the latest observation accurately."""
    frame = service.observe(
        {
            "anchor_id": "anchor-persisted",
            "anchor_valid": True,
            "heartbeat_running": True,
            "capsule_store_configured": False,
            "capsule_count": 0,
            "active_threads": 3,
            "pending_events": 4,
            "unresolved_alerts": 1,
            "last_checkpoint_age_seconds": None,
            "checkpoint_due": False,
            "notes": ["persisted test"],
        }
    )

    assert service.frame_file.exists()

    with service.frame_file.open("r", encoding="utf-8") as fh:
        persisted = json.load(fh)

    assert persisted["frame_id"] == frame.frame_id
    assert persisted["anchor_id"] == frame.anchor_id
    assert persisted["recommended_action"] == frame.recommended_action
    assert persisted["drift_band"] == frame.drift_band
    assert persisted["C_current"] == frame.C_current
    assert persisted["distance_to_C_star"] == frame.distance_to_C_star
    assert persisted["notes"] == list(frame.notes)



def test_custom_thresholds_shift_checkpoint_boundary(coherence_root: Path) -> None:
    """PROVE: Threshold tuning changes intervention behavior deterministically."""
    custom_service = CoherenceService(
        coherence_root / "custom-thresholds",
        thresholds=CoherenceThresholds(
            fragmentation_warn=0.05,
            fragmentation_restore=0.50,
            distance_warn=0.05,
            distance_restore=0.30,
        ),
    )

    frame = custom_service.observe(
        {
            "anchor_id": "anchor-custom-thresholds",
            "anchor_valid": True,
            "heartbeat_running": True,
            "capsule_store_configured": True,
            "capsule_count": 1,
            "active_threads": 2,
            "pending_events": 2,
            "unresolved_alerts": 0,
            "last_checkpoint_age_seconds": 120.0,
            "checkpoint_due": False,
            "notes": ["threshold test"],
        }
    )

    assert frame.recommended_action in {"checkpoint", "restore"}
    assert frame.drift_band in {"warning", "restore"}
