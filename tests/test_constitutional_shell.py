import json
from pathlib import Path
import time

import pytest

from phikernel.anchor import StateAnchorService
from phikernel.capsule import ContinuityCapsuleStore
from phikernel.constitutional_shell import (
    build_shell_candidate_seeds,
    build_shell_route_candidates,
)
from phikernel.constitutional_store import PersistedConstitutionalState
from phikernel.control_state import quarantine_runtime, seal_runtime
from phikernel.control_witness import (
    ActionUsage,
    BoundedControlGrant,
    ControlActionRule,
    ControlContract,
    ControlPromotionReceipt,
    ControlSession,
)
from phikernel.shell import PhiKernelShell, RuntimePaths
from phikernel.warrant import ResourceBudget, Warrant
from phikernel.witness_bench import (
    ADVISE,
    BOUNDED_CONTROL,
    SHADOW,
    PromotionAuthorizationReceipt,
    PromotionState,
)


@pytest.fixture
def runtime_paths(tmp_path: Path) -> RuntimePaths:
    runtime_root = tmp_path / ".phik-runtime"
    return RuntimePaths(
        runtime_root=runtime_root,
        anchor_root=runtime_root / "anchor",
        capsule_root=runtime_root / "capsule",
        heart_root=runtime_root / "heart",
        coherence_root=runtime_root / "coherence",
        control_root=runtime_root / "control",
    )


@pytest.fixture
def initialized_shell(
    runtime_paths: RuntimePaths,
) -> tuple[PhiKernelShell, str, ContinuityCapsuleStore]:
    passphrase = "resonance-is-the-key-369"
    anchor = StateAnchorService(runtime_paths.anchor_root)
    anchor.initialize(
        passphrase=passphrase,
        sovereign_name="Tal-Aren-Vox",
        user_label="Ori",
        resonant_label="StateAnchor / Sovereign Heartbeat",
    )
    capsules = ContinuityCapsuleStore(
        runtime_paths.capsule_root,
        anchor,
        control_root=runtime_paths.runtime_root,
    )
    shell = PhiKernelShell(runtime_paths)
    return shell, passphrase, capsules


