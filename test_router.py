import json
from pathlib import Path

import pytest

from phik_router_v0_1_0 import (
    CoachReply,
    CoachRouter,
    RouterError,
    load_bundle_from_args,
    main,
    render_reply,
)


@pytest.fixture
def router() -> CoachRouter:
    return CoachRouter()



def _bundle(
    *,
    prompt: str = "",
    anchor_valid: bool = True,
    field_action: str = "observe",
    field_band: str = "stable",
    heart_running: bool = True,
    latest_capsule: dict | None = None,
) -> dict:
    return {
        "shell_version": "0.1.1",
        "prompt": prompt,
        "anchor": {
            "anchor_id": "anchor-test",
            "sovereign_name": "Tal-Aren-Vox",
            "user_label": "Ori",
            "verification": {
                "valid": anchor_valid,
                "reason": "Anchor manifest verified successfully" if anchor_valid else "Signature verification failed",
            },
        },
        "heart": {
            "running": heart_running,
            "registered_jobs": 3,
        },
        "field": {
            "anchor_id": "anchor-test",
            "recommended_action": field_action,
            "drift_band": field_band,
            "C_current": 0.801,
            "distance_to_C_star": 0.008,
        },
        "latest_capsule": latest_capsule,
        "generated_at": 123.456,
        "next_hint": "Field is stable. Continue with orchestration or shell workflows.",
    }



def test_alert_state_always_routes_to_titan(router: CoachRouter) -> None:
    """PROVE: Critical field states override prompt keywords and force Titan."""
    bundle = _bundle(
        prompt="I want flow and momentum right now",
        anchor_valid=True,
        field_action="alert",
        field_band="critical",
        heart_running=True,
        latest_capsule={"capsule_id": "cap-1", "summary": "latest"},
    )

    reply = router.route(bundle)

    assert isinstance(reply, CoachReply)
    assert reply.coach == "Titan"
    assert reply.safe_to_proceed is False
    assert reply.field_action == "alert"
    assert reply.field_band == "critical"
    assert "containment" in reply.route_reason.lower() or "trust restoration" in reply.route_reason.lower()



def test_restore_state_always_routes_to_titan(router: CoachRouter) -> None:
    """PROVE: Restore conditions force grounded recovery before specialization."""
    bundle = _bundle(
        prompt="please help me reflect on the pattern",
        anchor_valid=True,
        field_action="restore",
        field_band="restore",
        heart_running=True,
        latest_capsule={"capsule_id": "cap-2", "summary": "checkpoint"},
    )

    reply = router.route(bundle)

    assert reply.coach == "Titan"
    assert reply.safe_to_proceed is False
    assert reply.field_action == "restore"
    assert any("Restore the latest known-good capsule." == step for step in reply.next_actions)



def test_invalid_anchor_triggers_titan_override(router: CoachRouter) -> None:
    """PROVE: Anchor trust failure blocks unsafe coach expansion."""
    bundle = _bundle(
        prompt="help me create a new plan",
        anchor_valid=False,
        field_action="observe",
        field_band="stable",
        heart_running=True,
        latest_capsule={"capsule_id": "cap-3", "summary": "stable"},
    )

    reply = router.route(bundle)

    assert reply.coach == "Titan"
    assert reply.safe_to_proceed is False
    assert reply.field_action == "alert"
    assert reply.field_band == "critical"
    assert "anchor trust is invalid" in reply.route_reason.lower()



def test_flow_keyword_routes_when_field_is_safe(router: CoachRouter) -> None:
    """PROVE: Movement / creative prompts route to Flow in a healthy field."""
    bundle = _bundle(
        prompt="I need momentum to create and start this draft",
        anchor_valid=True,
        field_action="observe",
        field_band="stable",
        heart_running=True,
        latest_capsule={"capsule_id": "cap-4", "summary": "working capsule"},
    )

    reply = router.route(bundle)

    assert reply.coach == "Flow"
    assert reply.safe_to_proceed is True
    assert "movement" in reply.route_reason.lower() or "momentum" in reply.route_reason.lower()
    assert "Momentum is available" in reply.body



def test_sage_keyword_routes_when_field_is_safe(router: CoachRouter) -> None:
    """PROVE: Reflection / interpretation prompts route to Sage in a healthy field."""
    bundle = _bundle(
        prompt="Can you help me understand the pattern and reflect on its meaning?",
        anchor_valid=True,
        field_action="observe",
        field_band="stable",
        heart_running=True,
        latest_capsule={"capsule_id": "cap-5", "summary": "pattern log"},
    )

    reply = router.route(bundle)

    assert reply.coach == "Sage"
    assert reply.safe_to_proceed is True
    assert "pattern" in reply.route_reason.lower() or "reflection" in reply.route_reason.lower()
    assert "reading the pattern" in reply.body.lower() or "identify the strongest pattern" in reply.body.lower()



