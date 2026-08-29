from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


EPSILON = 1e-8


@dataclass
class CylinderFeature:
    id: str
    center: tuple[float, float, float]
    axis: tuple[float, float, float]
    diameter: float
    internal: bool | None = None
    source_ids: list[str] = field(default_factory=list)


def _numbers(value: str) -> tuple[float, ...]:
    return tuple(
        float(item.replace("D", "E"))
        for item in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[ED][-+]?\d+)?", value)
    )


def _normalize(vector: Iterable[float]) -> tuple[float, float, float]:
    x, y, z = vector
    length = math.sqrt(x * x + y * y + z * z)
    if length <= EPSILON:
        raise ValueError("零向量不能作为圆柱轴线")
    result = (x / length, y / length, z / length)
    for component in result:
        if abs(component) <= EPSILON:
            continue
        if component < 0:
            return tuple(-item for item in result)
        break
    return result


def _dot(left: Iterable[float], right: Iterable[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _sub(left: Iterable[float], right: Iterable[float]) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right))


def _closest_axis_point(
    point: tuple[float, float, float], axis: tuple[float, float, float]
) -> tuple[float, float, float]:
    station = _dot(point, axis)
    return tuple(point[index] - axis[index] * station for index in range(3))


def _same_axis_line(left: CylinderFeature, right: CylinderFeature, tolerance: float) -> bool:
    if abs(_dot(left.axis, right.axis)) < 1 - 1e-6:
        return False
    return math.dist(
        _closest_axis_point(left.center, left.axis),
        _closest_axis_point(right.center, right.axis),
    ) <= tolerance


def merge_cylinders(
    features: Iterable[CylinderFeature], tolerance: float = 1e-4
) -> list[CylinderFeature]:
    merged: list[CylinderFeature] = []
    for feature in features:
        match = next(
            (
                current
                for current in merged
                if abs(current.diameter - feature.diameter) <= tolerance
                and current.internal == feature.internal
                and _same_axis_line(current, feature, tolerance)
            ),
            None,
        )
        if match is None:
            merged.append(feature)
        else:
            match.source_ids.extend(feature.source_ids or [feature.id])
    return merged


def cylinders_from_analysis(analysis: dict) -> list[CylinderFeature]:
    features: list[CylinderFeature] = []
    for axial in analysis.get("axial_features", []):
        center = tuple(float(item) for item in axial["center"])
        axis = _normalize(axial["axis"])
        segments = axial.get("segments", [])
        if segments:
            for index, segment in enumerate(segments):
                features.append(
                    CylinderFeature(
                        id=f"{axial['id']}:{index + 1}",
                        center=center,
                        axis=axis,
                        diameter=float(segment["diameter"]),
                        internal=bool(segment.get("internal")),
                        source_ids=[str(segment.get("face_id", axial["id"]))],
                    )
                )
        else:
            for index, diameter in enumerate(axial.get("diameters", [])):
                features.append(
                    CylinderFeature(
                        id=f"{axial['id']}:{index + 1}",
                        center=center,
                        axis=axis,
                        diameter=float(diameter),
                        source_ids=[str(axial["id"])],
                    )
                )
    return merge_cylinders(features)


def cylinders_from_step_text(text: str) -> list[CylinderFeature]:
    """Read analytic cylinders from a Part 21 STEP file.

    This dependency-free reader is intended for comparison tests and diagnostics.
    Production processing should use the OCCT analyzer, which can also determine
    whether a cylindrical patch is internal or external.
    """

    entities = {
        match.group(1): match.group(2).strip()
        for match in re.finditer(r"(?ms)^\s*#(\d+)\s*=\s*(.*?);\s*$", text)
    }
    features: list[CylinderFeature] = []
    cylinder_pattern = re.compile(
        r"CYLINDRICAL_SURFACE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*([^\)]+)\)"
    )
    placement_pattern = re.compile(
        r"AXIS2_PLACEMENT_3D\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*#(\d+)"
    )
    for entity_id, value in entities.items():
        cylinder = cylinder_pattern.search(value)
        if not cylinder:
            continue
        placement = entities.get(cylinder.group(1), "")
        placement_match = placement_pattern.search(placement)
        if not placement_match:
            continue
        point_values = _numbers(entities.get(placement_match.group(1), ""))
        axis_values = _numbers(entities.get(placement_match.group(2), ""))
        if len(point_values) < 3 or len(axis_values) < 3:
            continue
        features.append(
            CylinderFeature(
                id=f"step-surface-{entity_id}",
                center=tuple(point_values[-3:]),
                axis=_normalize(axis_values[-3:]),
                diameter=float(cylinder.group(2).replace("D", "E")) * 2,
                source_ids=[f"#{entity_id}"],
            )
        )
    return merge_cylinders(features)


