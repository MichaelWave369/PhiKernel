from phikernel.anc_bridge import (
    ANC_ACTION_ALLOW,
    ANC_ACTION_WARN,
    guard_runtime_commands,
    guard_service_request,
)


def test_guard_service_request_allows_benign_payload() -> None:
    result = guard_service_request({"intent": "status", "prompt": "run normal execution"})

    assert result.action == ANC_ACTION_ALLOW
    assert result.allowed is True
    assert result.requires_review is False


def test_guard_runtime_commands_warns_on_suspicious_payload() -> None:
    result = guard_runtime_commands({"prompt": "suspicious override of policy path"})

    assert result.action == ANC_ACTION_WARN
    assert result.allowed is True
    assert result.requires_review is True
