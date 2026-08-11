from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader


PDF_POINTS_PER_MM = 72.0 / 25.4


@dataclass(frozen=True)
class TextItem:
    text: str
    x: float
    y: float


@dataclass(frozen=True)
class VectorPath:
    signature: str
    paint_operator: str
    bounds: tuple[float, float, float, float]

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]

    @property
    def center(self) -> tuple[float, float]:
        return (
            (self.bounds[0] + self.bounds[2]) / 2,
            (self.bounds[1] + self.bounds[3]) / 2,
        )


def _numeric(value: Any) -> float:
    return float(value.as_numeric() if hasattr(value, "as_numeric") else value)


def _transform_point(x: float, y: float, matrix: list[float]) -> tuple[float, float]:
    return (
        matrix[0] * x + matrix[2] * y + matrix[4],
        matrix[1] * x + matrix[3] * y + matrix[5],
    )


def _text_origin(text_matrix: list[float], current_matrix: list[float]) -> tuple[float, float]:
    return _transform_point(text_matrix[4], text_matrix[5], current_matrix)


def extract_page_graph(pdf_path: Path, page_number: int = 1) -> dict:
    reader = PdfReader(pdf_path)
    if page_number < 1 or page_number > len(reader.pages):
        raise ValueError(f"PDF 不存在第 {page_number} 页")
    page = reader.pages[page_number - 1]
    texts: list[TextItem] = []
    paths: list[VectorPath] = []
    current_path: list[tuple[str, tuple[tuple[float, float], ...]]] = []

    def visit_text(
        value: str,
        current_matrix: list[float],
        text_matrix: list[float],
        _font: dict | None,
        _font_size: float,
    ) -> None:
        normalized = " ".join(value.split())
        if not normalized:
            return
        x, y = _text_origin(text_matrix, current_matrix)
        texts.append(TextItem(normalized, x, y))

    def visit_operator(
        operator: bytes,
        arguments: list[Any],
        current_matrix: list[float],
        _text_matrix: list[float],
    ) -> None:
        nonlocal current_path
        if operator in {b"m", b"l"}:
            point = _transform_point(
                _numeric(arguments[0]),
                _numeric(arguments[1]),
                current_matrix,
            )
            current_path.append((operator.decode("ascii"), (point,)))
        elif operator == b"c":
            points = tuple(
                _transform_point(
                    _numeric(arguments[index]),
                    _numeric(arguments[index + 1]),
                    current_matrix,
                )
                for index in range(0, 6, 2)
            )
            current_path.append(("c", points))
        elif operator == b"h":
            current_path.append(("h", ()))
        elif operator in {b"S", b"s", b"f", b"f*", b"B", b"B*", b"b", b"b*", b"n"}:
            points = [point for _, command_points in current_path for point in command_points]
            if points:
                x_values = [point[0] for point in points]
                y_values = [point[1] for point in points]
                paths.append(
                    VectorPath(
                        signature="".join(command for command, _ in current_path),
                        paint_operator=operator.decode("ascii"),
                        bounds=(
                            min(x_values),
                            min(y_values),
                            max(x_values),
                            max(y_values),
                        ),
                    )
                )
            current_path = []

    page.extract_text(
        visitor_operand_before=visit_operator,
        visitor_text=visit_text,
    )
    return {
        "page_count": len(reader.pages),
        "page_number": page_number,
        "width": float(page.mediabox.width),
        "height": float(page.mediabox.height),
        "texts": texts,
        "paths": paths,
    }


