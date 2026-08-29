from service.correspondence import build_manufacturing_specification


def test_near_tie_candidates_are_retained_for_review():
    plan = {
        "drawing_entities": [
            {
                "id": "D2-001",
                "semantic_type": "diameter",
                "nominal": 8.0,
                "quantity": 1,
                "tolerance": {"lower": -0.1, "upper": 0.1},
                "diameter_symbol_present": True,
            }
        ],
        "measurements": [],
    }
    analysis = {
        "axial_features": [
            {
                "id": f"H{index}",
                "center": [index * 20, 0, 0],
                "axis": [0, 0, 1],
                "segments": [
                    {"face_id": f"F{index}", "diameter": 8.0, "internal": True}
                ],
            }
            for index in (1, 2)
        ]
    }

    specification = build_manufacturing_specification(plan, analysis, {"features": []})

    mapping = specification["mappings"][0]
    assert mapping["status"] == "ambiguous"
    assert mapping["cad_feature_ids"] == ["H1:1", "H2:1"]
    assert specification["summary"] == {
        "drawing_entities": 1,
        "matched": 0,
        "ambiguous": 1,
        "unmapped": 0,
    }
