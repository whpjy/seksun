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
    points: tuple[tuple[float, float], ...] = ()

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
            # A single PDF paint operation often contains hundreds of disconnected
            # subpaths. Keeping one combined bounding box loses small inspection
            # boxes and leader segments inside a page-sized bound, so preserve each
            # move-to-delimited subpath independently.
            subpaths: list[list[tuple[str, tuple[tuple[float, float], ...]]]] = []
            for command in current_path:
                if command[0] == "m" or not subpaths:
                    subpaths.append([])
                subpaths[-1].append(command)
            for subpath in subpaths:
                points = [point for _, command_points in subpath for point in command_points]
                if not points:
                    continue
                x_values = [point[0] for point in points]
                y_values = [point[1] for point in points]
                paths.append(
                    VectorPath(
                        signature="".join(command for command, _ in subpath),
                        paint_operator=operator.decode("ascii"),
                        bounds=(min(x_values), min(y_values), max(x_values), max(y_values)),
                        points=tuple(points),
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


def _distance_to_box(
    point: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> float:
    x, y = point
    left, bottom, right, top = bounds
    dx = max(left - x, 0.0, x - right)
    dy = max(bottom - y, 0.0, y - top)
    return math.hypot(dx, dy)


def _leader_target(
    entity: dict[str, Any],
    segments: list[tuple[tuple[float, float], tuple[float, float], float]],
    endpoint_index: dict[tuple[int, int], set[int]],
    page_width: float,
    page_height: float,
) -> tuple[list[float], float, dict[str, Any]] | None:
    """Follow linework adjacent to a callout and return its remote endpoint.

    Engineering PDF writers normally emit leaders and dimension shoulders as
    individual ``m/l`` subpaths.  We deliberately ignore curves, page borders and
    very short glyph strokes, then walk the small endpoint graph that touches the
    annotation box.  The result is contextual evidence only; it is never treated
    as a verified conformance binding.
    """

    raw_bounds = entity.get("bbox_pdf") or []
    if len(raw_bounds) != 4:
        return None
    bounds = tuple(float(value) for value in raw_bounds)
    diagonal = math.hypot(page_width, page_height)
    endpoint_tolerance = max(2.5, diagonal * 0.0012)
    seed_tolerance = max(12.0, float(entity.get("font_size") or 0) * 1.5)

    seeds = {
        index
        for index, (first, second, _) in enumerate(segments)
        if min(_distance_to_box(first, bounds), _distance_to_box(second, bounds))
        <= seed_tolerance
    }
    if not seeds:
        return None

    # Preserve the line segment closest to the annotation as independent
    # directional evidence.  It is deliberately not assumed to be a verified
    # dimension line: depending on drafting style it may be a leader shoulder.
    seed_segment = min(
        seeds,
        key=lambda index: (
            min(
                _distance_to_box(segments[index][0], bounds),
                _distance_to_box(segments[index][1], bounds),
            ),
            -segments[index][2],
            index,
        ),
    )

    connected = set(seeds)
    frontier = sorted(seeds, reverse=True)
    endpoints = [point for index in sorted(seeds) for point in segments[index][:2]]
    while frontier and len(connected) < 24:
        current = frontier.pop()
        current_points = segments[current][:2]
        neighbor_indices: set[int] = set()
        for point in current_points:
            cell_x = round(point[0] / endpoint_tolerance)
            cell_y = round(point[1] / endpoint_tolerance)
            for offset_x in (-1, 0, 1):
                for offset_y in (-1, 0, 1):
                    neighbor_indices.update(
                        endpoint_index.get((cell_x + offset_x, cell_y + offset_y), set())
                    )
        for index in sorted(neighbor_indices):
            if index in connected:
                continue
            segment = segments[index]
            if any(
                math.dist(left, right) <= endpoint_tolerance
                for left in current_points
                for right in segment[:2]
            ):
                connected.add(index)
                frontier.append(index)
                endpoints.extend(segment[:2])

    anchor = (
        (bounds[0] + bounds[2]) / 2,
        (bounds[1] + bounds[3]) / 2,
    )
    target = max(endpoints, key=lambda point: math.dist(anchor, point))
    distance = math.dist(anchor, target)
    if distance < max(18.0, diagonal * 0.008):
        return None
    confidence = min(0.9, 0.5 + distance / max(diagonal * 0.15, 1.0) * 0.35)
    first, second, segment_length = segments[seed_segment]
    dx, dy = second[0] - first[0], second[1] - first[1]
    direction = [dx / segment_length, dy / segment_length]
    cardinality = max(abs(direction[0]), abs(direction[1]))
    direction_confidence = min(
        0.85,
        0.4 + 0.25 * cardinality + 0.2 * min(1.0, segment_length / max(24.0, diagonal * 0.02)),
    )
    evidence = {
        "adjacent_segment_pdf": [
            [round(first[0], 3), round(first[1], 3)],
            [round(second[0], 3), round(second[1], 3)],
        ],
        "dimension_direction_pdf": [round(direction[0], 6), round(direction[1], 6)],
        "direction_confidence": round(direction_confidence, 3),
        "connected_segment_count": len(connected),
    }
    return [round(target[0], 3), round(target[1], 3)], round(confidence, 3), evidence


def bind_drawing_context(
    graph: dict[str, Any],
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach conservative leader targets and local drawing-region identities."""

    page_width = float(graph["width"])
    page_height = float(graph["height"])
    paths: list[VectorPath] = graph["paths"]
    diagonal = math.hypot(page_width, page_height)
    endpoint_tolerance = max(2.5, diagonal * 0.0012)
    segments: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    for path in paths:
        if path.signature != "ml" or len(path.points) != 2:
            continue
        first, second = path.points
        length = math.dist(first, second)
        if length < 4.0 or length > diagonal * 0.35:
            continue
        if path.width > page_width * 0.3 or path.height > page_height * 0.3:
            continue
        segments.append((first, second, length))
    endpoint_index: dict[tuple[int, int], set[int]] = {}
    for index, segment in enumerate(segments):
        for point in segment[:2]:
            cell = (
                round(point[0] / endpoint_tolerance),
                round(point[1] / endpoint_tolerance),
            )
            endpoint_index.setdefault(cell, set()).add(index)
    bound: list[tuple[dict[str, Any], list[float], float]] = []
    for entity in entities:
        if entity.get("skip_context_binding"):
            continue
        target = _leader_target(
            entity,
            segments,
            endpoint_index,
            page_width,
            page_height,
        )
        if target is None:
            continue
        point, confidence, evidence = target
        entity["leader_target_pdf"] = point
        entity["context_confidence"] = confidence
        entity.update(evidence)
        bound.append((entity, point, confidence))

    # Leader endpoints belonging to one projected view form compact spatial
    # groups.  The radius is deliberately smaller than the separation between
    # normal orthographic views on an engineering sheet.
    radius = min(page_width, page_height) * 0.07
    remaining = set(range(len(bound)))
    groups: list[set[int]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        group = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            nearby = {
                index
                for index in remaining
                if math.dist(bound[current][1], bound[index][1]) <= radius
            }
            remaining -= nearby
            group |= nearby
            frontier.extend(sorted(nearby, reverse=True))
        groups.append(group)

    core_groups = [group for group in groups if len(group) >= 3]
    small_groups = [group for group in groups if len(group) < 3]
    if core_groups:
        detached: list[set[int]] = []
        for group in small_groups:
            closest = min(
                (
                    (
                        min(
                            math.dist(bound[left][1], bound[right][1])
                            for left in group
                            for right in core
                        ),
                        core,
                    )
                    for core in core_groups
                ),
                key=lambda item: item[0],
            )
            if closest[0] <= radius * 2.2:
                closest[1].update(group)
            else:
                detached.append(group)
        groups = core_groups + detached

    groups.sort(
        key=lambda group: (
            -sum(bound[index][1][1] for index in group) / len(group),
            sum(bound[index][1][0] for index in group) / len(group),
        )
    )
    for number, group in enumerate(groups, 1):
        xs = [bound[index][1][0] for index in group]
        ys = [bound[index][1][1] for index in group]
        region_id = f"PAGE_1_REGION_{number:02d}"
        region_bounds = [min(xs), min(ys), max(xs), max(ys)]
        for index in group:
            entity = bound[index][0]
            entity["view_id"] = region_id
            entity["status"] = "context_bound"
            entity["view_region_pdf"] = [round(value, 3) for value in region_bounds]
            entity["view_region_size"] = len(group)
    return entities


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

    if re.fullmatch(r"(?:STAMPING|PUNCHING)DIRECTION", compact, re.IGNORECASE):
        return {
            "semantic_type": "process_direction",
            "nominal": None,
            "unit": None,
            "quantity": 1,
            "target_feature_type": "manufacturing_direction",
            "confidence": 0.96,
            "comparison_eligible": False,
        }

    # Parenthesized values are basic/reference dimensions. They are useful
    # drawing requirements even though they do not carry a direct tolerance.
    basic = re.fullmatch(r"\(([0-9]+(?:[.,][0-9]+)?)\)", compact)
    if basic:
        return {
            "semantic_type": "basic_dimension",
            "nominal": _parse_number(basic.group(1)),
            "unit": "mm",
            "quantity": 1,
            "target_feature_type": "linear_geometry",
            "confidence": 0.98,
            "is_basic": True,
        }

    # Some PDF exports preserve the tolerance and datum labels of a feature
    # control frame but lose its graphical characteristic symbol. Retain that
    # evidence without claiming deterministic conformance.
    gdt = re.fullmatch(
        r"([0-9]+(?:[.,][0-9]+)?)\s*(?:CZ\s*)?"
        r"([A-Z](?:-[A-Z])?(?:\s+[A-Z](?:-[A-Z])?)*)",
        raw,
        re.IGNORECASE,
    )
    if gdt and any(character.isalpha() for character in gdt.group(2)):
        return {
            "semantic_type": "gdt_feature_control_frame",
            "nominal": _parse_number(gdt.group(1)),
            "unit": "mm",
            "quantity": 1,
            "target_feature_type": "geometric_control",
            "confidence": 0.82,
            "datum_references": gdt.group(2).upper().split(),
            "comparison_eligible": False,
            "recognition_status": "symbol_recovery_required",
        }

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
        r"R(?:\((\d+)[xX]\))?([0-9]+(?:[.,][0-9]+)?)(?:\((\d+)[xX]\))?"
        r"(?:±|\+/-)?([0-9]+(?:[.,][0-9]+)?)?",
        compact,
        re.IGNORECASE,
    )
    if radius:
        tolerance = _parse_number(radius.group(4))
        return {
            "semantic_type": "radius",
            "nominal": _parse_number(radius.group(2)),
            "unit": "mm",
            "quantity": int(radius.group(1) or radius.group(3) or 1),
            "tolerance": {"upper": tolerance, "lower": -tolerance}
            if tolerance is not None
            else None,
            "target_feature_type": "fillet_or_round",
            "confidence": 0.98,
        }

    asymmetric_dimension = re.fullmatch(
        r"(?:\((\d+)[xX]\)|(\d+)[xX])?([Ø]?)([0-9]+(?:[.,][0-9]+)?)(?:\((\d+)[xX]\))?"
        r"\+([0-9]+(?:[.,][0-9]+)?)/-([0-9]+(?:[.,][0-9]+)?)",
        compact,
        re.IGNORECASE,
    )
    if asymmetric_dimension:
        quantity = int(
            asymmetric_dimension.group(1)
            or asymmetric_dimension.group(2)
            or asymmetric_dimension.group(5)
            or 1
        )
        has_diameter_symbol = bool(asymmetric_dimension.group(3))
        nominal = _parse_number(asymmetric_dimension.group(4))
        semantic_type = "diameter" if has_diameter_symbol else "linear_dimension"
        return {
            "semantic_type": semantic_type,
            "nominal": nominal,
            "unit": "mm",
            "quantity": quantity,
            "tolerance": {
                "upper": _parse_number(asymmetric_dimension.group(6)),
                "lower": -float(_parse_number(asymmetric_dimension.group(7)) or 0),
            },
            "target_feature_type": "cylindrical_hole" if semantic_type == "diameter" else "linear_geometry",
            "confidence": 0.99 if has_diameter_symbol else 0.9,
            "diameter_symbol_present": has_diameter_symbol,
        }

    dimension = re.fullmatch(
        r"(?:\((\d+)[xX]\)|(\d+)[xX])?([Ø]?)([0-9]+(?:[.,][0-9]+)?)(?:\((\d+)[xX]\))?"
        r"(?:(?:±|\+/-)([0-9]+(?:[.,][0-9]+)?))?",
        compact,
        re.IGNORECASE,
    )
    if not dimension:
        return None

    quantity = int(dimension.group(1) or dimension.group(2) or dimension.group(5) or 1)
    has_diameter_symbol = bool(dimension.group(3))
    tolerance = _parse_number(dimension.group(6))
    nominal = _parse_number(dimension.group(4))
    # Some embedded drawing fonts drop the diameter glyph during text extraction.
    # Repeated small callouts are therefore retained as diameter candidates. Large
    # repeated values (for example 2x83.25) remain linear dimensions.
    # A repeated small value is not enough to prove a diameter (for example,
    # ``4x 6.4`` can be four linear tabs). Keep the legacy glyph-loss fallback
    # only when an explicit tolerance also makes the hole-callout shape strong.
    inferred_diameter = quantity > 1 and (
        tolerance is not None
        or (dimension.group(2) is not None and float(nominal or 0) <= 3.0)
    )
    semantic_type = "diameter" if has_diameter_symbol or inferred_diameter else "linear_dimension"
    confidence = 0.99 if has_diameter_symbol else 0.94 if inferred_diameter else 0.78
    return {
        "semantic_type": semantic_type,
        "nominal": nominal,
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
    inspection_cache: dict[TextItem, bool] = {}

    def is_inspection_label(item: TextItem) -> bool:
        """Detect the small boxed inspection indices used by the sample drawings."""

        if item in inspection_cache:
            return inspection_cache[item]
        result = any(
            12.0 <= path.width <= 30.0
            and path.height <= 0.8
            and item.x - 15.0 <= path.bounds[0] <= item.x + 1.5
            and item.y - 4.0 <= path.bounds[1] <= item.y + 0.5
            for path in graph["paths"]
        )
        inspection_cache[item] = result
        return result

    def is_inside_feature_control_frame(bounds: tuple[float, float, float, float]) -> bool:
        """Detect one cell of a vector-drawn GD&T feature-control frame."""

        left, bottom, right, top = bounds
        tolerance = 2.5
        horizontal = [
            path
            for path in graph["paths"]
            if path.signature == "ml" and path.width >= right - left
            and path.height <= tolerance
        ]
        vertical = [
            path
            for path in graph["paths"]
            if path.signature == "ml" and path.height >= top - bottom
            and path.width <= tolerance
        ]
        left_edges = [
            path for path in vertical
            if path.bounds[0] <= left + tolerance
            and left - path.bounds[0] <= 80.0
            and path.bounds[1] <= bottom + tolerance
            and path.bounds[3] >= top - tolerance
        ]
        right_edges = [
            path for path in vertical
            if path.bounds[2] >= right - tolerance
            and path.bounds[2] - right <= 80.0
            and path.bounds[1] <= bottom + tolerance
            and path.bounds[3] >= top - tolerance
        ]
        if not left_edges or not right_edges:
            return False
        for left_edge in left_edges:
            for right_edge in right_edges:
                frame_left = left_edge.bounds[0]
                frame_right = right_edge.bounds[2]
                if frame_right - frame_left < right - left:
                    continue
                lower = any(
                    path.bounds[0] <= frame_left + tolerance
                    and path.bounds[2] >= frame_right - tolerance
                    and abs(path.bounds[1] - bottom) <= 18.0
                    for path in horizontal
                )
                upper = any(
                    path.bounds[0] <= frame_left + tolerance
                    and path.bounds[2] >= frame_right - tolerance
                    and abs(path.bounds[1] - top) <= 18.0
                    for path in horizontal
                )
                if lower and upper:
                    return True
        return False

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
                if re.fullmatch(r"\(?\d+[xX]\)?", other.text.strip())
                and (
                    (abs(other.y - item.y) <= 3.0 and 0.0 < item.x - other.x <= 45.0)
                    or (abs(other.x - item.x) <= 18.0 and 0.0 < item.y - other.y <= 24.0)
                )
            ),
            key=lambda pair: item.y - pair[1].y,
            default=None,
        )
        number_index, number_item = number_match
        quantity = f" ({quantity_match[1].text.strip().strip('()')})" if quantity_match else ""
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

    # Quantity suffixes are common in ISO drawings: the nominal is on one
    # baseline and ``(4x)`` is either directly to its right or just below it.
    # Normalize both layouts to the prefix form understood by the parser.
    for index, item in enumerate(texts):
        if index in consumed:
            continue
        quantity_match = re.fullmatch(r"\(([0-9]+)[xX]\)", item.text.strip())
        if not quantity_match:
            continue
        targets: list[tuple[float, int, TextItem]] = []
        for other_index, other in enumerate(texts):
            if other_index == index or other_index in consumed or is_inspection_label(other):
                continue
            target_text = other.text.strip()
            if not re.fullmatch(r"R?[0-9]+(?:[.,][0-9]+)?", target_text, re.IGNORECASE):
                continue
            dx, dy = item.x - other.x, item.y - other.y
            horizontally_adjacent = abs(dy) <= 4.5 and 3.0 <= abs(dx) <= 52.0
            vertically_adjacent = abs(dx) <= 30.0 and 5.0 <= abs(dy) <= 27.0
            if horizontally_adjacent or vertically_adjacent:
                targets.append((math.hypot(dx, dy), other_index, other))
        if not targets:
            continue
        _, target_index, target = min(targets, key=lambda candidate: candidate[0])
        quantity = quantity_match.group(1)
        target_text = target.text.strip()
        combined = (
            f"R({quantity}x){target_text[1:]}"
            if target_text.upper().startswith("R")
            else f"({quantity}x){target_text}"
        )
        source_items.append(
            TextItem(combined, target.x, target.y, max(item.font_size, target.font_size))
        )
        consumed.update({index, target_index})

    # Quantity prefixes are also commonly emitted separately from an otherwise
    # complete callout ("8x" + "R14.75", or "2x" + "19.5").
    for index, item in enumerate(texts):
        if index in consumed:
            continue
        quantity_match = re.fullmatch(r"\(?([0-9]+)[xX]\)?", item.text.strip())
        if not quantity_match:
            continue
        target_match = min(
            (
                (other_index, other)
                for other_index, other in enumerate(texts)
                if other_index not in consumed
                and other_index != index
                and re.fullmatch(r"R?[0-9]+(?:[.,][0-9]+)?", other.text.strip(), re.IGNORECASE)
                and abs(other.y - item.y) <= 3.0
                and 5.0 <= other.x - item.x <= 72.0
                and not is_inspection_label(other)
            ),
            key=lambda candidate: candidate[1].x - item.x,
            default=None,
        )
        if target_match is None:
            continue
        target_index, target = target_match
        target_text = target.text.strip()
        quantity = quantity_match.group(1)
        combined = (
            f"R({quantity}x){target_text[1:]}"
            if target_text.upper().startswith("R")
            else f"({quantity}x){target_text}"
        )
        source_items.append(TextItem(combined, item.x, item.y, max(item.font_size, target.font_size)))
        consumed.update({index, target_index})

    # Many CAD PDF writers emit the nominal and its tolerance as independent
    # text objects on the same baseline, with a lower deviation immediately
    # below the upper deviation. Reassemble those objects before parsing so
    # values such as "9 / 0.1 / 0.15" do not become three dimensions.
    for index, item in enumerate(texts):
        if index in consumed or is_inspection_label(item):
            continue
        raw = item.text.strip()
        if not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", raw):
            continue
        nominal = _parse_number(raw)
        if nominal is None or nominal <= 1:
            continue
        upper_match = min(
            (
                (other_index, other, float(other.text.replace(",", ".")))
                for other_index, other in enumerate(texts)
                if other_index not in consumed
                and other_index != index
                and re.fullmatch(r"\+?[0-9]+(?:[.,][0-9]+)?", other.text.strip())
                and abs(float(other.text.replace(",", "."))) <= 1
                and abs(other.y - item.y) <= 12.0
                and 8.0 <= other.x - item.x <= max(65.0, item.font_size * 7)
                and not is_inspection_label(other)
            ),
            key=lambda candidate: candidate[1].x - item.x,
            default=None,
        )
        if upper_match is None:
            continue
        upper_index, upper_item, upper = upper_match
        lower_match = min(
            (
                (other_index, other, float(other.text.replace(",", ".")))
                for other_index, other in enumerate(texts)
                if other_index not in consumed
                and other_index not in {index, upper_index}
                and re.fullmatch(r"-?[0-9]+(?:[.,][0-9]+)?", other.text.strip())
                and abs(float(other.text.replace(",", "."))) <= 1
                and abs(other.x - upper_item.x) <= 8.0
                and 4.0 <= abs(upper_item.y - other.y) <= 30.0
                and not is_inspection_label(other)
            ),
            key=lambda candidate: upper_item.y - candidate[1].y,
            default=None,
        )
        if lower_match:
            lower_index, _, lower = lower_match
            combined = f"{raw}+{abs(upper):g}/-{abs(lower):g}"
            consumed.add(lower_index)
        else:
            combined = f"{raw}±{upper:g}"
        source_items.append(TextItem(combined, item.x, item.y, item.font_size))
        consumed.update({index, upper_index})

    for index, item in enumerate(texts):
        if index in consumed:
            continue
        raw = " ".join(item.text.split()).strip()
        numeric_values = re.fullmatch(
            r"([0-9]+(?:[.,][0-9]+)?)\s+([0-9]+(?:[.,][0-9]+)?)"
            r"(?:\s+([0-9]+(?:[.,][0-9]+)?)\s+([0-9]+(?:[.,][0-9]+)?))?",
            raw,
        )
        if numeric_values:
            values = [_parse_number(value) for value in numeric_values.groups() if value is not None]
            pairs = list(zip(values[::2], values[1::2]))
            if pairs and all(
                nominal is not None
                and tolerance is not None
                and nominal > tolerance
                and tolerance <= max(1.0, nominal * 0.2)
                for nominal, tolerance in pairs
            ):
                for offset, (nominal, tolerance) in enumerate(pairs):
                    source_items.append(
                        TextItem(
                            text=f"{nominal:g}±{tolerance:g}",
                            x=item.x + offset * 36.0,
                            y=item.y,
                            font_size=item.font_size,
                        )
                    )
                continue
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
    excluded_candidates: list[dict[str, Any]] = []
    numeric_rows: dict[int, int] = {}
    for candidate in texts:
        numeric_tokens = re.findall(r"(?<!\d)\d{1,3}(?!\d)", candidate.text.strip())
        if numeric_tokens and re.fullmatch(r"\d{1,3}(?:\s+\d{1,3})*", candidate.text.strip()):
            row_key = round(candidate.y / 2.0)
            numeric_rows[row_key] = numeric_rows.get(row_key, 0) + len(numeric_tokens)
    for item in source_items:
        raw = " ".join(item.text.split()).strip()
        if re.fullmatch(r"\d{1,3}", raw) and is_inspection_label(item):
            inspection_label_count += 1
            continue
        if re.fullmatch(r"0\d+", raw):
            excluded_candidates.append(
                {
                    "raw_text": raw,
                    "anchor_pdf": [round(item.x, 3), round(item.y, 3)],
                    "reason": "zero_padded_inspection_or_revision_index",
                }
            )
            continue
        # Long, tolerance-free integers are overwhelmingly title-block metadata.
        if re.fullmatch(r"\d{4,}", raw):
            continue
        row_member_count = numeric_rows.get(round(item.y / 2.0), 0)
        is_hard_edge_index = (
            re.fullmatch(r"\d{1,3}", raw) is not None
            and (item.y >= page_height * 0.965 or item.y <= page_height * 0.035)
        )
        is_border_index_row = (
            re.fullmatch(r"\d{1,3}", raw) is not None
            and (row_member_count >= 6 or is_hard_edge_index)
            and (item.y >= page_height * 0.9 or item.y <= page_height * 0.1)
        )
        if is_border_index_row:
            excluded_candidates.append(
                {
                    "raw_text": raw,
                    "anchor_pdf": [round(item.x, 3), round(item.y, 3)],
                    "reason": "drawing_border_index_row",
                    "evidence": {
                        "numeric_tokens_on_row": row_member_count,
                        "page_edge": "top" if item.y >= page_height * 0.9 else "bottom",
                    },
                }
            )
            continue
        parsed = _parse_annotation(item.text)
        if parsed is None:
            continue
        nominal = float(parsed.get("nominal") or 0)
        standalone_small_number = (
            nominal <= 1
            and re.fullmatch(r"[+-]?[0-9]+(?:[.,][0-9]+)?", raw) is not None
        )
        if standalone_small_number:
            nearby_texts = [
                other.text.strip()
                for other in texts
                if other is not item
                and abs(other.y - item.y) <= 3.0
                and 8.0 <= other.x - item.x <= 105.0
            ]
            if any(text in {"A", "B", "C"} for text in nearby_texts):
                continue
        # Exclude obvious border/title-block tokens.  Ambiguous drawing-area integers
        # are retained with lower confidence so the UI still lists them for review.
        if item.x < 55 or item.y < max(80.0, page_height * 0.08):
            continue
        if item.x > page_width * 0.58 and item.y < page_height * 0.13:
            continue
        if item.x > page_width * 0.82 and item.y < page_height * 0.58:
            continue
        # Some CAD PDF writers expose a unit text matrix even though the visible
        # glyphs are roughly 8-10 pt high. A hard 8 pt floor for both axes keeps
        # leader seeding aligned with the actual rendered callout extent.
        effective_font_size = max(item.font_size, 8.0)
        text_width = max(effective_font_size * 0.55 * len(item.text), 8.0)
        text_height = effective_font_size
        text_bounds = (
            item.x,
            item.y - text_height * 0.25,
            item.x + text_width,
            item.y + text_height,
        )
        if standalone_small_number and is_inside_feature_control_frame(text_bounds):
            parsed.update(
                {
                    "semantic_type": "gdt_feature_control_frame",
                    "target_feature_type": "geometric_control",
                    "comparison_eligible": False,
                    "recognition_status": "numeric_cell_in_vector_frame",
                    "confidence": max(float(parsed.get("confidence") or 0), 0.9),
                }
            )
        elif nominal == 0 and any(
            re.fullmatch(r"-[0-9]+(?:[.,][0-9]+)?", other.text.strip())
            and abs(other.x - item.x) <= 8.0
            and 4.0 <= abs(other.y - item.y) <= 30.0
            for other in texts
        ):
            parsed.update(
                {
                    "semantic_type": "tolerance_fragment",
                    "target_feature_type": "annotation_context",
                    "comparison_eligible": False,
                    "recognition_status": "detached_stacked_tolerance",
                    "confidence": max(float(parsed.get("confidence") or 0), 0.9),
                }
            )
        entity = {
            "id": f"D2-{len(entities) + 1:03d}",
            "page": page_number,
            "raw_text": item.text,
            "anchor_pdf": [round(item.x, 3), round(item.y, 3)],
            "bbox_pdf": [
                round(text_bounds[0], 3),
                round(text_bounds[1], 3),
                round(text_bounds[2], 3),
                round(text_bounds[3], 3),
            ],
            "view_id": "PAGE_1_UNASSIGNED",
            "status": "unbound",
            "source_method": "pypdf_text_object",
            "font_size": round(float(item.font_size or 0), 3),
            **parsed,
        }
        entities.append(entity)

    # Preserve standalone nX tokens as auditable context rows. They remain
    # linked to the assembled parent dimension when one is nearby, but are not
    # independently compared with CAD geometry.
    for item in texts:
        quantity_match = re.fullmatch(r"\(?([0-9]+)[xX]\)?", item.text.strip())
        if not quantity_match or is_inspection_label(item):
            continue
        if item.x < 55 or item.y < max(80.0, page_height * 0.08):
            continue
        if item.x > page_width * 0.82 and item.y < page_height * 0.58:
            continue
        aligned_parent = min(
            (
                (
                    math.dist(
                        (item.x, item.y),
                        tuple(float(value) for value in entity.get("anchor_pdf", [0, 0])),
                    ),
                    entity,
                )
                for entity in entities
                if entity.get("comparison_eligible") is not False
                and entity.get("semantic_type") in {"linear_dimension", "radius"}
                and int(entity.get("quantity") or 1) == 1
                and abs(float((entity.get("anchor_pdf") or [0, 0])[1]) - item.y) <= 30.0
                and abs(float((entity.get("anchor_pdf") or [0, 0])[0]) - item.x) <= 240.0
            ),
            key=lambda candidate: candidate[0],
            default=None,
        )
        nearest = min(
            (
                (
                    math.dist(
                        (item.x, item.y),
                        tuple(float(value) for value in entity.get("anchor_pdf", [0, 0])),
                    ),
                    entity,
                )
                for entity in entities
                if entity.get("comparison_eligible") is not False
            ),
            key=lambda candidate: candidate[0],
            default=None,
        )
        has_near_parent = bool(nearest and nearest[0] <= 90.0)
        use_aligned_parent = aligned_parent is not None and not has_near_parent
        parent = (
            nearest[1]
            if has_near_parent
            else aligned_parent[1]
            if use_aligned_parent
            else None
        )
        parent_id = parent["id"] if parent else None
        if use_aligned_parent:
            parent["quantity"] = int(quantity_match.group(1))
            parent["quantity_binding"] = {
                "method": "same_baseline_detached_multiplier",
                "source_anchor_pdf": [round(item.x, 3), round(item.y, 3)],
                "distance_pdf": round(float(aligned_parent[0]), 3),
            }
        entities.append(
            {
                "id": f"D2-{len(entities) + 1:03d}",
                "page": page_number,
                "raw_text": item.text.strip(),
                "anchor_pdf": [round(item.x, 3), round(item.y, 3)],
                "bbox_pdf": [
                    round(item.x, 3), round(item.y - 2.0, 3),
                    round(item.x + max(8.0, len(item.text) * 4.4), 3),
                    round(item.y + 8.0, 3),
                ],
                "view_id": "PAGE_1_UNASSIGNED",
                "status": "context_only",
                "source_method": "pypdf_quantity_context",
                "semantic_type": "quantity_multiplier",
                "nominal": None,
                "unit": None,
                "quantity": int(quantity_match.group(1)),
                "target_feature_type": "annotation_context",
                "confidence": 0.96 if parent_id else 0.72,
                "comparison_eligible": False,
                "parent_entity_id": parent_id,
                "skip_context_binding": True,
            }
        )

    linked_quantity_parents = {
        str(entity.get("parent_entity_id"))
        for entity in entities
        if entity.get("semantic_type") == "quantity_multiplier"
        and entity.get("parent_entity_id")
    }
    repeated_entities = [
        entity for entity in entities
        if entity.get("comparison_eligible") is not False
        and int(entity.get("quantity") or 1) > 1
        and entity["id"] not in linked_quantity_parents
    ]
    for parent in repeated_entities:
        quantity = int(parent["quantity"])
        anchor = parent.get("anchor_pdf") or [0, 0]
        entities.append(
            {
                "id": f"D2-{len(entities) + 1:03d}",
                "page": page_number,
                "raw_text": f"({quantity}x)",
                "anchor_pdf": list(anchor),
                "bbox_pdf": list(parent.get("bbox_pdf") or [*anchor, *anchor]),
                "view_id": parent.get("view_id", "PAGE_1_UNASSIGNED"),
                "status": "context_only",
                "source_method": "derived_quantity_context",
                "semantic_type": "quantity_multiplier",
                "nominal": None,
                "unit": None,
                "quantity": quantity,
                "target_feature_type": "annotation_context",
                "confidence": 0.92,
                "comparison_eligible": False,
                "parent_entity_id": parent["id"],
                "skip_context_binding": True,
            }
        )

    diagnostics = {
        "page": page_number,
        "text_object_count": len(graph["texts"]),
        "entity_count": len(entities),
        "inspection_label_count": inspection_label_count,
        "excluded_candidate_count": len(excluded_candidates),
        "excluded_candidates": excluded_candidates,
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
    bind_drawing_context(graph, drawing_entities)
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
        "excluded_drawing_entities": entity_diagnostics.get("excluded_candidates", []),
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
        "excluded_drawing_entities": entity_diagnostics.get("excluded_candidates", []),
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
        "context_binding": {
            "leader_targets": sum("leader_target_pdf" in item for item in drawing_entities),
            "assigned_regions": len(
                {
                    item["view_id"]
                    for item in drawing_entities
                    if item.get("status") == "context_bound"
                }
            ),
        },
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
    bind_drawing_context(graph, drawing_entities)
    texts: list[TextItem] = graph["texts"]
    paths: list[VectorPath] = graph["paths"]
    candidates: list[dict[str, Any]] = []
    numeric_pattern = re.compile(r"^[<>]?[+-]?[0-9]+(?:\.[0-9]+)?(?:\s*(?:±|\+/-)\s*[0-9.]+)?$")
    engineering_marker = re.compile(
        r"(?:[Øø⌀±°]|\bREF\b|\bTYP\b|\bMAX\b|\bMIN\b|\bR\s*[0-9]|[0-9]\s*[xX]\s*[Øø⌀]?)",
        re.IGNORECASE,
    )
    excluded_positions = {
        (
            str(item.get("raw_text") or ""),
            round(float((item.get("anchor_pdf") or [0, 0])[0])),
            round(float((item.get("anchor_pdf") or [0, 0])[1])),
        )
        for item in entity_diagnostics.get("excluded_candidates", [])
    }
    seen: set[tuple[str, int, int]] = set()
    for item in texts:
        value = " ".join(item.text.split())
        if not value or len(value) > 160:
            continue
        if (value, round(item.x), round(item.y)) in excluded_positions:
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
        "excluded_drawing_entities": entity_diagnostics.get("excluded_candidates", []),
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
        "excluded_drawing_entities": entity_diagnostics.get("excluded_candidates", []),
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
        "context_binding": {
            "leader_targets": sum("leader_target_pdf" in item for item in drawing_entities),
            "assigned_regions": len(
                {
                    item["view_id"]
                    for item in drawing_entities
                    if item.get("status") == "context_bound"
                }
            ),
        },
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