def _parse_hole_callout(text: str) -> tuple[int, float, float] | None:
    compact = re.sub(r"\s+", "", text).replace("⌀", "Ø")
    patterns = (
        r"\((\d+)[xX]\)[Øø]?([0-9]+(?:\.[0-9]+)?)(?:±|\+/-)([0-9]+(?:\.[0-9]+)?)",
        r"(\d+)[xX][Øø]?([0-9]+(?:\.[0-9]+)?)(?:±|\+/-)([0-9]+(?:\.[0-9]+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            return int(match.group(1)), float(match.group(2)), float(match.group(3))
    return None


def _nearest_inspection_label(callout: TextItem, texts: list[TextItem]) -> TextItem | None:
    labels = [item for item in texts if re.fullmatch(r"\d{1,3}", item.text)]
    if not labels:
        return None
    nearest = min(labels, key=lambda item: math.hypot(item.x - callout.x, item.y - callout.y))
    return nearest if math.hypot(nearest.x - callout.x, nearest.y - callout.y) <= 80 else None


def _nearest_drawing_scale(callout: TextItem, texts: list[TextItem]) -> tuple[float, TextItem]:
    candidates: list[tuple[float, TextItem]] = []
    for item in texts:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?):([0-9]+(?:\.[0-9]+)?)", item.text)
        if match and float(match.group(2)) > 0:
            candidates.append((float(match.group(1)) / float(match.group(2)), item))
    if not candidates:
        raise ValueError("无法从 PDF 找到视图比例")
    return min(
        candidates,
        key=lambda candidate: math.hypot(
            candidate[1].x - callout.x,
            candidate[1].y - callout.y,
        ),
    )


def _circle_paths(
    paths: list[VectorPath],
    expected_diameter_points: float,
    tolerance_ratio: float = 0.015,
) -> list[VectorPath]:
    tolerance = max(0.15, expected_diameter_points * tolerance_ratio)
    return [
        path
        for path in paths
        if path.signature in {"mcccc", "mcccch"}
        and path.paint_operator in {"S", "s", "B", "B*"}
        and abs(path.width - path.height) <= tolerance
        and abs(path.width - expected_diameter_points) <= tolerance
    ]


def _ordered_centers(paths: list[VectorPath], row_tolerance: float) -> list[tuple[float, float]]:
    remaining = sorted((path.center for path in paths), key=lambda point: (-point[1], point[0]))
    rows: list[list[tuple[float, float]]] = []
    while remaining:
        first = remaining.pop(0)
        row = [first]
        same_row = [point for point in remaining if abs(point[1] - first[1]) <= row_tolerance]
        for point in same_row:
            remaining.remove(point)
            row.append(point)
        rows.append(sorted(row, key=lambda point: point[0]))
    return [point for row in rows for point in row]


