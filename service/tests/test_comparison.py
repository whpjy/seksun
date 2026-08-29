import json
from pathlib import Path

from fastapi.testclient import TestClient

from service import main
from service.comparison import compare_c10_from_analysis


def measurement_plan() -> dict:
    return {
        "measurements": [
            {
                "id": "C10",
                "type": "hole_diameter",
                "nominal": 6.5,
                "tolerance": {"upper": 0.1, "lower": -0.1},
                "quantity": 6,
                "source": {"view": "VIEW_MAIN", "label": "10", "raw_text": "6x Ø6.5 ±0.1"},
            }
        ]
    }


def vector_extraction() -> dict:
    centers = [(0, 0), (41, 0), (85, 0), (0, -40), (32, -40), (85, -40)]
    return {
        "vector_features": [
            {
                "id": "HOLES",
                "members": [
                    {"id": f"PDF-{index + 1}", "relative_center_mm": list(center)}
                    for index, center in enumerate(centers)
                ],
            }
        ],
        "annotation_bindings": [
            {"measurement_id": "C10", "target_feature_id": "HOLES"}
        ],
    }


def step_analysis() -> dict:
    centers_and_diameters = [
        ((73, -20, 0), 6.5),
        ((32, -20, 0), 6.5),
        ((-12, -20, 0), 6.5),
        ((73, 20, 0), 6.5),
        ((41, 20, 0), 6.5),
        ((-12, 20, 0), 7.0),
    ]
    return {
        "axial_features": [
            {
                "id": f"STEP-{index + 1}",
                "center": list(center),
                "axis": [0, 0, 1],
                "segments": [
                    {
                        "face_id": f"FACE-{index + 1}-A",
                        "diameter": diameter,
                        "internal": True,
                    },
                    {
                        "face_id": f"FACE-{index + 1}-B",
                        "diameter": diameter,
                        "internal": True,
                    },
                ],
            }
            for index, (center, diameter) in enumerate(centers_and_diameters)
        ]
    }


def test_c10_pattern_registration_finds_out_of_tolerance_hole():
    result = compare_c10_from_analysis(
        measurement_plan(), vector_extraction(), step_analysis()
    )

    assert result["result"] == "fail"
    assert result["summary"] == {"matched": 6, "passed": 5, "failed": 1}
    assert result["registration"]["maximum_residual_mm"] == 0
    failed = [feature for feature in result["features"] if feature["result"] == "fail"]
    assert len(failed) == 1
    assert failed[0]["pdf_feature_id"] == "PDF-6"
    assert failed[0]["actual_diameter"] == 7.0
    assert failed[0]["step_source_ids"] == ["FACE-6-A", "FACE-6-B"]


def test_comparison_upload_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE_ROOT", tmp_path)

    def fake_comparison(directory: Path, pdf_path: Path, step_path: Path) -> dict:
        assert pdf_path.read_bytes() == b"vector-pdf"
        assert step_path.read_bytes() == b"ISO-10303-21"
        (directory / "analysis.json").write_text("{}", encoding="utf-8")
        views = directory / "views"
        views.mkdir()
        for name in ("front.svg", "top.svg", "right.svg", "three_views.svg"):
            (views / name).write_text("<svg/>", encoding="utf-8")
        (views / "views.json").write_text("{}", encoding="utf-8")
        result = {"measurement_id": "C10", "result": "fail"}
        (directory / "comparison.json").write_text(json.dumps(result), encoding="utf-8")
        return result

    monkeypatch.setattr(main, "process_pdf_step_comparison", fake_comparison)
    with TestClient(main.app) as client:
        response = client.post(
            "/api/v1/comparisons",
            files={
                "pdf": ("drawing.pdf", b"vector-pdf", "application/pdf"),
                "step": ("model.stp", b"ISO-10303-21", "application/octet-stream"),
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["comparison"]["result"] == "fail"
        comparison = client.get(payload["results"]["comparison"])
        assert comparison.status_code == 200
        assert comparison.json()["measurement_id"] == "C10"
        history = client.get("/api/v1/comparisons")
        assert history.status_code == 200
        assert history.json()["items"][0]["id"] == payload["id"]
        restored = client.get(f"/api/v1/comparisons/{payload['id']}")
        assert restored.status_code == 200
        assert restored.json()["comparison"]["result"] == "fail"
        assert client.get(restored.json()["inputs"]["pdf"]).content == b"vector-pdf"
        assert client.get(restored.json()["inputs"]["step"]).content == b"ISO-10303-21"
