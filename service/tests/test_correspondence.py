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


def test_linear_edge_and_bend_radius_are_matched_deterministically():
    plan = {
        "drawing_entities": [
            {"id": "D2-001", "semantic_type": "linear_dimension", "nominal": 17.2, "quantity": 1, "view_id": "VIEW_MAIN", "status": "bound"},
            {"id": "D2-002", "semantic_type": "radius", "nominal": 20.0, "quantity": 1, "view_id": "VIEW_MAIN", "status": "bound"},
        ],
        "measurements": [],
    }
    analysis = {
        "measurements": {"bounding_box": {"min": [0, 0, 0], "max": [42.4, 4.85, 25.3], "size": [42.4, 4.85, 25.3]}},
        "linear_edge_features": [
            {"id": "E0001", "length": 17.2, "start": [0, 0, 0], "end": [17.2, 0, 0], "center": [8.6, 0, 0], "direction": [1, 0, 0]}
        ],
        "radius_pair_analysis": {
            "bend_groups": [{"radius": 20.0, "axis": "X", "axis_vector": [1, 0, 0], "center": [0, 4.7, 30], "side": "OUTER", "faces": 3}]
        },
    }

    specification = build_manufacturing_specification(plan, analysis, {"features": []})

    assert specification["summary"] == {"drawing_entities": 2, "matched": 2, "ambiguous": 0, "unmapped": 0}
    assert specification["mappings"][0]["cad_feature_ids"] == ["E0001"]
    assert specification["mappings"][1]["cad_feature_ids"] == ["BEND-R001"]
    assert specification["cad_features"][-1]["center"] == [0.0, 4.7, 30.0]


def test_duplicate_exact_linear_candidates_remain_ambiguous():
    plan = {
        "drawing_entities": [{"id": "D2-001", "semantic_type": "linear_dimension", "nominal": 5.4, "quantity": 1}],
        "measurements": [],
    }
    analysis = {
        "linear_edge_features": [
            {"id": "E1", "length": 5.4, "start": [0, 0, 0], "end": [5.4, 0, 0]},
            {"id": "E2", "length": 5.4, "start": [0, 1, 0], "end": [5.4, 1, 0]},
        ]
    }

    specification = build_manufacturing_specification(plan, analysis, {"features": []})

    assert specification["mappings"][0]["status"] == "ambiguous"
    assert specification["mappings"][0]["cad_feature_ids"] == ["E1", "E2"]


def test_unique_numeric_candidate_without_drawing_context_is_provisional():
    plan = {
        "drawing_entities": [
            {
                "id": "D2-001",
                "semantic_type": "linear_dimension",
                "nominal": 17.2,
                "quantity": 1,
                "view_id": "PAGE_1_UNASSIGNED",
                "status": "unbound",
            }
        ],
        "measurements": [],
    }
    analysis = {
        "linear_edge_features": [
            {"id": "E1", "length": 17.2, "start": [0, 0, 0], "end": [17.2, 0, 0]}
        ]
    }

    specification = build_manufacturing_specification(plan, analysis, {"features": []})

    mapping = specification["mappings"][0]
    assert mapping["status"] == "matched"
    assert mapping["verification_status"] == "provisional_unique"
    assert mapping["cad_feature_ids"] == ["E1"]
    assert mapping["confidence"] < mapping["candidates"][0]["score"]
    assert specification["comparison_rows"][0]["verification_status"] == "provisional_unique"
    assert specification["comparison_rows"][0]["result"] == "not_evaluated"


def test_small_nominal_difference_is_a_candidate_but_not_a_pass_without_tolerance():
    plan = {
        "drawing_entities": [
            {"id": "D2-001", "semantic_type": "linear_dimension", "nominal": 16.0, "quantity": 1}
        ],
        "measurements": [],
    }
    analysis = {
        "plane_distance_features": [
            {"id": "P1", "distance": 15.96, "start": [0, 0, 0], "end": [15.96, 0, 0]}
        ]
    }

    specification = build_manufacturing_specification(plan, analysis, {"features": []})

    row = specification["comparison_rows"][0]
    assert row["mapping_status"] == "matched"
    assert row["verification_status"] == "provisional_unique"
    assert row["measured_values"] == [15.96]
    assert row["result"] == "not_evaluated"
