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
    font_size: float = 0.0


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
        font_size: float,
    ) -> None:
        normalized = " ".join(value.split())
        if not normalized:
            return
        x, y = _text_origin(text_matrix, current_matrix)
        texts.append(TextItem(normalized, x, y, float(font_size or 0.0)))

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


def _parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _parse_annotation(text: str) -> dict[str, Any] | None:
    """Parse one first-page text object into a conservative engineering entity.

    The parser deliberately preserves unsupported annotations instead of inventing a
    CAD binding.  It is the deterministic semantic-enrichment stage used before the
    correspondence scorer; later adapters can add GD&T and leader-line context.
    """

    raw = " ".join(text.split()).strip()
    compact = re.sub(r"\s+", "", raw).replace("⌀", "Ø").replace("ø", "Ø")
    if not compact or len(compact) > 96:
        return None

    surface = re.search(r"Rz\s*max\s*([0-9]+(?:[.,][0-9]+)?)", raw, re.IGNORECASE)
    if surface:
        return {
            "semantic_type": "surface_roughness",
            "nominal": _parse_number(surface.group(1)),
            "unit": "um",
            "quantity": 1,
            "target_feature_type": "surface",
            "confidence": 0.96,
        }

    angle = re.fullmatch(r"([0-9]+(?:[.,][0-9]+)?)°", compact)
    if angle:
        return {
            "semantic_type": "angle",
            "nominal": _parse_number(angle.group(1)),
            "unit": "deg",
            "quantity": 1,
            "target_feature_type": "angular_geometry",
            "confidence": 0.98,
        }

    radius = re.fullmatch(
        r"R(?:\((\d+)[xX]\))?([0-9]+(?:[.,][0-9]+)?)(?:±|\+/-)?([0-9]+(?:[.,][0-9]+)?)?",
        compact,
        re.IGNORECASE,
    )
    if radius:
        tolerance = _parse_number(radius.group(3))
        return {
            "semantic_type": "radius",
            "nominal": _parse_number(radius.group(2)),
            "unit": "mm",
            "quantity": int(radius.group(1) or 1),
            "tolerance": {"upper": tolerance, "lower": -tolerance}
            if tolerance is not None
            else None,
            "target_feature_type": "fillet_or_round",
            "confidence": 0.98,
        }

    dimension = re.fullmatch(
        r"(?:\((\d+)[xX]\)|(\d+)[xX])?([Ø]?)([0-9]+(?:[.,][0-9]+)?)"
        r"(?:(?:±|\+/-)([0-9]+(?:[.,][0-9]+)?))?",
        compact,
        re.IGNORECASE,
    )
    if not dimension:
        return None

    quantity = int(dimension.group(1) or dimension.group(2) or 1)
    has_diameter_symbol = bool(dimension.group(3))
    tolerance = _parse_number(dimension.group(5))
    # A repeated, toleranced callout without an explicit symbol is treated as a hole
    # diameter candidate.  This covers common Bosch-style "(6x)6.5 +/-0.1" drawings
    # while keeping plain integers conservative.
    inferred_diameter = quantity > 1 and tolerance is not None
    semantic_type = "diameter" if has_diameter_symbol or inferred_diameter else "linear_dimension"
    confidence = 0.99 if has_diameter_symbol else 0.94 if inferred_diameter else 0.78
    return {
        "semantic_type": semantic_type,
        "nominal": _parse_number(dimension.group(4)),
        "unit": "mm",
        "quantity": quantity,
        "tolerance": {"upper": tolerance, "lower": -tolerance}
        if tolerance is not None
        else None,
        "target_feature_type": "cylindrical_hole" if semantic_type == "diameter" else "linear_geometry",
        "confidence": confidence,
        "diameter_symbol_present": has_diameter_symbol,
    }