def extract_c10_from_pdf(pdf_path: Path) -> tuple[dict, dict, dict]:
    graph = extract_page_graph(pdf_path)
    texts: list[TextItem] = graph["texts"]
    paths: list[VectorPath] = graph["paths"]
    callout_candidates = [
        (item, parsed)
        for item in texts
        if (parsed := _parse_hole_callout(item.text)) is not None
    ]
    if not callout_candidates:
        raise ValueError("PDF 中未找到数量×孔径±公差形式的孔标注")

    selected: tuple[TextItem, tuple[int, float, float], TextItem | None] | None = None
    for item, parsed in callout_candidates:
        label = _nearest_inspection_label(item, texts)
        if label and label.text == "10":
            selected = item, parsed, label
            break
    if selected is None:
        item, parsed = callout_candidates[0]
        selected = item, parsed, _nearest_inspection_label(item, texts)

    callout, (quantity, nominal, symmetric_tolerance), label = selected
    drawing_scale, scale_item = _nearest_drawing_scale(callout, texts)
    points_per_model_mm = PDF_POINTS_PER_MM * drawing_scale
    expected_diameter_points = nominal * points_per_model_mm
    circles = _circle_paths(paths, expected_diameter_points)
    if len(circles) != quantity:
        raise ValueError(
            f"PDF 标注要求 {quantity} 个孔，但矢量路径匹配到 {len(circles)} 个候选圆"
        )

    centers = _ordered_centers(circles, expected_diameter_points * 0.25)
    origin_x = min(point[0] for point in centers)
    origin_y = max(point[1] for point in centers)
    members = [
        {
            "id": f"VG_HOLE_{index + 1:02d}",
            "center_pdf": [round(center[0], 6), round(center[1], 6)],
            "relative_center_mm": [
                round((center[0] - origin_x) / points_per_model_mm, 6),
                round((center[1] - origin_y) / points_per_model_mm, 6),
            ],
        }
        for index, center in enumerate(centers)
    ]
    label_value = label.text if label else "10"
    label_anchor = label if label else callout
    normalized_callout = f"{nominal:g} ±{symmetric_tolerance:g} ({quantity}x)"
    measurement_plan = {
        "schema_version": "0.2.0",
        "plan_id": f"{pdf_path.stem}-page-1-auto",
        "source": {
            "filename": pdf_path.name,
            "page": 1,
            "unit": "mm",
            "extraction_method": "pypdf_vector_content_stream",
        },
        "scope": {"mode": "automatically_extracted_characteristics", "included_labels": [label_value]},
        "measurements": [
            {
                "id": "C10",
                "type": "hole_diameter",
                "nominal": nominal,
                "tolerance": {"upper": symmetric_tolerance, "lower": -symmetric_tolerance},
                "quantity": quantity,
                "target": {
                    "feature_type": "cylindrical_hole_group",
                    "constraints": {"diameter": nominal, "count": quantity},
                },
                "source": {
                    "view": "VIEW_MAIN",
                    "label": label_value,
                    "anchor_pdf": [round(label_anchor.x, 6), round(label_anchor.y, 6)],
                    "raw_text": normalized_callout,
                },
                "extraction": {"status": "ready", "confidence": 0.98},
            }
        ],
    }
    vector_extraction = {
        "schema_version": "0.2.0",
        "source": {"filename": pdf_path.name, "page": 1},
        "extraction_method": "pypdf_content_stream_operators",
        "page": {
            "width": graph["width"],
            "height": graph["height"],
            "unit": "pdf_point",
            "coordinate_system": "origin_bottom_left",
        },
        "view_calibration": {
            "view_id": "VIEW_MAIN",
            "drawing_scale": drawing_scale,
            "pdf_points_per_model_mm": points_per_model_mm,
            "source_text": scale_item.text,
            "confidence": 0.99,
        },
        "vector_features": [
            {
                "id": "VG_HOLE_GROUP_01",
                "type": "circular_feature_group",
                "quantity": quantity,
                "diameter_mm": nominal,
                "diameter_pdf_points": round(sum(path.width for path in circles) / len(circles), 6),
                "members": members,
                "confidence": 0.99,
            }
        ],
        "annotation_bindings": [
            {
                "measurement_id": "C10",
                "raw_text": normalized_callout,
                "type": "hole_diameter",
                "target_feature_id": "VG_HOLE_GROUP_01",
                "nominal": nominal,
                "tolerance": {"upper": symmetric_tolerance, "lower": -symmetric_tolerance},
                "quantity": quantity,
                "binding_method": [
                    "text_pattern",
                    "nearest_inspection_label",
                    "nearest_view_scale",
                    "vector_circle_diameter",
                    "group_quantity",
                ],
                "confidence": 0.98,
            }
        ],
    }
    diagnostics = {
        "schema_version": "0.1.0",
        "source_file": pdf_path.name,
        "page_count": graph["page_count"],
        "text_item_count": len(texts),
        "painted_path_count": len(paths),
        "hole_callout_candidates": len(callout_candidates),
        "matched_circle_count": len(circles),
        "selected_callout_text": callout.text,
        "selected_scale_text": scale_item.text,
    }
    return measurement_plan, vector_extraction, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract characteristic C10 from a vector PDF")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output-directory", type=Path)
    arguments = parser.parse_args()
    plan, vector, diagnostics = extract_c10_from_pdf(arguments.pdf)
    payload = {"measurement_plan": plan, "vector_extraction": vector, "diagnostics": diagnostics}
    if arguments.output_directory:
        arguments.output_directory.mkdir(parents=True, exist_ok=True)
        for name, content in payload.items():
            (arguments.output_directory / f"{name}.json").write_text(
                json.dumps(content, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
