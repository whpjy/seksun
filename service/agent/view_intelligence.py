from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from service.agent.model import QwenModelClient
from service.agent.vision import multimodal_user_content
from service.correspondence import build_manufacturing_specification, cad_measurement_features


GRAPH_NAME = "drawing_view_graph.json"
MODEL_IO_NAME = "view_model_io.json"
VIEW_TYPES = {"front", "top", "right", "bottom", "rear", "section", "detail", "auxiliary", "unknown"}
PROJECTION_IDS = {"front", "top", "right"}
PROJECTION_FRAMES = {
    "front": ([0.0, -1.0, 0.0], [1.0, 0.0, 0.0]),
    "top": ([0.0, 0.0, 1.0], [1.0, 0.0, 0.0]),
    "right": ([-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]),
}

VIEW_PROMPT = """分析第一个图像中的工程图纸页面，并将它与随后三个由同一STEP模型生成的OCCT投影视图比较。
只返回一个JSON对象，不要Markdown，不要解释。不要从像素估算工程尺寸。
识别图纸中的独立视图区域，排除标题栏、边框、说明文字和表格。
bbox_normalized使用图纸图像左上角为原点，坐标范围0到1，格式[x1,y1,x2,y2]。
matched_projection_id只能是front、top、right或null；只有轮廓、孔位布局和朝向有明确依据时才填写。
对于剖视图、局部视图和辅助视图，不要强行匹配固定三视图；可给出建议的observation_direction和x_direction。
输出结构：
{
  "projection_method": "first_angle|third_angle|unknown",
  "views": [{
    "id": "VIEW_01",
    "type": "front|top|right|bottom|rear|section|detail|auxiliary|unknown",
    "label": "A-A或局部视图标签，没有则为null",
    "bbox_normalized": [0.0,0.0,1.0,1.0],
    "matched_projection_id": "front|top|right|null",
    "observation_direction": [0,-1,0],
    "x_direction": [1,0,0],
    "confidence": 0.0,
    "evidence": "简短可公开的视觉依据"
  }],
  "uncertainties": ["无法确定的视图关系"]
}
宁可输出unknown或null，也不要制造确定关系。最多输出16个视图。"""


