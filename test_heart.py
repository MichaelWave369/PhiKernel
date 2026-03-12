import json
import time
from pathlib import Path

import pytest

from phik_anchor_v0_1_1 import StateAnchorService
from phik_capsule_v0_1_1 import ContinuityCapsuleStore
from phik_heart_v0_1_1 import (
    HeartAlreadyRunningError,
    HeartJobDefinition,
    HeartNotRunningError,
    HeartbeatService,
    JobRegistrationError,
)


@pytest.fixture
def forge_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Separate roots for anchor, capsule, and heart storage."""
    anchor_root = tmp_path / ".phik-anchor-vault"
    capsule_root = tmp_path / ".phik-capsule-vault"
    heart_root = tmp_path / ".phik-heart-vault"
    return anchor_root, capsule_root, heart_root


@pytest.fixture
def initialized_runtime(
    forge_paths: tuple[Path, Path, Path],
) -> tuple[StateAnchorService, ContinuityCapsuleStore, Path, str]:
    anchor_root, capsule_root, heart_root = forge_paths
    passphrase = "resonance-is-the-key-369"

    anchor_service = StateAnchorService(anchor_root)
    anchor_service.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="StateAnchor / Sovereign Heartbeat",
    )

    capsule_store = ContinuityCapsuleStore(capsule_root, anchor_service)
    return anchor_service, capsule_store, heart_root, passphrase



def test_register_job_and_execute_due_job(
    initialized_runtime: tuple[StateAnchorService, ContinuityCapsuleStore, Path, str],
) -> None:
    """PROVE: A manually registered due job executes and updates heart status."""
    anchor_service, capsule_store, heart_root, _ = initialized_runtime
    heart = HeartbeatService(
        heart_root,
        anchor_service=anchor_service,
        capsule_store=capsule_store,
    )

    counter = {"calls": 0}

    def ping_job() -> dict:
        counter["calls"] += 1
        return {"calls": counter["calls"], "message": "heart-ping"}

    heart.register_job(
        HeartJobDefinition(
            name="ping",
            interval_seconds=0.001,
            callback=ping_job,
            semantic_phase=6,
            run_immediately=True,
            description="Simple deterministic pulse.",
        )
    )

    results = heart.run_due_jobs_once()
    status = heart.status()

    assert len(results) == 1
    assert results[0].job_name == "ping"
    assert results[0].success is True
    assert results[0].output["message"] == "heart-ping"
    assert counter["calls"] == 1
    assert status.loop_iterations >= 1
    assert status.recent_jobs[-1]["job_name"] == "ping"



def test_install_default_jobs_and_checkpoint_creates_capsule(
    initialized_runtime: tuple[StateAnchorService, ContinuityCapsuleStore, Path, str],
) -> None:
    """PROVE: Default jobs verify anchor, collect metrics, and seal a checkpoint capsule."""
    anchor_service, capsule_store, heart_root, passphrase = initialized_runtime
    heart = HeartbeatService(
        heart_root,
        anchor_service=anchor_service,
        capsule_store=capsule_store,
    )

    def checkpoint_state_provider() -> dict:
        return {
            "thread_summary": "Automatic checkpoint",
            "semantic_phase": 9,
            "events": ["verify", "metrics", "seal"],
        }

    def coherence_provider() -> dict:
        return {
            "C_current": 0.781,
            "C_star": 0.809016,
            "distance_to_C_star": 0.028016,
            "fragmentation_score": 0.24,
            "recommended_action": "checkpoint",
        }

    heart.install_default_jobs(
        passphrase=passphrase,
        checkpoint_state_provider=checkpoint_state_provider,
        coherence_provider=coherence_provider,
        checkpoint_interval_seconds=0.001,
        anchor_verify_interval_seconds=0.001,
        metrics_interval_seconds=0.001,
    )

    time.sleep(0.01)
    results = heart.run_due_jobs_once()
    names = {result.job_name for result in results}
    status = heart.status()
    capsules = capsule_store.list_capsules()

    assert {"verify_anchor", "collect_metrics", "checkpoint_capsule"}.issubset(names)
    assert len(capsules) == 1
    assert status.last_checkpoint_capsule_id == capsules[0]["capsule_id"]
    assert status.last_coherence_frame is not None
    assert status.last_coherence_frame["C_current"] == 0.781
    assert status.last_coherence_frame["recommended_action"] == "checkpoint"



def test_invalid_anchor_is_reported_by_verify_job(
    initialized_runtime: tuple[StateAnchorService, ContinuityCapsuleStore, Path, str],
) -> None:
    """PROVE: The heart surfaces invalid anchor state during trust checks."""
    anchor_service, capsule_store, heart_root, _ = initialized_runtime
    heart = HeartbeatService(
        heart_root,
        anchor_service=anchor_service,
        capsule_store=capsule_store,
    )
    heart.install_default_jobs(
        checkpoint_state_provider=None,
        coherence_provider=None,
        anchor_verify_interval_seconds=0.001,
        metrics_interval_seconds=60.0,
    )

    manifest_path = anchor_service.root / "anchor_manifest.json"
    with manifest_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data["sovereign_name"] = "Imposter_System"
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)

    time.sleep(0.01)
    results = heart.run_due_jobs_once()
    verify_result = next(result for result in results if result.job_name == "verify_anchor")

    assert verify_result.success is True
    assert verify_result.output["valid"] is False
    assert "Signature verification failed" in verify_result.output["reason"]



def test_start_and_stop_cycle_persists_status(
    initialized_runtime: tuple[StateAnchorService, ContinuityCapsuleStore, Path, str],
) -> None:
    """PROVE: Background loop start/stop is safe and status is persisted to disk."""
    anchor_service, capsule_store, heart_root, _ = initialized_runtime
    heart = HeartbeatService(
        heart_root,
        anchor_service=anchor_service,
        capsule_store=capsule_store,
        tick_interval_seconds=0.01,
    )

    counter = {"calls": 0}

    def ping_job() -> dict:
        counter["calls"] += 1
        return {"calls": counter["calls"]}

    heart.register_job(
        HeartJobDefinition(
            name="background_ping",
            interval_seconds=0.01,
            callback=ping_job,
            semantic_phase=6,
            run_immediately=True,
        )
    )

    heart.start()
    time.sleep(0.05)
    assert heart.running is True

    heart.stop(timeout=2.0)
    status = heart.status()

    assert heart.running is False
    assert counter["calls"] >= 1
    assert status.loop_iterations >= 1
    assert heart.status_file.exists()

    with heart.status_file.open("r", encoding="utf-8") as fh:
        persisted = json.load(fh)

    assert persisted["running"] is False
    assert persisted["registered_jobs"] == 1
    assert persisted["loop_iterations"] >= 1



def test_lifecycle_and_registration_guards(
    initialized_runtime: tuple[StateAnchorService, ContinuityCapsuleStore, Path, str],
) -> None:
    """PROVE: Duplicate jobs and invalid start/stop cycles are rejected."""
    anchor_service, capsule_store, heart_root, _ = initialized_runtime
    heart = HeartbeatService(
        heart_root,
        anchor_service=anchor_service,
        capsule_store=capsule_store,
        tick_interval_seconds=0.01,
    )

    def noop() -> dict:
        return {"ok": True}

    definition = HeartJobDefinition(
        name="dup_job",
        interval_seconds=1.0,
        callback=noop,
        semantic_phase=3,
    )
    heart.register_job(definition)

    with pytest.raises(JobRegistrationError):
        heart.register_job(definition)

    with pytest.raises(HeartNotRunningError):
        heart.stop()

    heart.start()
    with pytest.raises(HeartAlreadyRunningError):
        heart.start()
    heart.stop(timeout=2.0)
