from phikernel.tiekat_v69_runtime import build_runtime_face_state, runtime_field_summary


def test_runtime_face_state_exposes_12_faces_and_derived_metrics() -> None:
    field = build_runtime_face_state(
        {
            "anchor_valid": True,
            "coherence_score": 0.82,
            "stability_score": 0.79,
            "risk_score": 0.12,
            "contamination_load": 0.08,
            "edge_flow_score": 0.71,
            "recovery_vertex_score": 0.74,
        }
    )

    assert len(field.face_scores) == 12
    assert field.weakest_face in field.face_scores
    assert 0.0 <= field.field_average <= 1.0
    assert 0.0 <= field.field_variance <= 1.0


def test_runtime_field_summary_roundtrip_contract() -> None:
    summary = runtime_field_summary({"coherence_score": 0.75, "risk_score": 0.20})

    assert "face_scores" in summary
    assert summary["weakest_face"] in summary["face_scores"]
    assert "contamination_load" in summary