def _json_object(content: str) -> dict[str, Any] | None:
    candidate = content.strip()
    first = candidate.find("{")
    if first < 0:
        return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(candidate[first:])
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", candidate, re.IGNORECASE)
        if not fenced:
            return None
        try:
            parsed = json.loads(fenced.group(1))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _vector(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return result if sum(item * item for item in result) > 1.0e-8 else None


def _normalize_graph(payload: dict[str, Any], model: str, source: str) -> dict[str, Any]:
    views: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, raw in enumerate(payload.get("views") or []):
        if not isinstance(raw, dict):
            continue
        bbox = raw.get("bbox_normalized")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            left, top, right, bottom = [max(0.0, min(1.0, float(value))) for value in bbox]
        except (TypeError, ValueError):
            continue
        if right - left < 0.02 or bottom - top < 0.02:
            continue
        proposed_id = str(raw.get("id") or f"VIEW_{index + 1:02d}").upper()
        view_id = proposed_id if re.fullmatch(r"[A-Z0-9_-]{1,40}", proposed_id) else f"VIEW_{index + 1:02d}"
        while view_id in used:
            view_id = f"VIEW_{index + 1:02d}_{len(used) + 1}"
        used.add(view_id)
        view_type = str(raw.get("type") or "unknown").lower()
        matched = str(raw.get("matched_projection_id") or "").lower() or None
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        direction = _vector(raw.get("observation_direction"))
        x_direction = _vector(raw.get("x_direction"))
        if matched in PROJECTION_FRAMES:
            direction, x_direction = PROJECTION_FRAMES[matched]
        views.append(
            {
                "id": view_id,
                "type": view_type if view_type in VIEW_TYPES else "unknown",
                "label": str(raw.get("label"))[:80] if raw.get("label") else None,
                "bbox_normalized": [round(left, 6), round(top, 6), round(right, 6), round(bottom, 6)],
                "matched_projection_id": matched if matched in PROJECTION_IDS else None,
                "observation_direction": direction,
                "x_direction": x_direction,
                "confidence": round(confidence, 4),
                "evidence": str(raw.get("evidence") or "")[:300],
            }
        )
        if len(views) >= 16:
            break
    projection_method = str(payload.get("projection_method") or "unknown").lower()
    if projection_method not in {"first_angle", "third_angle", "unknown"}:
        projection_method = "unknown"
    return {
        "schema_version": "0.1.0",
        "source": source,
        "model": model,
        "projection_method": projection_method,
        "views": views,
        "uncertainties": [str(item)[:300] for item in (payload.get("uncertainties") or [])[:20]],
    }


def _fallback_graph(directory: Path, model: str, reason: str) -> dict[str, Any]:
    plan = json.loads((directory / "measurement_plan.json").read_text(encoding="utf-8"))
    vector = json.loads((directory / "vector_extraction.json").read_text(encoding="utf-8"))
    page = vector.get("page") or {}
    width, height = float(page.get("width") or 1), float(page.get("height") or 1)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entity in plan.get("drawing_entities") or []:
        view_id = str(entity.get("view_id") or "")
        if view_id and not view_id.endswith("UNASSIGNED"):
            grouped.setdefault(view_id, []).append(entity)
    views = []
    for view_id, entities in grouped.items():
        bounds = [item.get("view_region_pdf") for item in entities if len(item.get("view_region_pdf") or []) == 4]
        if not bounds:
            continue
        left = min(float(item[0]) for item in bounds) / width
        right = max(float(item[2]) for item in bounds) / width
        bottom = min(float(item[1]) for item in bounds) / height
        top = max(float(item[3]) for item in bounds) / height
        views.append(
            {
                "id": view_id,
                "type": "unknown",
                "label": None,
                "bbox_normalized": [round(left, 6), round(1 - top, 6), round(right, 6), round(1 - bottom, 6)],
                "matched_projection_id": None,
                "observation_direction": None,
                "x_direction": None,
                "confidence": 0.35,
                "evidence": "由PDF引出线终点聚类生成的确定性回退区域",
            }
        )
    return {
        "schema_version": "0.1.0",
        "source": "deterministic_fallback",
        "model": model,
        "projection_method": "unknown",
        "views": views[:16],
        "uncertainties": [reason[:300]],
    }


def understand_drawing_views(directory: Path) -> dict[str, Any]:
    client = QwenModelClient()
    client.timeout = max(
        client.timeout,
        float(os.getenv("AGENT_VISION_TIMEOUT_SECONDS", "300")),
    )
    model = os.getenv("AGENT_VISION_MODEL", os.getenv("AGENT_DEFAULT_MODEL", "qwen3.7-plus"))
    try:
        reply = client.complete(
            model=model,
            messages=[
                {"role": "system", "content": "你是工程制图视图理解器。只输出符合用户模式的JSON，不进行尺寸合格判断。"},
                {"role": "user", "content": multimodal_user_content(directory, VIEW_PROMPT)},
            ],
            tools=[],
            round_index=0,
        )
        payload = _json_object(reply.content)
        if payload is None:
            raise ValueError("vision model did not return a JSON object")
        graph = _normalize_graph(payload, model, "multimodal_model")
        if not graph["views"]:
            raise ValueError("vision model returned no valid drawing views")
        model_io = {"model": model, "mode": "mock" if client.uses_mock else "live", "response": reply.raw, "usage": reply.usage}
    except Exception as exc:
        graph = _fallback_graph(directory, model, str(exc))
        model_io = {"model": model, "mode": "fallback", "error": str(exc)}
    (directory / GRAPH_NAME).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / MODEL_IO_NAME).write_text(json.dumps(model_io, ensure_ascii=False, indent=2), encoding="utf-8")
    return graph


def _contains(view: dict[str, Any], point: list[float]) -> bool:
    left, top, right, bottom = view["bbox_normalized"]
    return left <= point[0] <= right and top <= point[1] <= bottom


def _unit(value: Any, fallback: list[float]) -> list[float]:
    vector = _vector(value) or fallback
    length = math.sqrt(sum(item * item for item in vector))
    return [item / length for item in vector]