def test_missing_heart_defaults_to_titan(router: CoachRouter) -> None:
    """PROVE: If pulse is offline, the router falls back to Titan."""
    bundle = _bundle(
        prompt="I want to build momentum",
        anchor_valid=True,
        field_action="observe",
        field_band="stable",
        heart_running=False,
        latest_capsule={"capsule_id": "cap-6", "summary": "stable"},
    )

    reply = router.route(bundle)

    assert reply.coach == "Titan"
    assert reply.safe_to_proceed is True
    assert "pulse is offline" in reply.route_reason.lower()



def test_missing_capsule_defaults_to_titan(router: CoachRouter) -> None:
    """PROVE: No continuity capsule means Titan establishes grounding first."""
    bundle = _bundle(
        prompt="help me move forward",
        anchor_valid=True,
        field_action="observe",
        field_band="stable",
        heart_running=True,
        latest_capsule=None,
    )

    reply = router.route(bundle)

    assert reply.coach == "Titan"
    assert reply.safe_to_proceed is True
    assert "no continuity capsule exists yet" in reply.route_reason.lower()



def test_bundle_fields_are_consumed_without_hallucinating(router: CoachRouter) -> None:
    """PROVE: The reply trace reflects real bundle state rather than invented values."""
    bundle = _bundle(
        prompt="steady help tonight",
        anchor_valid=True,
        field_action="checkpoint",
        field_band="warning",
        heart_running=True,
        latest_capsule={"capsule_id": "cap-7", "summary": "night check-in"},
    )

    reply = router.route(bundle)
    record = reply.to_record()

    assert record["trace"]["anchor_valid"] is True
    assert record["trace"]["heart_running"] is True
    assert record["trace"]["has_latest_capsule"] is True
    assert record["field_action"] == "checkpoint"
    assert record["field_band"] == "warning"
    assert record["coach"] == "Titan"



def test_render_reply_outputs_human_readable_portrait(router: CoachRouter) -> None:
    """PROVE: The router can render a clean terminal reply for the shell."""
    reply = router.route(
        _bundle(
            prompt="I need help settling my mind tonight",
            latest_capsule={"capsule_id": "cap-8", "summary": "evening state"},
        )
    )

    rendered = render_reply(reply)

    assert ":: PHIK ROUTER ::" in rendered
    assert f"Coach: {reply.coach}" in rendered
    assert reply.opening in rendered
    assert reply.body in rendered



def test_load_bundle_from_inline_json() -> None:
    """PROVE: Inline JSON bundle loading works cleanly."""
    bundle = _bundle(prompt="inline")
    args = type("Args", (), {"bundle_file": None, "bundle_json": json.dumps(bundle)})()

    loaded = load_bundle_from_args(args)

    assert loaded["prompt"] == "inline"
    assert loaded["anchor"]["anchor_id"] == "anchor-test"



def test_load_bundle_prefers_file_when_provided(tmp_path: Path) -> None:
    """PROVE: File-based bundle loading works cleanly."""
    bundle = _bundle(prompt="from file")
    bundle_file = tmp_path / "bundle.json"
    bundle_file.write_text(json.dumps(bundle), encoding="utf-8")

    args = type("Args", (), {"bundle_file": str(bundle_file), "bundle_json": None})()
    loaded = load_bundle_from_args(args)

    assert loaded["prompt"] == "from file"



def test_load_bundle_rejects_non_object_json() -> None:
    """PROVE: The router refuses malformed bundle payloads."""
    args = type("Args", (), {"bundle_file": None, "bundle_json": json.dumps([1, 2, 3])})()

    with pytest.raises(RouterError):
        load_bundle_from_args(args)



def test_cli_accepts_bundle_json(capsys: pytest.CaptureFixture[str]) -> None:
    """PROVE: The CLI emits a valid JSON reply from inline bundle input."""
    bundle = _bundle(
        prompt="I need momentum to start",
        latest_capsule={"capsule_id": "cap-9", "summary": "ready"},
    )

    exit_code = main(["--bundle-json", json.dumps(bundle), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["coach"] in {"Titan", "Flow", "Sage"}
    assert payload["route_reason"]
    assert payload["field_action"] in {"observe", "checkpoint", "restore", "alert"}



def test_cli_reads_bundle_from_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """PROVE: The CLI can read a think bundle from stdin as piped shell input."""
    bundle = _bundle(
        prompt="How is the field? help me understand the pattern",
        latest_capsule={"capsule_id": "cap-10", "summary": "field snapshot"},
    )
    monkeypatch.setattr("sys.stdin.read", lambda: json.dumps(bundle))

    exit_code = main(["--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["coach"] in {"Titan", "Sage"}
    assert payload["trace"]["has_latest_capsule"] is True



def test_cli_rejects_missing_bundle(capsys: pytest.CaptureFixture[str]) -> None:
    """PROVE: Empty CLI input fails honestly through argparse semantics."""
    with pytest.raises(SystemExit) as excinfo:
        main([])

    captured = capsys.readouterr()
    assert excinfo.value.code == 2
    assert "No think bundle provided" in captured.err
