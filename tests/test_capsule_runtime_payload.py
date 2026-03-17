import pytest

from phikernel.capsule import ContinuityCapsuleError, export_result_payload, import_result_payload


def test_export_import_runtime_result_payload_roundtrip() -> None:
    payload = {
        "engine": "phikernel",
        "adapter": "tiekat_v50",
        "verdict": "OVERSOUL_UNREADY",
        "debug": {"total_sessions": 1},
    }

    encoded = export_result_payload(payload)
    decoded = import_result_payload(encoded)

    assert decoded == payload


def test_import_rejects_invalid_json() -> None:
    with pytest.raises(ContinuityCapsuleError):
        import_result_payload("not-json")