def _project_feature_normalized(
    feature: dict[str, Any],
    direction_value: Any,
    x_value: Any,
    bounds: tuple[list[float], list[float]],
) -> list[float] | None:
    direction = _unit(direction_value, [0.0, -1.0, 0.0])
    x_direction = _unit(x_value, [1.0, 0.0, 0.0])
    dot = sum(direction[index] * x_direction[index] for index in range(3))
    x_direction = [x_direction[index] - direction[index] * dot for index in range(3)]
    x_length = math.sqrt(sum(item * item for item in x_direction))
    if x_length <= 1.0e-8:
        return None
    x_direction = [item / x_length for item in x_direction]
    y_direction = [
        direction[1] * x_direction[2] - direction[2] * x_direction[1],
        direction[2] * x_direction[0] - direction[0] * x_direction[2],
        direction[0] * x_direction[1] - direction[1] * x_direction[0],
    ]
    minimum, maximum = bounds
    corners = [
        [x, y, z]
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]
    projected_x = [sum(point[index] * x_direction[index] for index in range(3)) for point in corners]
    projected_y = [sum(point[index] * y_direction[index] for index in range(3)) for point in corners]
    center = feature.get("center") or []
    if not isinstance(center, list) or len(center) != 3:
        return None
    center_x = sum(float(center[index]) * x_direction[index] for index in range(3))
    center_y = sum(float(center[index]) * y_direction[index] for index in range(3))
    return [
        (center_x - min(projected_x)) / max(max(projected_x) - min(projected_x), 1.0e-9),
        (center_y - min(projected_y)) / max(max(projected_y) - min(projected_y), 1.0e-9),
    ]


