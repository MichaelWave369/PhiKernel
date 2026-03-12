from __future__ import annotations

"""
phik_heart_v0_1_1.py

Reference implementation for PhiKernel's third substrate module:
`phik-heart` (Heartbeat + Scheduler).

This module turns the Anchor + Capsule substrate into a living local runtime.
It provides:
- A monotonic-clock scheduler for recurring jobs.
- A heartbeat loop with safe start / stop semantics.
- Structured job results and runtime status.
- Built-in maintenance jobs for anchor verification and optional checkpointing.
- Hook points for TIEKAT / coherence scoring providers.
- Atomic persistence of heart status for shell / UI consumers.

Dependencies:
- phik_anchor_v0_1_1.py
- phik_capsule_v0_1_1.py

This is designed as a local-first substrate service, not a distributed scheduler.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import json
import os
import tempfile
import threading
import time

from phikernel.anchor import StateAnchorService
from phikernel.capsule import ContinuityCapsuleStore


DEFAULT_HEART_VERSION = "0.1.1"
DEFAULT_TICK_INTERVAL_SECONDS = 0.25
DEFAULT_HISTORY_LIMIT = 100
DEFAULT_CHECKPOINT_INTERVAL_SECONDS = 300.0
DEFAULT_ANCHOR_VERIFY_INTERVAL_SECONDS = 60.0
DEFAULT_METRICS_INTERVAL_SECONDS = 30.0
DEFAULT_HEART_PHASE = 6
VALID_PHASES = {3, 6, 9}


class HeartbeatError(Exception):
    """Base exception for all phik-heart failures."""


class HeartAlreadyRunningError(HeartbeatError):
    """Raised when start() is called twice without stopping."""


class HeartNotRunningError(HeartbeatError):
    """Raised when stop() is called while the heart is inactive."""


class JobRegistrationError(HeartbeatError):
    """Raised when an invalid job definition is registered."""


@dataclass(frozen=True)
class HeartJobDefinition:
    """Definition for a recurring heartbeat job."""

    name: str
    interval_seconds: float
    callback: Callable[[], dict[str, Any]]
    semantic_phase: int = DEFAULT_HEART_PHASE
    enabled: bool = True
    run_immediately: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise JobRegistrationError("Job name must not be empty")
        if self.interval_seconds <= 0:
            raise JobRegistrationError("Job interval_seconds must be > 0")
        if self.semantic_phase not in VALID_PHASES:
            raise JobRegistrationError(
                f"Invalid semantic_phase '{self.semantic_phase}'. Expected one of {sorted(VALID_PHASES)}"
            )
        if not callable(self.callback):
            raise JobRegistrationError("Job callback must be callable")


@dataclass
class _ScheduledJob:
    definition: HeartJobDefinition
    next_run_monotonic: float
    last_run_monotonic: float | None = None
    last_result: "HeartJobResult | None" = None


@dataclass(frozen=True)
class HeartJobResult:
    """Structured output for each executed heartbeat job."""

    job_name: str
    semantic_phase: int
    success: bool
    started_at_wall: float
    finished_at_wall: float
    duration_seconds: float
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class HeartbeatStatus:
    """Public status surface safe for shell / monitoring consumption."""

    version: str
    running: bool
    started_at_wall: float | None
    stopped_at_wall: float | None
    tick_interval_seconds: float
    loop_iterations: int
    registered_jobs: int
    last_tick_wall: float | None
    last_checkpoint_capsule_id: str | None
    last_coherence_frame: dict[str, Any] | None
    recent_jobs: list[dict[str, Any]]


class HeartbeatService:
    """Monotonic scheduler and maintenance loop for PhiKernel.

    Core responsibilities:
    - Maintain a monotonic scheduler for recurring jobs.
    - Verify anchor health periodically.
    - Optionally seal checkpoint capsules from a state provider.
    - Collect coherence metrics from a provider callback.
    - Publish a public status record for shell / UI consumption.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        anchor_service: StateAnchorService,
        capsule_store: ContinuityCapsuleStore | None = None,
        tick_interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> None:
        if tick_interval_seconds <= 0:
            raise HeartbeatError("tick_interval_seconds must be > 0")
        if history_limit <= 0:
            raise HeartbeatError("history_limit must be > 0")

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.status_file = self.root / "heart_status.json"

        self.anchor_service = anchor_service
        self.capsule_store = capsule_store
        self.tick_interval_seconds = tick_interval_seconds
        self.history_limit = history_limit

        self._jobs: dict[str, _ScheduledJob] = {}
        self._history: list[HeartJobResult] = []
        self._loop_iterations = 0
        self._started_at_wall: float | None = None
        self._stopped_at_wall: float | None = None
        self._last_tick_wall: float | None = None
        self._last_checkpoint_capsule_id: str | None = None
        self._last_coherence_frame: dict[str, Any] | None = None

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    def register_job(self, definition: HeartJobDefinition) -> None:
        """Register a recurring job before or during runtime."""
        with self._lock:
            if definition.name in self._jobs:
                raise JobRegistrationError(f"Job '{definition.name}' is already registered")

            now = time.monotonic()
            next_run = now if definition.run_immediately else now + definition.interval_seconds
            self._jobs[definition.name] = _ScheduledJob(
                definition=definition,
                next_run_monotonic=next_run,
            )
            self._persist_status()

    def start(self) -> None:
        """Start the background heartbeat loop."""
        with self._lock:
            if self.running:
                raise HeartAlreadyRunningError("Heartbeat is already running")

            self._stop_event.clear()
            self._started_at_wall = time.time()
            self._stopped_at_wall = None
            self._thread = threading.Thread(
                target=self._run_loop,
                name="phik-heart",
                daemon=True,
            )
            self._thread.start()
            self._persist_status()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background heartbeat loop."""
        thread: threading.Thread | None
        with self._lock:
            if not self.running or self._thread is None:
                raise HeartNotRunningError("Heartbeat is not running")
            thread = self._thread
            self._stop_event.set()

        thread.join(timeout=timeout)
        with self._lock:
            self._stopped_at_wall = time.time()
            self._thread = None
            self._persist_status()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def run_due_jobs_once(self) -> list[HeartJobResult]:
        """Execute all jobs whose monotonic schedule is due right now.

        This is useful for deterministic tests without starting the background loop.
        """
        with self._lock:
            now = time.monotonic()
            due_names = [name for name, job in self._jobs.items() if job.definition.enabled and now >= job.next_run_monotonic]

        results: list[HeartJobResult] = []
        for name in due_names:
            result = self._execute_job(name)
            if result is not None:
                results.append(result)

        with self._lock:
            self._loop_iterations += 1
            self._last_tick_wall = time.time()
            self._persist_status()
        return results

    def install_default_jobs(
        self,
        *,
        passphrase: str | None = None,
        checkpoint_state_provider: Callable[[], dict[str, Any]] | None = None,
        coherence_provider: Callable[[], dict[str, Any]] | None = None,
        checkpoint_interval_seconds: float = DEFAULT_CHECKPOINT_INTERVAL_SECONDS,
        anchor_verify_interval_seconds: float = DEFAULT_ANCHOR_VERIFY_INTERVAL_SECONDS,
        metrics_interval_seconds: float = DEFAULT_METRICS_INTERVAL_SECONDS,
    ) -> None:
        """Install the default maintenance jobs for the local runtime.

        Jobs installed:
        - verify_anchor
        - collect_metrics (optional coherence provider)
        - checkpoint_capsule (requires capsule store, passphrase, and state provider)
        """
        self.register_job(
            HeartJobDefinition(
                name="verify_anchor",
                interval_seconds=anchor_verify_interval_seconds,
                semantic_phase=3,
                run_immediately=True,
                description="Verify signed anchor integrity.",
                callback=self._make_anchor_verify_job(),
            )
        )

        self.register_job(
            HeartJobDefinition(
                name="collect_metrics",
                interval_seconds=metrics_interval_seconds,
                semantic_phase=6,
                run_immediately=True,
                description="Collect runtime / coherence metrics.",
                callback=self._make_collect_metrics_job(coherence_provider),
            )
        )

        if checkpoint_state_provider is not None:
            if self.capsule_store is None:
                raise HeartbeatError(
                    "checkpoint_state_provider was supplied but capsule_store is not configured"
                )
            if passphrase is None:
                raise HeartbeatError(
                    "checkpoint_state_provider was supplied but passphrase is missing"
                )
            self.register_job(
                HeartJobDefinition(
                    name="checkpoint_capsule",
                    interval_seconds=checkpoint_interval_seconds,
                    semantic_phase=9,
                    run_immediately=False,
                    description="Seal periodic continuity checkpoints.",
                    callback=self._make_checkpoint_job(
                        passphrase=passphrase,
                        state_provider=checkpoint_state_provider,
                    ),
                )
            )

    def status(self) -> HeartbeatStatus:
        with self._lock:
            return HeartbeatStatus(
                version=DEFAULT_HEART_VERSION,
                running=self.running,
                started_at_wall=self._started_at_wall,
                stopped_at_wall=self._stopped_at_wall,
                tick_interval_seconds=self.tick_interval_seconds,
                loop_iterations=self._loop_iterations,
                registered_jobs=len(self._jobs),
                last_tick_wall=self._last_tick_wall,
                last_checkpoint_capsule_id=self._last_checkpoint_capsule_id,
                last_coherence_frame=self._last_coherence_frame,
                recent_jobs=[self._result_to_record(r) for r in self._history],
            )

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_due_jobs_once()
            self._stop_event.wait(self.tick_interval_seconds)

    def _execute_job(self, name: str) -> HeartJobResult | None:
        with self._lock:
            job = self._jobs.get(name)
            if job is None:
                return None
            definition = job.definition

        started = time.time()
        started_mono = time.monotonic()
        try:
            raw_output = definition.callback() or {}
            if not isinstance(raw_output, dict):
                raise HeartbeatError(
                    f"Job '{definition.name}' returned a non-dict result: {type(raw_output)!r}"
                )
            success = True
            error = None
            output = raw_output
        except Exception as exc:  # noqa: BLE001
            success = False
            error = f"{type(exc).__name__}: {exc}"
            output = {}

        finished = time.time()
        duration = time.monotonic() - started_mono
        result = HeartJobResult(
            job_name=definition.name,
            semantic_phase=definition.semantic_phase,
            success=success,
            started_at_wall=started,
            finished_at_wall=finished,
            duration_seconds=duration,
            output=output,
            error=error,
        )

        with self._lock:
            scheduled = self._jobs[name]
            scheduled.last_run_monotonic = time.monotonic()
            scheduled.next_run_monotonic = scheduled.last_run_monotonic + definition.interval_seconds
            scheduled.last_result = result
            self._history.append(result)
            self._history = self._history[-self.history_limit :]
            self._apply_side_effects(result)
            self._persist_status()
        return result

    def _apply_side_effects(self, result: HeartJobResult) -> None:
        if result.job_name == "checkpoint_capsule" and result.success:
            self._last_checkpoint_capsule_id = result.output.get("capsule_id")
        if result.job_name == "collect_metrics" and result.success:
            self._last_coherence_frame = result.output

    def _make_anchor_verify_job(self) -> Callable[[], dict[str, Any]]:
        def job() -> dict[str, Any]:
            verification = self.anchor_service.verify_anchor()
            return {
                "valid": verification.valid,
                "reason": verification.reason,
                "anchor_id": verification.anchor_id,
                "manifest_hash": verification.manifest_hash,
                "verified_at": verification.verified_at,
            }

        return job

    def _make_collect_metrics_job(
        self,
        coherence_provider: Callable[[], dict[str, Any]] | None,
    ) -> Callable[[], dict[str, Any]]:
        def job() -> dict[str, Any]:
            anchor_status = self.anchor_service.public_status()
            metrics: dict[str, Any] = {
                "semantic_phase": 6,
                "anchor_id": anchor_status["anchor_id"],
                "anchor_valid": anchor_status["verification"]["valid"],
                "manifest_hash": anchor_status["manifest_hash"],
                "capsule_store_configured": self.capsule_store is not None,
                "observed_at": time.time(),
            }
            if coherence_provider is not None:
                extra = coherence_provider() or {}
                if not isinstance(extra, dict):
                    raise HeartbeatError("coherence_provider must return a dict")
                metrics.update(extra)
            return metrics

        return job

    def _make_checkpoint_job(
        self,
        *,
        passphrase: str,
        state_provider: Callable[[], dict[str, Any]],
    ) -> Callable[[], dict[str, Any]]:
        def job() -> dict[str, Any]:
            if self.capsule_store is None:
                raise HeartbeatError("capsule_store is not configured")
            state = state_provider() or {}
            if not isinstance(state, dict):
                raise HeartbeatError("checkpoint state_provider must return a dict")

            capsule = self.capsule_store.seal(
                passphrase=passphrase,
                state=state,
                capsule_type="checkpoint",
                semantic_phase=9,
                tags=["heartbeat", "auto-checkpoint"],
                summary="Automatic heart checkpoint",
            )
            verification = self.capsule_store.verify_capsule(capsule.capsule_id)
            return {
                "capsule_id": capsule.capsule_id,
                "capsule_type": capsule.capsule_type,
                "semantic_phase": capsule.semantic_phase,
                "verified": verification.valid,
                "verification_reason": verification.reason,
                "created_at": capsule.created_at,
            }

        return job

    def _persist_status(self) -> None:
        status = self.status()
        _atomic_write_json(self.status_file, {
            "version": status.version,
            "running": status.running,
            "started_at_wall": status.started_at_wall,
            "stopped_at_wall": status.stopped_at_wall,
            "tick_interval_seconds": status.tick_interval_seconds,
            "loop_iterations": status.loop_iterations,
            "registered_jobs": status.registered_jobs,
            "last_tick_wall": status.last_tick_wall,
            "last_checkpoint_capsule_id": status.last_checkpoint_capsule_id,
            "last_coherence_frame": status.last_coherence_frame,
            "recent_jobs": status.recent_jobs,
        })
        _chmod_owner_only(self.status_file)

    def _result_to_record(self, result: HeartJobResult) -> dict[str, Any]:
        return {
            "job_name": result.job_name,
            "semantic_phase": result.semantic_phase,
            "success": result.success,
            "started_at_wall": result.started_at_wall,
            "finished_at_wall": result.finished_at_wall,
            "duration_seconds": result.duration_seconds,
            "output": result.output,
            "error": result.error,
        }


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as tmp:
        json.dump(data, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


def _chmod_owner_only(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


if __name__ == "__main__":
    # Minimal deterministic smoke test without starting the background loop.
    anchor_root = Path("./.phik-anchor-demo")
    capsule_root = Path("./.phik-capsule-demo")
    heart_root = Path("./.phik-heart-demo")
    passphrase = "change-this-demo-passphrase"

    anchor_service = StateAnchorService(anchor_root)
    if not (anchor_root / "anchor_manifest.json").exists():
        anchor_service.initialize(
            passphrase=passphrase,
            sovereign_name="Tal-Aren-Vox",
            user_label="Ori",
            resonant_label="StateAnchor / Sovereign Heartbeat",
        )

    capsule_store = ContinuityCapsuleStore(capsule_root, anchor_service)

    def state_provider() -> dict[str, Any]:
        return {
            "thread_summary": "Heartbeat smoke test",
            "phase": 9,
            "events": ["anchor-verified", "checkpoint-requested"],
        }

    def coherence_provider() -> dict[str, Any]:
        return {
            "C_current": 0.781,
            "C_star": 0.809016,
            "distance_to_C_star": 0.028016,
            "fragmentation_score": 0.24,
            "recommended_action": "checkpoint",
        }

    heart = HeartbeatService(
        heart_root,
        anchor_service=anchor_service,
        capsule_store=capsule_store,
    )
    heart.install_default_jobs(
        passphrase=passphrase,
        checkpoint_state_provider=state_provider,
        coherence_provider=coherence_provider,
        checkpoint_interval_seconds=0.01,
        anchor_verify_interval_seconds=0.01,
        metrics_interval_seconds=0.01,
    )

    time.sleep(0.02)
    results = heart.run_due_jobs_once()
    for result in results:
        print(result)
    print(heart.status())