def extract_drawing_entities(pdf_path: Path, page_number: int = 1) -> tuple[list[dict], dict]:
    """Extract all bounded, measurable annotation candidates from one PDF page."""

    graph = extract_page_graph(pdf_path, page_number)
    entities: list[dict[str, Any]] = []
    page_width = float(graph["width"])
    page_height = float(graph["height"])

    def is_inspection_label(item: TextItem) -> bool:
        """Detect the small boxed inspection indices used by the sample drawings."""

        return any(
            12.0 <= path.width <= 30.0
            and path.height <= 0.8
            and item.x - 15.0 <= path.bounds[0] <= item.x + 1.5
            and item.y - 4.0 <= path.bounds[1] <= item.y + 0.5
            for path in graph["paths"]
        )

    source_items: list[TextItem] = []
    consumed: set[int] = set()
    texts: list[TextItem] = graph["texts"]

    # Some authoring tools emit a radius callout as three independent text objects
    # ("R", "(6x)", "6").  Reassemble those objects before semantic parsing.
    for index, item in enumerate(texts):
        if item.text.strip().upper() != "R":
            continue
        number_match = min(
            (
                (other_index, other)
                for other_index, other in enumerate(texts)
                if other_index != index
                and re.fullmatch(r"\d+(?:[.,]\d+)?", other.text.strip())
                and abs(other.y - item.y) <= 4.0
                and 0.0 < other.x - item.x <= 22.0
            ),
            key=lambda pair: pair[1].x - item.x,
            default=None,
        )
        if number_match is None:
            continue
        quantity_match = min(
            (
                (other_index, other)
                for other_index, other in enumerate(texts)
                if re.fullmatch(r"\(\d+[xX]\)", other.text.strip())
                and abs(other.x - item.x) <= 18.0
                and 0.0 < item.y - other.y <= 24.0
            ),
            key=lambda pair: item.y - pair[1].y,
            default=None,
        )
        number_index, number_item = number_match
        quantity = f" {quantity_match[1].text.strip()}" if quantity_match else ""
        source_items.append(
            TextItem(
                text=f"R{quantity}{number_item.text.strip()}",
                x=item.x,
                y=item.y,
                font_size=max(item.font_size, number_item.font_size),
            )
        )
        consumed.update({index, number_index})
        if quantity_match:
            consumed.add(quantity_match[0])

    for index, item in enumerate(texts):
        if index in consumed:
            continue
        raw = " ".join(item.text.split()).strip()
        integer_tokens = re.fullmatch(r"\d+(?:\s+\d+)+", raw)
        if integer_tokens:
            values = raw.split()
            # Two dimensions can share one PDF text object. Inspection-index groups
            # have a characteristic surrounding box and must not become dimensions.
            if len(values) == 2 and not is_inspection_label(item):
                for offset, value in enumerate(values):
                    source_items.append(
                        TextItem(
                            text=value,
                            x=item.x + offset * 14.0,
                            y=item.y,
                            font_size=item.font_size,
                        )
                    )
            continue
        source_items.append(item)

    inspection_label_count = 0
    for item in source_items:
        raw = " ".join(item.text.split()).strip()
        if re.fullmatch(r"\d{1,3}", raw) and is_inspection_label(item):
            inspection_label_count += 1
            continue
        # Long, tolerance-free integers are overwhelmingly title-block metadata.
        if re.fullmatch(r"\d{4,}", raw):
            continue
        parsed = _parse_annotation(item.text)
        if parsed is None:
            continue
        # Exclude obvious border/title-block tokens.  Ambiguous drawing-area integers
        # are retained with lower confidence so the UI still lists them for review.
        if item.x < 55 or item.y < max(80.0, page_height * 0.08):
            continue
        if item.x > page_width * 0.82 and item.y < page_height * 0.58:
            continue
        text_width = max(item.font_size * 0.55 * len(item.text), 8.0)
        text_height = max(item.font_size, 8.0)
        entity = {
            "id": f"D2-{len(entities) + 1:03d}",
            "page": page_number,
            "raw_text": item.text,
            "anchor_pdf": [round(item.x, 3), round(item.y, 3)],
            "bbox_pdf": [
                round(item.x, 3),
                round(item.y - text_height * 0.25, 3),
                round(item.x + text_width, 3),
                round(item.y + text_height, 3),
            ],
            "view_id": "PAGE_1_UNASSIGNED",
            "status": "unbound",
            "source_method": "pypdf_text_object",
            **parsed,
        }
        entities.append(entity)

    diagnostics = {
        "page": page_number,
        "text_object_count": len(graph["texts"]),
        "entity_count": len(entities),
        "inspection_label_count": inspection_label_count,
        "semantic_type_counts": {
            kind: sum(item["semantic_type"] == kind for item in entities)
            for kind in sorted({item["semantic_type"] for item in entities})
        },
    }
    return entities, diagnostics


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
    drawing_entities, entity_diagnostics = extract_drawing_entities(pdf_path)
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
    source_entity = min(
        (
            entity
            for entity in drawing_entities
            if entity.get("semantic_type") == "diameter"
            and entity.get("quantity") == quantity
            and abs(float(entity.get("nominal", -1)) - nominal) <= 1e-6
        ),
        key=lambda entity: math.dist(entity["anchor_pdf"], [callout.x, callout.y]),
        default=None,
    )
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
                    "drawing_entity_id": source_entity["id"] if source_entity else None,
                    "anchor_pdf": [round(label_anchor.x, 6), round(label_anchor.y, 6)],
                    "raw_text": normalized_callout,
                },
                "extraction": {"status": "ready", "confidence": 0.98},
            }
        ],
        "drawing_entities": drawing_entities,
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
                "drawing_entity_id": source_entity["id"] if source_entity else None,
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
        "drawing_entity_extraction": entity_diagnostics,
        "selected_callout_text": callout.text,
        "selected_scale_text": scale_item.text,
    }
    return measurement_plan, vector_extraction, diagnostics