def _registration_candidates(entity: dict[str, Any], features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semantic = entity.get("semantic_type")
    nominal = entity.get("nominal")
    if nominal is None or semantic not in {"radius", "diameter", "linear_dimension"}:
        return []
    nominal = float(nominal)
    epsilon = max(0.2, abs(nominal) * 0.02)
    radius_types = {"fillet_radius", "bend_radius", "torus_radius"}
    diameter_types = {"cylindrical_feature", "hole_axis_group"}
    result = []
    for feature in features:
        kind = feature.get("type")
        if semantic == "radius" and kind not in radius_types:
            continue
        if semantic == "diameter" and kind not in diameter_types:
            continue
        if semantic == "linear_dimension" and (kind in radius_types or kind in diameter_types):
            continue
        if abs(float(feature.get("value") or 0) - nominal) <= epsilon:
            result.append(feature)
    return result


def _fit_axis(values: list[tuple[float, float]]) -> tuple[float, float] | None:
    mean_source = sum(item[0] for item in values) / len(values)
    mean_target = sum(item[1] for item in values) / len(values)
    variance = sum((item[0] - mean_source) ** 2 for item in values)
    if variance <= 1.0e-6:
        return None
    slope = sum((source - mean_source) * (target - mean_target) for source, target in values) / variance
    if not 0.3 <= abs(slope) <= 3.0:
        return None
    return slope, mean_target - slope * mean_source


def _build_view_registration(
    view: dict[str, Any],
    entities: list[dict[str, Any]],
    analysis: dict[str, Any],
    features: list[dict[str, Any]],
    seed_feature_ids: dict[str, list[str]],
    projection_size: list[float] | None,
) -> dict[str, Any]:
    left, top, right, bottom = view["bbox_normalized"]
    width, height = max(right - left, 1.0e-9), max(bottom - top, 1.0e-9)
    box = ((analysis.get("measurements") or {}).get("bounding_box") or {})
    minimum = [float(item) for item in (box.get("min") or [0, 0, 0])]
    maximum = [float(item) for item in (box.get("max") or [0, 0, 0])]
    page_width = float((analysis.get("_drawing_page") or {}).get("width") or 1)
    page_height = float((analysis.get("_drawing_page") or {}).get("height") or 1)
    feature_index = {str(item.get("id")): item for item in features}
    landmarks: list[dict[str, Any]] = []
    used_feature_ids: set[str] = set()
    for entity in entities:
        seeded = [
            feature_index[item]
            for item in seed_feature_ids.get(str(entity.get("id")), [])
            if item in feature_index
        ]
        candidates = seeded or _registration_candidates(entity, features)
        if len(candidates) != 1:
            continue
        if str(candidates[0]["id"]) in used_feature_ids:
            continue
        target = entity.get("leader_target_pdf") or entity.get("anchor_pdf") or []
        if len(target) != 2:
            continue
        drawing = [
            (float(target[0]) / page_width - left) / width,
            ((1 - float(target[1]) / page_height) - top) / height,
        ]
        model = _project_feature_normalized(
            candidates[0], view.get("observation_direction"), view.get("x_direction"), (minimum, maximum)
        )
        if model is None:
            continue
        landmarks.append(
            {
                "drawing_entity_id": entity["id"],
                "cad_feature_id": candidates[0]["id"],
                "source": "deterministic_unique_mapping" if seeded else "unique_numeric_candidate",
                "drawing": drawing,
                "model": model,
            }
        )
        used_feature_ids.add(str(candidates[0]["id"]))

    matrix = [1.0, 0.0, 0.0, 0.0, -1.0, 1.0]
    method = "view_bbox_normalization"
    if projection_size and projection_size[0] > 0 and projection_size[1] > 0:
        drawing_aspect = (width * page_width) / max(height * page_height, 1.0e-9)
        projection_aspect = projection_size[0] / projection_size[1]
        if drawing_aspect > projection_aspect:
            silhouette_fraction = projection_aspect / drawing_aspect
            padding = (1 - silhouette_fraction) / 2
            matrix = [1 / silhouette_fraction, 0.0, -padding / silhouette_fraction, 0.0, -1.0, 1.0]
        else:
            silhouette_fraction = drawing_aspect / projection_aspect
            padding = (1 - silhouette_fraction) / 2
            matrix = [1.0, 0.0, 0.0, 0.0, -1 / silhouette_fraction, 1 + padding / silhouette_fraction]
        method = "projection_aspect_affine"
    fallback_matrix = list(matrix)
    fallback_method = method
    residual = None
    inliers = landmarks
    if len(landmarks) >= 3:
        best_inliers: list[dict[str, Any]] = []
        best_error = float("inf")
        for first_index in range(len(landmarks) - 1):
            for second_index in range(first_index + 1, len(landmarks)):
                first, second = landmarks[first_index], landmarks[second_index]
                drawing_dx = second["drawing"][0] - first["drawing"][0]
                drawing_dy = second["drawing"][1] - first["drawing"][1]
                if abs(drawing_dx) <= 0.04 or abs(drawing_dy) <= 0.04:
                    continue
                slope_x = (second["model"][0] - first["model"][0]) / drawing_dx
                slope_y = (second["model"][1] - first["model"][1]) / drawing_dy
                if not 0.3 <= abs(slope_x) <= 3.0 or not 0.3 <= abs(slope_y) <= 3.0:
                    continue
                candidate_matrix = [
                    slope_x,
                    0.0,
                    first["model"][0] - slope_x * first["drawing"][0],
                    0.0,
                    slope_y,
                    first["model"][1] - slope_y * first["drawing"][1],
                ]
                errors = [
                    math.hypot(
                        candidate_matrix[0] * item["drawing"][0] + candidate_matrix[2] - item["model"][0],
                        candidate_matrix[4] * item["drawing"][1] + candidate_matrix[5] - item["model"][1],
                    ) / math.sqrt(2)
                    for item in landmarks
                ]
                candidate_inliers = [item for item, error in zip(landmarks, errors) if error <= 0.12]
                candidate_error = sum(error for error in errors if error <= 0.12)
                if len(candidate_inliers) > len(best_inliers) or (
                    len(candidate_inliers) == len(best_inliers) and candidate_error < best_error
                ):
                    best_inliers, best_error = candidate_inliers, candidate_error
        minimum_inliers = 3 if len(landmarks) == 3 else max(4, math.ceil(len(landmarks) * 0.35))
        if len(best_inliers) >= minimum_inliers:
            fit_x = _fit_axis([(item["drawing"][0], item["model"][0]) for item in best_inliers])
            fit_y = _fit_axis([(item["drawing"][1], item["model"][1]) for item in best_inliers])
            if fit_x and fit_y:
                candidate_matrix = [fit_x[0], 0.0, fit_x[1], 0.0, fit_y[0], fit_y[1]]
                errors = [
                    math.hypot(
                        candidate_matrix[0] * item["drawing"][0] + candidate_matrix[2] - item["model"][0],
                        candidate_matrix[4] * item["drawing"][1] + candidate_matrix[5] - item["model"][1],
                    ) / math.sqrt(2)
                    for item in landmarks
                ]
                inliers = [item for item, error in zip(landmarks, errors) if error <= 0.12]
            else:
                inliers = []
            if len(inliers) >= minimum_inliers:
                fit_x = _fit_axis([(item["drawing"][0], item["model"][0]) for item in inliers])
                fit_y = _fit_axis([(item["drawing"][1], item["model"][1]) for item in inliers])
                if fit_x and fit_y:
                    matrix = [fit_x[0], 0.0, fit_x[1], 0.0, fit_y[0], fit_y[1]]
                    residuals = [
                        math.hypot(
                            matrix[0] * item["drawing"][0] + matrix[2] - item["model"][0],
                            matrix[4] * item["drawing"][1] + matrix[5] - item["model"][1],
                        ) / math.sqrt(2)
                        for item in inliers
                    ]
                    residual = math.sqrt(sum(error * error for error in residuals) / len(residuals))
                    if residual <= 0.1:
                        method = "numeric_landmark_affine"
                    else:
                        matrix = fallback_matrix
                        method = fallback_method
                        inliers = []
                        residual = None
    confidence = (
        min(0.95, float(view.get("confidence") or 0.5) * min(1.0, len(inliers) / 6) * max(0.0, 1 - float(residual or 0) / 0.2))
        if method == "numeric_landmark_affine"
        else min(
            0.7 if method == "projection_aspect_affine" else 0.55,
            float(view.get("confidence") or 0.5) * (0.72 if method == "projection_aspect_affine" else 0.55),
        )
    )
    return {
        "method": method,
        "coordinate_system": "drawing_view_top_left_to_step_projection_bottom_left",
        "matrix_2x3": [round(item, 6) for item in matrix],
        "landmark_count": len(landmarks),
        "inlier_count": len(inliers) if method == "numeric_landmark_affine" else 0,
        "rmse_normalized": round(residual, 6) if residual is not None and method == "numeric_landmark_affine" else None,
        "confidence": round(confidence, 4),
        "landmarks": inliers[:20] if method == "numeric_landmark_affine" else [],
        "candidate_landmarks": landmarks[:20] if method != "numeric_landmark_affine" else [],
    }


def apply_view_graph(directory: Path, graph: dict[str, Any]) -> dict[str, Any]:
    plan_path = directory / "measurement_plan.json"
    vector_path = directory / "vector_extraction.json"
    comparison_path = directory / "comparison.json"
    analysis_path = directory / "analysis.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    vector = json.loads(vector_path.read_text(encoding="utf-8"))
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    page = vector.get("page") or {}
    width, height = float(page.get("width") or 1), float(page.get("height") or 1)
    analysis["_drawing_page"] = {"width": width, "height": height}
    views = graph.get("views") or []
    assignments: dict[str, list[dict[str, Any]]] = {}
    excluded: list[dict[str, Any]] = []
    for entity in plan.get("drawing_entities") or []:
        anchor = entity.get("leader_target_pdf") or entity.get("anchor_pdf") or []
        if len(anchor) != 2:
            continue
        point = [float(anchor[0]) / width, 1 - float(anchor[1]) / height]
        matches = [view for view in views if _contains(view, point)]
        if not matches:
            raw = str(entity.get("raw_text") or "")
            weak_unbound_number = (
                entity.get("status") == "unbound"
                and entity.get("semantic_type") == "linear_dimension"
                and entity.get("leader_target_pdf") is None
                and re.fullmatch(r"[+-]?\d+(?:[.,]\d+)?", raw.strip()) is not None
            )
            if weak_unbound_number:
                entity["comparison_eligible"] = False
                entity["exclusion_reason"] = "outside_detected_drawing_views"
                excluded.append(entity)
            continue
        view = min(matches, key=lambda item: (item["bbox_normalized"][2] - item["bbox_normalized"][0]) * (item["bbox_normalized"][3] - item["bbox_normalized"][1]))
        left, top, right, bottom = view["bbox_normalized"]
        entity.update(
            {
                "view_id": view["id"],
                "status": "ai_view_bound",
                "view_type": view.get("type"),
                "view_confidence": view.get("confidence"),
                "view_region_pdf": [round(left * width, 3), round((1 - bottom) * height, 3), round(right * width, 3), round((1 - top) * height, 3)],
                "view_direction_hint": view.get("observation_direction"),
                "view_x_direction_hint": view.get("x_direction"),
                "matched_projection_id": view.get("matched_projection_id"),
                "comparison_eligible": True,
            }
        )
        assignments.setdefault(view["id"], []).append(entity)
    features = cad_measurement_features(analysis)
    projection_sizes: dict[str, list[float]] = {}
    manifest_path = directory / "views" / "views.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        projection_sizes = {
            str(item.get("id")): [float(item.get("width") or 0), float(item.get("height") or 0)]
            for item in manifest.get("views") or []
        }
    seed_feature_ids = {
        str(item.get("drawing_entity_id")): list(item.get("cad_feature_ids") or [])
        for item in ((comparison.get("manufacturing_specification") or {}).get("mappings") or [])
        if item.get("status") == "matched" and len(item.get("cad_feature_ids") or []) == 1
    }
    registrations: dict[str, dict[str, Any]] = {}
    for view in views:
        assigned = assignments.get(view.get("id"), [])
        if not assigned or not view.get("observation_direction") or not view.get("x_direction"):
            continue
        registration = _build_view_registration(
            view,
            assigned,
            analysis,
            features,
            seed_feature_ids,
            projection_sizes.get(str(view.get("matched_projection_id") or "")),
        )
        view["registration"] = registration
        registrations[str(view["id"])] = registration
        for entity in assigned:
            entity["view_registration"] = registration
    for entities in assignments.values():
        for entity in entities:
            entity["view_region_size"] = len(entities)
            entity["context_confidence"] = max(float(entity.get("context_confidence") or 0), float(entity.get("view_confidence") or 0) * 0.8)
    vector["drawing_entities"] = plan.get("drawing_entities") or []
    vector["view_graph"] = graph
    plan["view_graph"] = graph
    specification = build_manufacturing_specification(plan, analysis, comparison)
    previous = (comparison.get("manufacturing_specification") or {}).get("summary") or {}
    affine_count = sum(
        item.get("method") == "numeric_landmark_affine" for item in registrations.values()
    )
    updated_summary = specification["summary"]
    accepted = bool(
        affine_count > 0
        or int(updated_summary.get("matched") or 0) >= int(previous.get("matched") or 0)
    )
    view_intelligence = {
        "source": graph.get("source"),
        "model": graph.get("model"),
        "views": len(views),
        "assigned_entities": sum(len(items) for items in assignments.values()),
        "excluded_entities": len(excluded),
        "registered_views": len(registrations),
        "affine_registered_views": affine_count,
        "accepted": accepted,
        "decision": "accepted" if accepted else "rejected_no_measured_improvement",
        "previous_summary": previous,
        "updated_summary": updated_summary,
    }
    (directory / GRAPH_NAME).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    if not accepted:
        comparison["view_intelligence"] = view_intelligence
        comparison["excluded_drawing_entities"] = []
        comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        analysis.pop("_drawing_page", None)
        return comparison
    comparison.update(
        {
            "manufacturing_specification": specification,
            "drawing_entities": specification["drawing_entities"],
            "cad_features": specification["cad_features"],
            "mappings": specification["mappings"],
            "comparison_rows": specification["comparison_rows"],
            "view_intelligence": view_intelligence,
            "excluded_drawing_entities": [
                {"id": item.get("id"), "raw_text": item.get("raw_text"), "reason": item.get("exclusion_reason")}
                for item in excluded
            ],
        }
    )
    analysis.pop("_drawing_page", None)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    vector_path.write_text(json.dumps(vector, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "manufacturing_specification.json").write_text(json.dumps(specification, ensure_ascii=False, indent=2), encoding="utf-8")
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    return comparison