def cylinders_from_step(path: Path) -> list[CylinderFeature]:
    return cylinders_from_step_text(path.read_text(encoding="ascii", errors="replace"))


def _plane_basis(
    axis: tuple[float, float, float]
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    helper = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.8 else (0.0, 1.0, 0.0)
    projection = _dot(helper, axis)
    first = _normalize(tuple(helper[i] - axis[i] * projection for i in range(3)))
    second = (
        axis[1] * first[2] - axis[2] * first[1],
        axis[2] * first[0] - axis[0] * first[2],
        axis[0] * first[1] - axis[1] * first[0],
    )
    return first, _normalize(second)


def _project(
    point: tuple[float, float, float],
    basis: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> tuple[float, float]:
    return _dot(point, basis[0]), _dot(point, basis[1])


def _assign_nearest(
    predicted: list[tuple[float, float]],
    candidates: list[tuple[float, float]],
) -> tuple[list[int], list[float]] | None:
    pairs = sorted(
        (
            (math.dist(expected, actual), expected_index, actual_index)
            for expected_index, expected in enumerate(predicted)
            for actual_index, actual in enumerate(candidates)
        ),
        key=lambda item: item[0],
    )
    assigned = [-1] * len(predicted)
    residuals = [math.inf] * len(predicted)
    used: set[int] = set()
    for distance, expected_index, actual_index in pairs:
        if assigned[expected_index] >= 0 or actual_index in used:
            continue
        assigned[expected_index] = actual_index
        residuals[expected_index] = distance
        used.add(actual_index)
        if len(used) == len(predicted):
            break
    return None if any(index < 0 for index in assigned) else (assigned, residuals)


def match_feature_pattern(
    template: list[tuple[float, float]],
    features: list[CylinderFeature],
    nominal_diameter: float,
    position_tolerance: float = 0.25,
) -> dict:
    if len(template) < 2:
        raise ValueError("孔组至少需要两个 PDF 特征")

    diameter_window = max(1.0, nominal_diameter * 0.2)
    eligible = [
        feature
        for feature in features
        if abs(feature.diameter - nominal_diameter) <= diameter_window
        and feature.internal is not False
    ]
    if len(eligible) < len(template):
        raise ValueError("STEP 中没有足够的候选圆柱特征")

    best: dict | None = None
    for reference in eligible:
        axis_group = [
            feature
            for feature in eligible
            if abs(_dot(reference.axis, feature.axis)) >= 1 - 1e-6
        ]
        if len(axis_group) < len(template):
            continue
        basis = _plane_basis(reference.axis)
        model_points = [_project(feature.center, basis) for feature in axis_group]

        for left_index in range(len(template)):
            for right_index in range(left_index + 1, len(template)):
                template_vector = (
                    template[right_index][0] - template[left_index][0],
                    template[right_index][1] - template[left_index][1],
                )
                template_length = math.hypot(*template_vector)
                if template_length <= EPSILON:
                    continue
                template_x = (
                    template_vector[0] / template_length,
                    template_vector[1] / template_length,
                )
                template_y = (-template_x[1], template_x[0])

                for model_left in range(len(model_points)):
                    for model_right in range(len(model_points)):
                        if model_left == model_right:
                            continue
                        model_vector = (
                            model_points[model_right][0] - model_points[model_left][0],
                            model_points[model_right][1] - model_points[model_left][1],
                        )
                        model_length = math.hypot(*model_vector)
                        if model_length <= EPSILON:
                            continue
                        scale = model_length / template_length
                        if not 0.98 <= scale <= 1.02:
                            continue
                        model_x = (model_vector[0] / model_length, model_vector[1] / model_length)
                        perpendicular = (-model_x[1], model_x[0])

                        for reflection in (False, True):
                            model_y = (
                                -perpendicular[0] if reflection else perpendicular[0],
                                -perpendicular[1] if reflection else perpendicular[1],
                            )
                            predicted: list[tuple[float, float]] = []
                            for point in template:
                                delta = (
                                    point[0] - template[left_index][0],
                                    point[1] - template[left_index][1],
                                )
                                local_x = delta[0] * template_x[0] + delta[1] * template_x[1]
                                local_y = delta[0] * template_y[0] + delta[1] * template_y[1]
                                predicted.append(
                                    (
                                        model_points[model_left][0]
                                        + scale * (local_x * model_x[0] + local_y * model_y[0]),
                                        model_points[model_left][1]
                                        + scale * (local_x * model_x[1] + local_y * model_y[1]),
                                    )
                                )
                            assignment = _assign_nearest(predicted, model_points)
                            if assignment is None:
                                continue
                            indices, residuals = assignment
                            maximum_residual = max(residuals)
                            if maximum_residual > position_tolerance:
                                continue
                            rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
                            diameter_error = sum(
                                abs(axis_group[index].diameter - nominal_diameter)
                                for index in indices
                            ) / len(indices)
                            score = rms + abs(scale - 1) * 10 + diameter_error * 0.001
                            if best is None or score < best["score"]:
                                best = {
                                    "score": score,
                                    "scale": scale,
                                    "reflection": reflection,
                                    "rms_residual_mm": rms,
                                    "maximum_residual_mm": maximum_residual,
                                    "features": [axis_group[index] for index in indices],
                                    "residuals": residuals,
                                    "projected_centers": [model_points[index] for index in indices],
                                }
    if best is None:
        raise ValueError("无法在 STEP 中找到与 PDF 孔组一致的几何阵列")
    return best


def compare_c10(
    measurement_plan: dict,
    vector_extraction: dict,
    cylinders: list[CylinderFeature],
) -> dict:
    measurement = next(
        (item for item in measurement_plan.get("measurements", []) if item.get("id") == "C10"),
        None,
    )
    if measurement is None:
        raise ValueError("测量计划中不存在 C10")
    binding = next(
        (
            item
            for item in vector_extraction.get("annotation_bindings", [])
            if item.get("measurement_id") == "C10"
        ),
        None,
    )
    if binding is None:
        raise ValueError("矢量提取结果中不存在 C10 绑定")
    feature_group = next(
        (
            item
            for item in vector_extraction.get("vector_features", [])
            if item.get("id") == binding.get("target_feature_id")
        ),
        None,
    )
    if feature_group is None:
        raise ValueError("C10 指向的 PDF 矢量孔组不存在")

    template = [tuple(member["relative_center_mm"]) for member in feature_group["members"]]
    nominal = float(measurement["nominal"])
    tolerance = measurement["tolerance"]
    lower_limit = nominal + float(tolerance["lower"])
    upper_limit = nominal + float(tolerance["upper"])
    matched = match_feature_pattern(template, cylinders, nominal)

    results = []
    for member, feature, residual, projected in zip(
        feature_group["members"],
        matched["features"],
        matched["residuals"],
        matched["projected_centers"],
    ):
        passed = lower_limit - EPSILON <= feature.diameter <= upper_limit + EPSILON
        results.append(
            {
                "pdf_feature_id": member["id"],
                "pdf_relative_center_mm": member["relative_center_mm"],
                "step_feature_id": feature.id,
                "step_source_ids": feature.source_ids,
                "step_center": list(feature.center),
                "step_axis": list(feature.axis),
                "step_projected_center": list(projected),
                "actual_diameter": round(feature.diameter, 6),
                "deviation": round(feature.diameter - nominal, 6),
                "position_residual_mm": round(residual, 6),
                "result": "pass" if passed else "fail",
            }
        )

    passed_count = sum(item["result"] == "pass" for item in results)
    expected_quantity = int(measurement.get("quantity", len(template)))
    overall_passed = passed_count == expected_quantity and len(results) == expected_quantity
    return {
        "schema_version": "0.1.0",
        "measurement_id": "C10",
        "type": measurement["type"],
        "source": measurement["source"],
        "expected": {
            "quantity": expected_quantity,
            "nominal_diameter": nominal,
            "lower_limit": lower_limit,
            "upper_limit": upper_limit,
        },
        "registration": {
            "method": "rigid_2d_pattern_match",
            "scale": round(matched["scale"], 8),
            "reflection": matched["reflection"],
            "rms_residual_mm": round(matched["rms_residual_mm"], 6),
            "maximum_residual_mm": round(matched["maximum_residual_mm"], 6),
        },
        "summary": {
            "matched": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
        },
        "result": "pass" if overall_passed else "fail",
        "features": results,
    }


def compare_c10_from_analysis(
    measurement_plan: dict, vector_extraction: dict, analysis: dict
) -> dict:
    return compare_c10(
        measurement_plan,
        vector_extraction,
        cylinders_from_analysis(analysis),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare PDF characteristic C10 with STEP cylinders")
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--vector", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    plan = json.loads(arguments.plan.read_text(encoding="utf-8"))
    vector = json.loads(arguments.vector.read_text(encoding="utf-8"))
    result = compare_c10(plan, vector, cylinders_from_step(arguments.step))
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(content + "\n", encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