def _write_heart(runtime_paths: RuntimePaths, *, running: bool = True) -> None:
    runtime_paths.heart_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "0.1.1",
        "running": running,
        "started_at_wall": 111.0,
        "stopped_at_wall": None if running else 222.0,
        "tick_interval_seconds": 0.25,
        "loop_iterations": 3,
        "registered_jobs": 2,
        "last_tick_wall": 333.0,
        "last_checkpoint_capsule_id": None,
        "last_coherence_frame": None,
        "recent_jobs": [],
    }
    with (runtime_paths.heart_root / "heart_status.json").open(
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(payload, fh)


def _write_field(
    runtime_paths: RuntimePaths,
    *,
    action: str = "observe",
    band: str = "stable",
) -> None:
    runtime_paths.coherence_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "0.1.1",
        "frame_id": "frame-test",
        "observed_at": 123.456,
        "semantic_phase": 6,
        "anchor_id": "anchor-test",
        "anchor_valid": True,
        "C_current": 0.801,
        "C_star": 0.809016,
        "distance_to_C_star": 0.008016,
        "phi_flow": 0.74,
        "lambda_node": 0.91,
        "sigma_feedback": 0.18,
        "fragmentation_score": 0.12,
        "recommended_action": action,
        "drift_band": band,
        "notes": ["constitutional-shell test"],
    }
    with (runtime_paths.coherence_root / "coherence_frame.json").open(
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(payload, fh)


def _seed_capsule(
    store: ContinuityCapsuleStore,
    passphrase: str,
) -> None:
    store.seal(
        passphrase=passphrase,
        state={"context": "constitutional shell", "phase": 9},
        capsule_type="working",
        semantic_phase=9,
        tags=["latest"],
        summary="Constitutional shell state",
    )


def _ready_shell(
    initialized_shell,
    runtime_paths: RuntimePaths,
) -> PhiKernelShell:
    shell, passphrase, capsules = initialized_shell
    _write_heart(runtime_paths, running=True)
    _write_field(runtime_paths)
    _seed_capsule(capsules, passphrase)
    return shell


def _persist_advise(shell: PhiKernelShell, *, base: float | None = None):
    t0 = time.time() if base is None else float(base)
    receipt = PromotionAuthorizationReceipt(
        receipt_id="receipt:advise-shell",
        proposal_id="proposal:advise-shell",
        witness_report_id="report:advise-shell",
        witness_report_hash="a" * 64,
        prior_mode=SHADOW,
        resulting_mode=ADVISE,
        prior_revision=0,
        resulting_revision=1,
        human_seal_id="seal:advise-shell",
        human_actor_id="human:mikey",
        authority_ref="anchor:human",
        applied_at=t0,
    )
    promotion = PromotionState(
        mode=ADVISE,
        revision=1,
        last_receipt_id=receipt.receipt_id,
        authorized_by_seal_id=receipt.human_seal_id,
    )
    state = PersistedConstitutionalState(
        promotion_state=promotion,
        advise_receipt=receipt,
    )
    shell.constitutional_store.save(
        state,
        written_at=t0,
        now=t0,
    )
    return state


def _persist_bounded(shell: PhiKernelShell):
    t0 = time.time()
    advise = _persist_advise(shell, base=t0)

    contract = ControlContract.create(
        actor_id="node:flow",
        action_rules=(
            ControlActionRule(
                rule_id="rule:python-shell",
                operation="execute",
                target="tool/python",
                max_count=3,
            ),
        ),
        resource_limits=(ResourceBudget("compute_ms", 100.0),),
        max_total_actions=3,
        max_clock_ticks=20.0,
        lifetime_seconds=3600.0,
        required_evidence_refs=("evidence:shell-approved",),
        created_at=t0 + 1.0,
    )
    proposal_id = "proposal:control-shell"
    witness_report_hash = "b" * 64
    warrant = Warrant.issue(
        issuer="human:mikey",
        bearer=contract.actor_id,
        scopes=contract.scopes,
        budgets=(ResourceBudget("compute_ms", 100.0),),
        lifetime_seconds=contract.lifetime_seconds,
        issued_at=t0 + 2.0,
        metadata={
            "control_contract_id": contract.contract_id,
            "control_contract_hash": contract.contract_hash,
            "control_promotion_proposal_id": proposal_id,
            "control_witness_report_hash": witness_report_hash,
            "human_seal_id": "seal:control-shell",
        },
    )
    assert warrant.expires_at is not None

    grant = BoundedControlGrant(
        grant_id="grant:control-shell",
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        promotion_proposal_id=proposal_id,
        witness_report_id="report:control-shell",
        witness_report_hash=witness_report_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        human_seal_id="seal:control-shell",
        human_actor_id="human:mikey",
        authority_ref="anchor:human-control",
        granted_at=t0 + 2.0,
        expires_at=warrant.expires_at,
    )
    receipt = ControlPromotionReceipt(
        receipt_id="receipt:control-shell",
        proposal_id=proposal_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        witness_report_id=grant.witness_report_id,
        witness_report_hash=grant.witness_report_hash,
        prior_mode=ADVISE,
        resulting_mode=BOUNDED_CONTROL,
        prior_revision=1,
        resulting_revision=2,
        grant_id=grant.grant_id,
        warrant_id=warrant.warrant_id,
        human_seal_id=grant.human_seal_id,
        human_actor_id=grant.human_actor_id,
        authority_ref=grant.authority_ref,
        applied_at=t0 + 2.0,
    )
    promotion = PromotionState(
        mode=BOUNDED_CONTROL,
        revision=2,
        last_receipt_id=receipt.receipt_id,
        authorized_by_seal_id=grant.human_seal_id,
    )
    session = ControlSession(
        session_id="session:control-shell",
        grant_id=grant.grant_id,
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        actor_id=contract.actor_id,
        warrant=warrant,
        started_at=t0 + 3.0,
        actions_used=0,
        clock_ticks_used=0.0,
        action_usage=(
            ActionUsage(rule_id="rule:python-shell", count=0),
        ),
    )
    state = PersistedConstitutionalState(
        promotion_state=promotion,
        advise_receipt=advise.advise_receipt,
        control_contract=contract,
        control_grant=grant,
        control_receipt=receipt,
        control_session=session,
    )
    shell.constitutional_store.save(
        state,
        written_at=t0 + 3.0,
        now=t0 + 3.0,
    )
    return state


def test_flow_prompt_receives_lower_flow_seed_cost_when_substrate_is_safe() -> None:
    bundle = {
        "prompt": "I need momentum to create and build the next thing",
        "anchor": {"verification": {"valid": True}},
        "heart": {"running": True},
        "field": {"recommended_action": "observe"},
        "latest_capsule": {"capsule_id": "cap"},
    }

    seeds = build_shell_candidate_seeds(bundle)
    by_coach = {seed.coach: seed for seed in seeds}

    assert by_coach["Flow"].base_cost < by_coach["Titan"].base_cost
    assert by_coach["Flow"].base_cost < by_coach["Sage"].base_cost
    assert any(
        "flow keyword matches" in reason
        for reason in by_coach["Flow"].reasons
    )


def test_sage_prompt_receives_lower_sage_seed_cost_when_substrate_is_safe() -> None:
    bundle = {
        "prompt": "Help me reflect on the meaning and pattern here",
        "anchor": {"verification": {"valid": True}},
        "heart": {"running": True},
        "field": {"recommended_action": "observe"},
        "latest_capsule": {"capsule_id": "cap"},
    }

    seeds = build_shell_candidate_seeds(bundle)
    by_coach = {seed.coach: seed for seed in seeds}

    assert by_coach["Sage"].base_cost < by_coach["Titan"].base_cost
    assert by_coach["Sage"].base_cost < by_coach["Flow"].base_cost


def test_safety_condition_prioritizes_titan_seed_without_granting_authority() -> None:
    bundle = {
        "prompt": "create build momentum",
        "anchor": {"verification": {"valid": True}},
        "heart": {"running": True},
        "field": {"recommended_action": "alert"},
        "latest_capsule": {"capsule_id": "cap"},
    }

    seeds = build_shell_candidate_seeds(bundle)
    by_coach = {seed.coach: seed for seed in seeds}

    assert by_coach["Titan"].base_cost == pytest.approx(0.0)
    assert by_coach["Flow"].base_cost == pytest.approx(10.0)
    assert by_coach["Sage"].base_cost == pytest.approx(10.0)


def test_shell_route_candidates_receive_evaluation_only_warrants() -> None:
    bundle = {
        "prompt": "create something",
        "anchor": {"verification": {"valid": True}},
        "heart": {"running": True},
        "field": {"recommended_action": "observe"},
        "latest_capsule": {"capsule_id": "cap"},
    }
    seeds = build_shell_candidate_seeds(bundle)

    candidates = build_shell_route_candidates(seeds, now=120.0)

    assert len(candidates) == 3
    for candidate in candidates:
        assert candidate.operation == "route-evaluate"
        assert candidate.warrant.metadata["execution_authority"] == "NONE"
        assert candidate.metadata["routing_evaluation_only"] is True

        route_auth = candidate.warrant.authorize(
            actor_id=candidate.actor_id,
            operation=candidate.operation,
            target=candidate.target,
            now=120.0,
        )
        execute_auth = candidate.warrant.authorize(
            actor_id=candidate.actor_id,
            operation="execute",
            target="tool/python",
            now=120.0,
        )

        assert route_auth.allowed is True
        assert execute_auth.allowed is False


def test_constitutional_route_json_runs_real_shadow_path(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "route",
            "I need momentum to create and start this draft",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["shell_mode"] == "SHADOW"
    assert payload["persisted_state_present"] is False
    assert payload["disposition"] == "LEGACY_SHADOW"
    assert payload["legacy_reply"]["coach"] == "Flow"
    assert payload["legacy_route_key"] == "route:flow"
    assert payload["vnext_receipt"]["selected_route_key"] == "route:flow"
    assert payload["shadow_comparison"]["comparison_state"] == "AGREE"
    assert payload["execution_licensed"] is False
    assert payload["steering_route_key"] is None
    assert payload["authority_change"] == "NONE"
    assert len(payload["candidate_seeds"]) == 3


def test_constitutional_route_text_output_is_explicitly_shadow(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "constitutional",
            "route",
            "Help me reflect on the pattern and meaning here",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert ":: PHIKERNEL CONSTITUTIONAL ROUTE ::" in captured.out
    assert "Mode: SHADOW" in captured.out
    assert "Legacy Coach: Sage" in captured.out
    assert "vNext Route: route:sage" in captured.out
    assert "Execution Licensed: False" in captured.out


def test_legacy_route_command_remains_legacy_and_unchanged(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "route",
            "I need momentum to create and start this draft",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["coach"] == "Flow"
    assert "shell_mode" not in payload
    assert "shadow_comparison" not in payload


def test_quarantine_blocks_constitutional_route_before_shadow_routing(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)
    quarantine_runtime(
        runtime_paths.runtime_root,
        operator_note="contain fixture",
    )

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "route",
            "create momentum",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["disposition"] == "RUNTIME_BLOCKED"
    assert payload["mode_before"] == "SHADOW"
    assert payload["mode_after"] == "SHADOW"
    assert payload["legacy_reply"] is None
    assert payload["vnext_receipt"] is None
    assert payload["execution_licensed"] is False
    assert "quarantined" in payload["reason"]


def test_seal_blocks_constitutional_route_before_shadow_routing(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)
    seal_runtime(
        runtime_paths.runtime_root,
        operator_note="seal fixture",
    )

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "route",
            "reflect on this",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["disposition"] == "RUNTIME_BLOCKED"
    assert payload["legacy_reply"] is None
    assert payload["shadow_comparison"] is None
    assert "sealed" in payload["reason"]


def test_constitutional_shell_never_exposes_advise_or_bounded_control_flag(
    initialized_shell,
    runtime_paths: RuntimePaths,
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)

    parser = shell._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "constitutional",
                "route",
                "--mode",
                "ADVISE",
                "create momentum",
            ]
        )


def test_constitutional_status_without_snapshot_reports_genesis_shadow(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "status",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["persisted_state_present"] is False
    assert payload["mode"] == SHADOW
    assert payload["revision"] == 0
    assert payload["history_count"] == 0
    assert payload["control"] is None


def test_persisted_advise_resumes_advisory_shell_routing(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)
    _persist_advise(shell)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "route",
            "Help me reflect on the pattern and meaning here",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["persisted_state_present"] is True
    assert payload["shell_mode"] == ADVISE
    assert payload["mode_before"] == ADVISE
    assert payload["mode_after"] == ADVISE
    assert payload["disposition"] == "LEGACY_WITH_ADVICE"
    assert payload["legacy_reply"]["coach"] == "Sage"
    assert payload["advisory_route_key"] == "route:sage"
    assert payload["steering_route_key"] is None
    assert payload["execution_licensed"] is False
    assert payload["automatic_collapse_persisted"] is False


def test_constitutional_status_reports_persisted_advise_lineage(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)
    state = _persist_advise(shell)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "status",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["persisted_state_present"] is True
    assert payload["mode"] == ADVISE
    assert payload["revision"] == 1
    assert payload["authorized_by_seal_id"] == state.promotion_state.authorized_by_seal_id
    assert payload["advise_receipt_id"] == state.advise_receipt.receipt_id
    assert payload["history_count"] == 1


def test_persisted_bounded_control_is_visible_but_route_does_not_steer(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)
    state = _persist_bounded(shell)
    before_remaining = state.control_session.warrant.remaining("compute_ms")

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "route",
            "I need momentum to create and start this draft",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    loaded = shell.constitutional_store.load()

    assert exit_code == 0
    assert payload["shell_mode"] == BOUNDED_CONTROL
    assert payload["mode_before"] == BOUNDED_CONTROL
    assert payload["mode_after"] == BOUNDED_CONTROL
    assert payload["disposition"] == "BOUNDED_UNAVAILABLE"
    assert payload["execution_licensed"] is False
    assert payload["steering_route_key"] is None
    assert payload["automatic_collapse_persisted"] is False
    assert loaded.control_session.warrant.remaining("compute_ms") == pytest.approx(
        before_remaining
    )
    assert loaded.control_session.actions_used == 0
    assert loaded.control_session.clock_ticks_used == pytest.approx(0.0)


def test_constitutional_status_reports_bounded_session_accounting(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)
    state = _persist_bounded(shell)

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "status",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["mode"] == BOUNDED_CONTROL
    assert payload["revision"] == 2
    assert payload["history_count"] == 2
    assert payload["control"]["grant_id"] == state.control_grant.grant_id
    assert payload["control"]["session_id"] == state.control_session.session_id
    assert payload["control"]["actions_used"] == 0
    assert payload["control"]["clock_ticks_used"] == pytest.approx(0.0)


def test_quarantine_collapses_persisted_bounded_control_and_writes_downgrade(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)
    _persist_bounded(shell)
    quarantine_runtime(
        runtime_paths.runtime_root,
        operator_note="contain bounded fixture",
    )

    exit_code = shell.run(
        [
            "--runtime-root",
            str(runtime_paths.runtime_root),
            "--json",
            "constitutional",
            "route",
            "create momentum",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    loaded = shell.constitutional_store.load()

    assert exit_code == 0
    assert payload["disposition"] == "RUNTIME_BLOCKED"
    assert payload["mode_before"] == BOUNDED_CONTROL
    assert payload["mode_after"] == SHADOW
    assert payload["execution_licensed"] is False
    assert payload["automatic_collapse_persisted"] is True
    assert payload["persisted_snapshot_hash_after"]
    assert loaded.promotion_state.mode == SHADOW
    assert loaded.promotion_state.revision == 3
    assert loaded.collapse_receipt is not None
    assert loaded.control_grant is None
    assert loaded.control_session is None
    assert len(shell.constitutional_store.history()) == 3


def test_corrupt_persisted_constitutional_state_fails_shell_closed(
    initialized_shell,
    runtime_paths: RuntimePaths,
    capsys: pytest.CaptureFixture[str],
) -> None:
    shell = _ready_shell(initialized_shell, runtime_paths)
    _persist_advise(shell)

    record = json.loads(
        shell.constitutional_store.state_file.read_text(encoding="utf-8")
    )
    record["payload"]["promotion_state"]["revision"] = 99
    shell.constitutional_store.state_file.write_text(
        json.dumps(record),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        shell.run(
            [
                "--runtime-root",
                str(runtime_paths.runtime_root),
                "constitutional",
                "status",
            ]
        )
    captured = capsys.readouterr()

    assert "Constitutional state failed validation" in captured.err
