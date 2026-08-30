from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable

from service.agent.vision import rasterize_svg
from service.agent.view_intelligence import apply_view_graph


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _read_json(directory: Path, name: str) -> dict[str, Any]:
    return json.loads((directory / name).read_text(encoding="utf-8"))


class ComparisonToolRegistry:
    def __init__(self, directory: Path):
        self.directory = directory
        self._spatial_view_count = 0
        self._spatial_directions: list[list[float]] = []
        self._handlers: dict[str, ToolHandler] = {
            "inspect_drawing_view_graph": self.inspect_drawing_view_graph,
            "inspect_drawing_requirements": self.inspect_drawing_requirements,
            "inspect_cad_features": self.inspect_cad_features,
            "inspect_comparison_evidence": self.inspect_comparison_evidence,
            "render_spatial_view": self.render_spatial_view,
            "register_drawing_view_projection": self.register_drawing_view_projection,
        }

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "inspect_drawing_view_graph",
                    "description": "Read the structured multimodal interpretation of drawing views, view boxes, projection hints and uncertainties.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_drawing_requirements",
                    "description": "Read requirements extracted from the 2D PDF. Values are evidence and must not be altered.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_cad_features",
                    "description": "Read a bounded summary of exact STEP/B-Rep features produced by Open CASCADE.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_comparison_evidence",
                    "description": "Read deterministic 2D-to-3D registration and tolerance evidence.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "render_spatial_view",
                    "description": "Ask Open CASCADE to generate a new exact hidden-line projection from an arbitrary observation direction. Use this to resolve spatial ambiguity; the returned PNG is shown to you in the next message.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "direction": {
                                "type": "array",
                                "items": {"type": "number", "minimum": -1, "maximum": 1},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "3D camera observation direction vector",
                            },
                            "x_direction": {
                                "type": "array",
                                "items": {"type": "number", "minimum": -1, "maximum": 1},
                                "minItems": 3,
                                "maxItems": 3,
                                "description": "Approximate horizontal direction in the projected image; must not be parallel to direction",
                            },
                            "purpose": {
                                "type": "string",
                                "maxLength": 240,
                                "description": "Concise public reason for choosing this view",
                            },
                            "focus_feature_id": {
                                "type": "string",
                                "maxLength": 80,
                                "description": "Optional PDF feature, comparison feature, or OCCT hole-group id to focus. The service resolves it to an exact OCCT group and falls back to the deterministic failed feature when needed.",
                            },
                            "based_on_view_id": {
                                "type": "string",
                                "maxLength": 64,
                                "description": "Previous exploration view that motivated this complementary direction",
                            },
                        },
                        "required": ["direction", "x_direction", "purpose"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "register_drawing_view_projection",
                    "description": "Confirm a drawing view against a canonical or rendered OCCT projection, persist its camera frame, and recompute candidate ranking.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "drawing_view_id": {"type": "string", "maxLength": 64},
                            "projection_view_id": {
                                "type": "string",
                                "maxLength": 64,
                                "description": "front, top, right, or a rendered explore_NN id",
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "evidence": {"type": "string", "minLength": 1, "maxLength": 300},
                        },
                        "required": ["drawing_view_id", "projection_view_id", "confidence", "evidence"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError(f"Unsupported agent tool: {name}")
        return handler(arguments)

    def inspect_drawing_requirements(self, _: dict[str, Any]) -> dict[str, Any]:
        plan = _read_json(self.directory, "measurement_plan.json")
        vector = _read_json(self.directory, "vector_extraction.json")
        return {
            "scope": plan.get("scope", {}),
            "extraction": plan.get("extraction", {}),
            "measurements": plan.get("measurements", []),
            "drawing_entities": plan.get("drawing_entities", [])[:200],
            "requirement_candidates": plan.get("requirement_candidates", [])[:100],
            "annotation_bindings": vector.get("annotation_bindings", []),
            "annotation_candidates": vector.get("annotation_candidates", [])[:100],
            "vector_features": vector.get("vector_features", []),
        }

    def inspect_drawing_view_graph(self, _: dict[str, Any]) -> dict[str, Any]:
        path = self.directory / "drawing_view_graph.json"
        if not path.is_file():
            return {"source": "unavailable", "views": [], "uncertainties": ["drawing view graph is unavailable"]}
        return json.loads(path.read_text(encoding="utf-8"))

    def inspect_cad_features(self, _: dict[str, Any]) -> dict[str, Any]:
        analysis = _read_json(self.directory, "analysis.json")
        return {
            "schema_version": analysis.get("schema_version"),
            "source_file": analysis.get("source_file"),
            "counts": analysis.get("counts"),
            "bounding_box": analysis.get("bounding_box"),
            "axial_features": analysis.get("axial_features", [])[:100],
            "hole_axis_groups": analysis.get("hole_axis_groups", [])[:100],
            "hole_patterns": analysis.get("hole_patterns", [])[:50],
        }

    def inspect_comparison_evidence(self, _: dict[str, Any]) -> dict[str, Any]:
        return _read_json(self.directory, "comparison.json")

    def register_drawing_view_projection(self, arguments: dict[str, Any]) -> dict[str, Any]:
        graph_path = self.directory / "drawing_view_graph.json"
        if not graph_path.is_file():
            raise RuntimeError("Drawing view graph is not available")
        drawing_view_id = str(arguments.get("drawing_view_id") or "").strip()
        projection_view_id = str(arguments.get("projection_view_id") or "").strip().lower()
        evidence = str(arguments.get("evidence") or "").strip()
        try:
            confidence = float(arguments.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise ValueError("confidence must be a number") from exc
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", drawing_view_id):
            raise ValueError("drawing_view_id contains unsupported characters")
        if not re.fullmatch(r"(?:front|top|right|explore_[0-9]{2})", projection_view_id):
            raise ValueError("projection_view_id must be front, top, right, or explore_NN")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not evidence or len(evidence) > 300:
            raise ValueError("evidence must contain 1 to 300 characters")

        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        view = next(
            (item for item in graph.get("views") or [] if item.get("id") == drawing_view_id),
            None,
        )
        if view is None:
            raise ValueError("drawing_view_id does not exist in the view graph")
        frames = {
            "front": ([0.0, -1.0, 0.0], [1.0, 0.0, 0.0]),
            "top": ([0.0, 0.0, 1.0], [1.0, 0.0, 0.0]),
            "right": ([-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]),
        }
        if projection_view_id in frames:
            direction, x_direction = frames[projection_view_id]
            view["matched_projection_id"] = projection_view_id
            view.pop("matched_spatial_view_id", None)
        else:
            request_path = self.directory / "spatial" / projection_view_id / "request.json"
            if not request_path.is_file():
                raise ValueError("projection_view_id does not reference a rendered spatial view")
            rendered_views = _read_json(request_path.parent, "request.json").get("views") or []
            rendered = next((item for item in rendered_views if item.get("id") == projection_view_id), None)
            if rendered is None:
                raise ValueError("rendered spatial view metadata is invalid")
            direction = self._vector(rendered, "direction")
            x_direction = self._vector(rendered, "x_direction")
            view["matched_projection_id"] = None
            view["matched_spatial_view_id"] = projection_view_id
        view.update(
            {
                "observation_direction": direction,
                "x_direction": x_direction,
                "confidence": round(confidence, 4),
                "registration_evidence": evidence,
                "registration_source": "agent_visual_confirmation",
            }
        )
        if graph.get("source") != "multimodal_model":
            graph["source"] = "hybrid_agent_registered"
        graph["registration_count"] = int(graph.get("registration_count") or 0) + 1
        graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        comparison = apply_view_graph(self.directory, graph)
        return {
            "drawing_view": view,
            "comparison_summary": (comparison.get("manufacturing_specification") or {}).get("summary", {}),
            "view_intelligence": comparison.get("view_intelligence", {}),
        }

    @staticmethod
    def _vector(arguments: dict[str, Any], name: str) -> list[float]:
        value = arguments.get(name)
        if not isinstance(value, list) or len(value) != 3:
            raise ValueError(f"{name} must contain exactly three numbers")
        result = [float(item) for item in value]
        if not all(math.isfinite(item) for item in result):
            raise ValueError(f"{name} must contain finite numbers")
        if math.sqrt(sum(item * item for item in result)) <= 1.0e-8:
            raise ValueError(f"{name} must not be a zero vector")
        return result

    def render_spatial_view(self, arguments: dict[str, Any]) -> dict[str, Any]:
        direction = self._vector(arguments, "direction")
        x_direction = self._vector(arguments, "x_direction")
        direction_length = math.sqrt(sum(item * item for item in direction))
        x_length = math.sqrt(sum(item * item for item in x_direction))
        alignment = abs(
            sum(left * right for left, right in zip(direction, x_direction))
            / direction_length
            / x_length
        )
        if alignment >= 0.999:
            raise ValueError("x_direction must not be parallel to direction")
        normalized_direction = [item / direction_length for item in direction]
        if any(
            sum(left * right for left, right in zip(normalized_direction, previous)) > 0.985
            for previous in self._spatial_directions
        ):
            raise ValueError("direction duplicates an existing spatial exploration view")
        if self._spatial_view_count >= 3:
            raise ValueError("A run may generate at most three spatial exploration views")
        purpose = str(arguments.get("purpose", "")).strip()
        if not purpose or len(purpose) > 240:
            raise ValueError("purpose must contain 1 to 240 characters")
        requested_focus_feature_id = str(arguments.get("focus_feature_id", "")).strip()
        based_on_view_id = str(arguments.get("based_on_view_id", "")).strip()
        safe_identifier = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
        safe_focus_alias = re.compile(r"^[A-Za-z0-9_:#-]{1,80}$")
        if requested_focus_feature_id and not safe_focus_alias.fullmatch(
            requested_focus_feature_id
        ):
            raise ValueError("focus_feature_id contains unsupported characters")
        if based_on_view_id and not safe_identifier.fullmatch(based_on_view_id):
            raise ValueError("based_on_view_id contains unsupported characters")
        if based_on_view_id and not (
            self.directory / "spatial" / based_on_view_id / f"{based_on_view_id}.png"
        ).is_file():
            raise ValueError("based_on_view_id does not reference an existing exploration view")

        comparison = _read_json(self.directory, "comparison.json")
        analysis_payload = _read_json(self.directory, "analysis.json")
        feature_to_group: dict[str, str] = {}
        focus_aliases: dict[str, str] = {}
        for group in analysis_payload.get("hole_axis_groups", []):
            group_id = str(group.get("id", "")).strip()
            if not group_id or not safe_identifier.fullmatch(group_id):
                continue
            focus_aliases[group_id] = group_id
            for feature_id_value in group.get("feature_ids", []):
                feature_id = str(feature_id_value).strip()
                if feature_id:
                    feature_to_group[feature_id] = group_id
                    focus_aliases[feature_id] = group_id

        failed_group_id = ""
        for item in comparison.get("features", []):
            step_feature_id = str(
                item.get("step_feature_id") or item.get("cad_feature_id") or ""
            ).strip()
            base_feature_id = step_feature_id.split(":", 1)[0]
            group_id = feature_to_group.get(step_feature_id) or feature_to_group.get(
                base_feature_id, ""
            )
            if not group_id:
                continue
            for alias in (
                step_feature_id,
                base_feature_id,
                str(item.get("pdf_feature_id") or "").strip(),
                group_id,
            ):
                if alias:
                    focus_aliases[alias] = group_id
            if item.get("result") == "fail" and not failed_group_id:
                failed_group_id = group_id

        focus_feature_id = focus_aliases.get(requested_focus_feature_id, "")
        if focus_feature_id:
            focus_resolution = "resolved_from_evidence"
        elif self._spatial_view_count >= 1 and failed_group_id:
            focus_feature_id = failed_group_id
            focus_resolution = (
                "fallback_to_failed_feature"
                if requested_focus_feature_id
                else "automatic_failed_feature"
            )
        else:
            focus_resolution = "unresolved" if requested_focus_feature_id else "none"

        step_files = sorted(
            [*self.directory.glob("*.stp"), *self.directory.glob("*.step")]
        )
        if len(step_files) != 1:
            raise RuntimeError("Agent run must contain exactly one STEP file")
        analysis = self.directory / "analysis.json"
        if not analysis.is_file():
            raise RuntimeError("OCCT analysis is not available")

        self._spatial_view_count += 1
        self._spatial_directions.append(normalized_direction)
        view_id = f"explore_{self._spatial_view_count:02d}"
        stage = (
            "overview"
            if self._spatial_view_count == 1
            else "focused_verification"
            if focus_feature_id
            else "complementary"
        )
        output = self.directory / "spatial" / view_id
        output.mkdir(parents=True, exist_ok=False)
        config = output / "request.json"
        config.write_text(
            json.dumps(
                {
                    "views": [
                        {
                            "id": view_id,
                            "title": view_id,
                            "direction": direction,
                            "x_direction": x_direction,
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        projector = os.getenv("MEAS_PROJECTOR_BIN", "occt-projector")
        completed = subprocess.run(
            [projector, str(step_files[0]), str(output), str(analysis), str(config)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(os.getenv("MEAS_PROCESS_TIMEOUT_SECONDS", "600")),
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(detail[-2000:] or "OCCT custom projection failed")

        svg = output / f"{view_id}.svg"
        png = output / f"{view_id}.png"
        if svg.is_file() and focus_feature_id:
            svg_text = svg.read_text(encoding="utf-8")
            focus_style = f"""
  <style data-agent-focus="{focus_feature_id}">
    [data-hole-groups*="{focus_feature_id}"] .feature-target {{ fill: rgba(220,38,38,.10) !important; stroke: #dc2626 !important; stroke-width: 3px !important; }}
    [data-hole-groups*="{focus_feature_id}"] .leader {{ stroke: #dc2626 !important; stroke-width: 1.5px !important; }}
    [data-hole-groups*="{focus_feature_id}"] .leader-dot {{ fill: #dc2626 !important; }}
    [data-hole-groups*="{focus_feature_id}"] text {{ fill: #b91c1c !important; }}
  </style>
"""
            svg.write_text(svg_text.replace("</svg>", f"{focus_style}</svg>"), encoding="utf-8")
        if not svg.is_file() or not rasterize_svg(svg, png):
            raise RuntimeError("Custom projection was generated but PNG rasterization failed")
        manifest = _read_json(output, "views.json")
        relative_png = str(png.relative_to(self.directory)).replace("\\", "/")
        relative_svg = str(svg.relative_to(self.directory)).replace("\\", "/")
        run_id = self.directory.name
        public_artifact = {
            "id": view_id,
            "kind": "spatial_projection",
            "label": purpose,
            "sequence": self._spatial_view_count,
            "stage": stage,
            "focus_feature_id": focus_feature_id or None,
            "requested_focus_feature_id": requested_focus_feature_id or None,
            "focus_resolution": focus_resolution,
            "based_on_view_id": based_on_view_id or None,
            "direction": direction,
            "x_direction": x_direction,
            "image_url": f"/api/v1/agent-runs/{run_id}/artifacts/{relative_png}",
            "svg_url": f"/api/v1/agent-runs/{run_id}/artifacts/{relative_svg}",
        }
        return {
            "view_id": view_id,
            "purpose": purpose,
            "sequence": self._spatial_view_count,
            "stage": stage,
            "focus_feature_id": focus_feature_id or None,
            "requested_focus_feature_id": requested_focus_feature_id or None,
            "focus_resolution": focus_resolution,
            "based_on_view_id": based_on_view_id or None,
            "direction": direction,
            "x_direction": x_direction,
            "projection": (manifest.get("views") or [{}])[0],
            "artifacts": [public_artifact],
            "_model_images": [
                {"path": relative_png, "label": f"OCCT spatial exploration {view_id}: {purpose}"}
            ],
        }
