from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from service.agent.model import QwenModelClient
from service.agent.vision import multimodal_user_content
from service.correspondence import build_manufacturing_specification


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
    views = graph.get("views") or []
    assignments: dict[str, list[dict[str, Any]]] = {}
    for entity in plan.get("drawing_entities") or []:
        anchor = entity.get("leader_target_pdf") or entity.get("anchor_pdf") or []
        if len(anchor) != 2:
            continue
        point = [float(anchor[0]) / width, 1 - float(anchor[1]) / height]
        matches = [view for view in views if _contains(view, point)]
        if not matches:
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
            }
        )
        assignments.setdefault(view["id"], []).append(entity)
    for entities in assignments.values():
        for entity in entities:
            entity["view_region_size"] = len(entities)
            entity["context_confidence"] = max(float(entity.get("context_confidence") or 0), float(entity.get("view_confidence") or 0) * 0.8)
    vector["drawing_entities"] = plan.get("drawing_entities") or []
    vector["view_graph"] = graph
    plan["view_graph"] = graph
    specification = build_manufacturing_specification(plan, analysis, comparison)
    previous = (comparison.get("manufacturing_specification") or {}).get("summary") or {}
    comparison.update(
        {
            "manufacturing_specification": specification,
            "drawing_entities": specification["drawing_entities"],
            "cad_features": specification["cad_features"],
            "mappings": specification["mappings"],
            "comparison_rows": specification["comparison_rows"],
            "view_intelligence": {
                "source": graph.get("source"),
                "model": graph.get("model"),
                "views": len(views),
                "assigned_entities": sum(len(items) for items in assignments.values()),
                "previous_summary": previous,
                "updated_summary": specification["summary"],
            },
        }
    )
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    vector_path.write_text(json.dumps(vector, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "manufacturing_specification.json").write_text(json.dumps(specification, ensure_ascii=False, indent=2), encoding="utf-8")
    comparison_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    return comparison
