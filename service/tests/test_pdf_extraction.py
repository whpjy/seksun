import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from service import main
from service.pdf_extraction import (
    PDF_POINTS_PER_MM,
    extract_c10_from_pdf,
    extract_drawing_entities,
)


def _circle_commands(center_x: float, center_y: float, radius: float) -> str:
    control = radius * 0.5522847498
    return " ".join(
        [
            f"{center_x + radius} {center_y} m",
            f"{center_x + radius} {center_y + control} {center_x + control} {center_y + radius} {center_x} {center_y + radius} c",
            f"{center_x - control} {center_y + radius} {center_x - radius} {center_y + control} {center_x - radius} {center_y} c",
            f"{center_x - radius} {center_y - control} {center_x - control} {center_y - radius} {center_x} {center_y - radius} c",
            f"{center_x + control} {center_y - radius} {center_x + radius} {center_y - control} {center_x + radius} {center_y} c S",
        ]
    )


def _write_vector_pdf(path: Path) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=1196.22, height=847.56)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    scale = PDF_POINTS_PER_MM * 2
    radius = 6.5 * scale / 2
    centers_mm = [(0, 0), (41, 0), (85, 0), (0, -40), (32, -40), (85, -40)]
    commands = [
        "BT /F1 10 Tf 1 0 0 1 206 695 Tm (\\(6x\\)6.5 +/-0.1) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 242 708 Tm (10) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 367 787 Tm (2:1) Tj ET",
    ]
    commands.extend(
        _circle_commands(150 + x * scale, 659 + y * scale, radius)
        for x, y in centers_mm
    )
    content = DecodedStreamObject()
    content.set_data(("\n".join(commands) + "\n").encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


def test_extracts_c10_from_raw_vector_pdf(tmp_path):
    pdf = tmp_path / "drawing.pdf"
    _write_vector_pdf(pdf)

    plan, vector, diagnostics = extract_c10_from_pdf(pdf)

    measurement = plan["measurements"][0]
    assert measurement["id"] == "C10"
    assert measurement["nominal"] == 6.5
    assert measurement["tolerance"] == {"upper": 0.1, "lower": -0.1}
    assert measurement["quantity"] == 6
    assert measurement["source"]["label"] == "10"

    calibration = vector["view_calibration"]
    assert calibration["drawing_scale"] == 2
    feature = vector["vector_features"][0]
    assert feature["quantity"] == 6
    assert feature["diameter_mm"] == 6.5
    relative_centers = [member["relative_center_mm"] for member in feature["members"]]
    expected_centers = [[0, 0], [41, 0], [85, 0], [0, -40], [32, -40], [85, -40]]
    for actual, expected in zip(relative_centers, expected_centers):
        assert actual == pytest.approx(expected, abs=1e-5)
    assert diagnostics["matched_circle_count"] == 6
    entities, entity_diagnostics = extract_drawing_entities(pdf)
    repeated_hole = next(item for item in entities if item["semantic_type"] == "diameter")
    assert repeated_hole["nominal"] == 6.5
    assert repeated_hole["quantity"] == 6
    assert repeated_hole["tolerance"] == {"upper": 0.1, "lower": -0.1}
    assert entity_diagnostics["entity_count"] >= 1


def test_comparison_process_uses_raw_pdf_without_sidecar(tmp_path, monkeypatch):
    pdf = tmp_path / "drawing.pdf"
    step = tmp_path / "model.stp"
    output = tmp_path / "job"
    output.mkdir()
    _write_vector_pdf(pdf)
    step.write_bytes(b"ISO-10303-21")

    def fake_process_step(directory: Path, _source: Path) -> None:
        centers_and_diameters = [
            ((73, -20, 0), 6.5),
            ((32, -20, 0), 6.5),
            ((-12, -20, 0), 6.5),
            ((73, 20, 0), 6.5),
            ((41, 20, 0), 6.5),
            ((-12, 20, 0), 7.0),
        ]
        analysis = {
            "axial_features": [
                {
                    "id": f"STEP-{index + 1}",
                    "center": list(center),
                    "axis": [0, 0, 1],
                    "segments": [
                        {
                            "face_id": f"FACE-{index + 1}",
                            "diameter": diameter,
                            "internal": True,
                        }
                    ],
                }
                for index, (center, diameter) in enumerate(centers_and_diameters)
            ]
        }
        (directory / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")

    monkeypatch.setattr(main, "process_step", fake_process_step)
    result = main.process_pdf_step_comparison(output, pdf, step)

    assert result["summary"] == {"matched": 6, "passed": 5, "failed": 1}
    assert result["result"] == "fail"
    for name in (
        "measurement_plan.json",
        "vector_extraction.json",
        "pdf_extraction_diagnostics.json",
        "comparison.json",
        "manufacturing_specification.json",
    ):
        assert (output / name).is_file()
    assert result["manufacturing_specification"]["summary"]["matched"] >= 1


def test_unsupported_drawing_enters_requirement_discovery(tmp_path, monkeypatch):
    pdf = tmp_path / "different-drawing.pdf"
    step = tmp_path / "model.step"
    output = tmp_path / "discovery-job"
    output.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=800, height=600)
    with pdf.open("wb") as stream:
        writer.write(stream)
    step.write_bytes(b"ISO-10303-21")

    def fake_process_step(directory: Path, _source: Path) -> None:
        (directory / "analysis.json").write_text(
            json.dumps({"axial_features": [], "hole_axis_groups": []}),
            encoding="utf-8",
        )

    monkeypatch.setattr(main, "process_step", fake_process_step)
    result = main.process_pdf_step_comparison(output, pdf, step)

    assert result["result"] == "not_evaluated"
    assert result["discovery"]["status"] == "needs_review"
    plan = json.loads((output / "measurement_plan.json").read_text(encoding="utf-8"))
    assert plan["scope"]["mode"] == "requirement_discovery"
    assert plan["measurements"] == []
