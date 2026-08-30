import json

from service.agent.view_intelligence import _normalize_graph, apply_view_graph


def test_normalize_view_graph_uses_authoritative_canonical_camera_frame():
    graph = _normalize_graph(
        {
            "projection_method": "third_angle",
            "views": [
                {
                    "id": "main",
                    "type": "front",
                    "bbox_normalized": [0.1, 0.2, 0.7, 0.8],
                    "matched_projection_id": "front",
                    "observation_direction": [1, 1, 1],
                    "x_direction": [0, 1, 0],
                    "confidence": 1.5,
                    "evidence": "same outer contour",
                }
            ],
        },
        "vision-model",
        "multimodal_model",
    )

    assert graph["views"][0]["id"] == "MAIN"
    assert graph["views"][0]["observation_direction"] == [0.0, -1.0, 0.0]
    assert graph["views"][0]["x_direction"] == [1.0, 0.0, 0.0]
    assert graph["views"][0]["confidence"] == 1.0


def test_apply_view_graph_binds_entities_and_persists_auditable_summary(tmp_path):
    plan = {
        "drawing_entities": [
            {
                "id": "D2-001",
                "raw_text": "10",
                "semantic_type": "linear_dimension",
                "nominal": 10.0,
                "quantity": 1,
                "anchor_pdf": [50, 50],
            },
            {
                "id": "D2-002",
                "raw_text": "20",
                "semantic_type": "linear_dimension",
                "nominal": 20.0,
                "quantity": 1,
                "anchor_pdf": [60, 60],
            },
            {
                "id": "D2-003",
                "raw_text": "30",
                "semantic_type": "linear_dimension",
                "nominal": 30.0,
                "quantity": 1,
                "anchor_pdf": [70, 70],
            },
        ],
        "measurements": [],
    }
    vector = {"page": {"width": 100, "height": 100}}
    analysis = {
        "measurements": {"bounding_box": {"min": [0, 0, 0], "max": [100, 25, 100], "size": [100, 25, 100]}},
        "linear_edge_features": [
            {"id": "L10", "length": 10, "center": [50, 0, 50], "direction": [1, 0, 0]},
            {"id": "L20", "length": 20, "center": [60, 0, 40], "direction": [1, 0, 0]},
            {"id": "L30", "length": 30, "center": [70, 0, 30], "direction": [1, 0, 0]},
        ],
    }
    comparison = {"result": "not_evaluated", "features": [], "manufacturing_specification": {"summary": {"matched": 0}}}
    graph = {
        "source": "multimodal_model",
        "model": "vision-model",
        "views": [
            {
                "id": "VIEW_01",
                "type": "front",
                "bbox_normalized": [0, 0, 1, 1],
                "matched_projection_id": "front",
                "observation_direction": [0, -1, 0],
                "x_direction": [1, 0, 0],
                "confidence": 0.9,
            }
        ],
    }
    for name, payload in (
        ("measurement_plan.json", plan),
        ("vector_extraction.json", vector),
        ("analysis.json", analysis),
        ("comparison.json", comparison),
    ):
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    updated = apply_view_graph(tmp_path, graph)

    assert updated["view_intelligence"]["assigned_entities"] == 3
    assert updated["drawing_entities"][0]["status"] == "ai_view_bound"
    assert updated["drawing_entities"][0]["matched_projection_id"] == "front"
    registration = updated["drawing_entities"][0]["view_registration"]
    assert registration["method"] == "numeric_landmark_affine"
    assert registration["inlier_count"] == 3
    assert registration["rmse_normalized"] == 0.0
    assert (tmp_path / "manufacturing_specification.json").is_file()
