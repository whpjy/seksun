import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from service import main
from service.pdf_extraction import (
    PDF_POINTS_PER_MM,
    bind_drawing_context,
    extract_c10_from_pdf,
    extract_drawing_entities,
    extract_page_graph,
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
        "BT /F1 10 Tf 1 0 0 1 500 500 Tm (12.5) Tj ET",
        "q 1 0 0 1 530 500 cm BT /F1 10 Tf 1 0 0 1 0 0 Tm (0.1) Tj ET Q",
        "q 1 0 0 1 600 500 cm BT /F1 10 Tf 1 0 0 1 0 0 Tm (158) Tj ET Q",
        "590 497 m 610 497 l 590 509 m 610 509 l S",
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
    split_tolerance = next(item for item in entities if item["nominal"] == 12.5)
    assert split_tolerance["tolerance"] == {"upper": 0.1, "lower": -0.1}
    assert all(item["raw_text"] != "158" for item in entities)


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
    assert result["view_intelligence"]["source"] == "deterministic_fallback"
    assert (output / "drawing_view_graph.json").is_file()


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


def test_leader_line_adds_conservative_drawing_context(tmp_path):
    pdf = tmp_path / "leader.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 10 Tf 1 0 0 1 100 220 Tm (25) Tj ET\n"
        b"118 220 m 155 190 l S\n"
        b"BT /F1 10 Tf 1 0 0 1 180 220 Tm (30) Tj ET\n"
        b"198 220 m 235 190 l S\n"
        b"BT /F1 10 Tf 1 0 0 1 165 120 Tm (40) Tj ET\n"
        b"183 120 m 200 90 l S\n"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    graph = extract_page_graph(pdf)
    entities, _ = extract_drawing_entities(pdf)
    bind_drawing_context(graph, entities)

    assert len(entities) >= 2
    assert all(item["status"] == "context_bound" for item in entities)
    assert all(item["view_id"].startswith("PAGE_1_REGION_") for item in entities)
    assert entities[0]["leader_target_pdf"] == pytest.approx([155, 190])
    assert entities[0]["adjacent_segment_pdf"] == [[118.0, 220.0], [155.0, 190.0]]
    assert len(entities[0]["dimension_direction_pdf"]) == 2
    assert entities[0]["direction_confidence"] > 0.5
    assert entities[0]["connected_segment_count"] == 1


def test_vector_frame_and_detached_tolerance_are_recognized_only(tmp_path):
    pdf = tmp_path / "semantic-context.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=400)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 10 Tf 1 0 0 1 220 220 Tm (0.1) Tj ET\n"
        b"200 215 m 260 215 l S 200 235 m 260 235 l S\n"
        b"200 215 m 200 235 l S 260 215 m 260 235 l S\n"
        b"BT /F1 10 Tf 1 0 0 1 120 150 Tm (0.0) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 120 138 Tm (-0.05) Tj ET\n"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    entities, _ = extract_drawing_entities(pdf)

    framed = next(item for item in entities if item["raw_text"] == "0.1")
    fragment = next(item for item in entities if item["raw_text"] == "0.0")
    assert framed["semantic_type"] == "gdt_feature_control_frame"
    assert framed["comparison_eligible"] is False
    assert framed["recognition_status"] == "numeric_cell_in_vector_frame"
    assert fragment["semantic_type"] == "tolerance_fragment"
    assert fragment["comparison_eligible"] is False


def test_border_index_row_is_quarantined_with_audit_reason(tmp_path):
    pdf = tmp_path / "border-grid.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=800, height=600)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    commands = [
        f"BT /F1 10 Tf 1 0 0 1 {80 + index * 80} 580 Tm ({index + 1}) Tj ET"
        for index in range(8)
    ]
    commands.append("BT /F1 10 Tf 1 0 0 1 300 300 Tm (25) Tj ET")
    content = DecodedStreamObject()
    content.set_data(("\n".join(commands) + "\n").encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    entities, diagnostics = extract_drawing_entities(pdf)

    assert [item["raw_text"] for item in entities] == ["25"]
    assert diagnostics["excluded_candidate_count"] >= 1
    assert {item["reason"] for item in diagnostics["excluded_candidates"]} == {"drawing_border_index_row"}


def test_split_iso_annotations_are_assembled_and_semantically_retained(tmp_path):
    pdf = tmp_path / "split-annotations.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=800, height=600)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    commands = [
        "BT /F1 10 Tf 1 0 0 1 100 300 Tm (17.2) Tj ET",
        "BT /F1 7 Tf 1 0 0 1 127 308 Tm (+0.15) Tj ET",
        "BT /F1 7 Tf 1 0 0 1 127 300 Tm (-0.05) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 100 250 Tm <362E342028327829> Tj ET",
        "BT /F1 10 Tf 1 0 0 1 100 210 Tm <52312E3228347829> Tj ET",
        "BT /F1 10 Tf 1 0 0 1 100 170 Tm <2834322E3429> Tj ET",
        "BT /F1 10 Tf 1 0 0 1 100 130 Tm (0.2 CZ A-A B-B C) Tj ET",
        "BT /F1 10 Tf 1 0 0 1 100 100 Tm (STAMPING DIRECTION) Tj ET",
    ]
    content = DecodedStreamObject()
    content.set_data(("\n".join(commands) + "\n").encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    with pdf.open("wb") as stream:
        writer.write(stream)

    entities, _ = extract_drawing_entities(pdf)
    by_text = {item["raw_text"]: item for item in entities}

    assert by_text["17.2+0.15/-0.05"]["tolerance"] == {"upper": 0.15, "lower": -0.05}
    assert by_text["6.4 (2x)"]["semantic_type"] == "linear_dimension"
    assert by_text["6.4 (2x)"]["quantity"] == 2
    assert by_text["R1.2(4x)"]["quantity"] == 4
    assert by_text["(42.4)"]["semantic_type"] == "basic_dimension"
    assert by_text["0.2 CZ A-A B-B C"]["comparison_eligible"] is False
    assert by_text["STAMPING DIRECTION"]["comparison_eligible"] is False