def discover_drawing_requirements(
    pdf_path: Path,
    extraction_error: str,
) -> tuple[dict, dict, dict]:
    """Collect bounded annotation candidates when no supported C10 template exists."""
    graph = extract_page_graph(pdf_path)
    drawing_entities, entity_diagnostics = extract_drawing_entities(pdf_path)
    texts: list[TextItem] = graph["texts"]
    paths: list[VectorPath] = graph["paths"]
    candidates: list[dict[str, Any]] = []
    numeric_pattern = re.compile(r"^[<>]?[+-]?[0-9]+(?:\.[0-9]+)?(?:\s*(?:±|\+/-)\s*[0-9.]+)?$")
    engineering_marker = re.compile(
        r"(?:[Øø⌀±°]|\bREF\b|\bTYP\b|\bMAX\b|\bMIN\b|\bR\s*[0-9]|[0-9]\s*[xX]\s*[Øø⌀]?)",
        re.IGNORECASE,
    )
    seen: set[tuple[str, int, int]] = set()
    for item in texts:
        value = " ".join(item.text.split())
        if not value or len(value) > 160:
            continue
        if not numeric_pattern.fullmatch(value) and not engineering_marker.search(value):
            continue
        key = (value, round(item.x), round(item.y))
        if key in seen:
            continue
        seen.add(key)
        upper = value.upper().replace("⌀", "Ø")
        kind = (
            "diameter"
            if "Ø" in upper or "ø" in value
            else "radius"
            if re.search(r"(?:^|\s)R\s*[0-9]", upper)
            else "angle"
            if "°" in value
            else "reference"
            if "REF" in upper
            else "linear_dimension_candidate"
        )
        candidates.append(
            {
                "id": f"REQ_CANDIDATE_{len(candidates) + 1:03d}",
                "raw_text": value,
                "kind": kind,
                "anchor_pdf": [round(item.x, 3), round(item.y, 3)],
                "status": "unbound",
            }
        )
        if len(candidates) >= 250:
            break

    source = {
        "filename": pdf_path.name,
        "page": 1,
        "unit": "mm",
        "extraction_method": "pypdf_requirement_candidate_discovery",
    }
    measurement_plan = {
        "schema_version": "0.3.0",
        "plan_id": f"{pdf_path.stem}-page-1-discovery",
        "source": source,
        "scope": {
            "mode": "requirement_discovery",
            "included_labels": [],
            "status": "needs_review",
        },
        "measurements": [],
        "drawing_entities": drawing_entities,
        "requirement_candidates": candidates,
        "extraction": {
            "status": "needs_review",
            "reason": "supported_c10_pattern_not_found",
            "detail": extraction_error,
        },
    }
    vector_extraction = {
        "schema_version": "0.3.0",
        "source": {"filename": pdf_path.name, "page": 1},
        "extraction_method": "pypdf_content_stream_discovery",
        "page": {
            "width": graph["width"],
            "height": graph["height"],
            "unit": "pdf_point",
            "coordinate_system": "origin_bottom_left",
        },
        "view_calibration": None,
        "vector_features": [],
        "annotation_bindings": [],
        "annotation_candidates": candidates,
        "drawing_entities": drawing_entities,
    }
    diagnostics = {
        "schema_version": "0.2.0",
        "source_file": pdf_path.name,
        "page_count": graph["page_count"],
        "text_item_count": len(texts),
        "painted_path_count": len(paths),
        "requirement_candidate_count": len(candidates),
        "mode": "requirement_discovery",
        "fallback_reason": extraction_error,
        "drawing_entity_extraction": entity_diagnostics,
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
