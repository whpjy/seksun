from __future__ import annotations

from typing import Any

from service.comparison import CylinderFeature, cylinders_from_analysis


TYPE_WEIGHT = 0.4
DIMENSION_WEIGHT = 0.4
CONTEXT_WEIGHT = 0.2
CANDIDATE_THRESHOLD = 0.3
NEAR_TIE_RATIO = 0.9


def _tolerance_epsilon(entity: dict[str, Any]) -> float:
    tolerance = entity.get("tolerance") or {}
    values = [
        abs(float(value))
        for value in (tolerance.get("lower"), tolerance.get("upper"))
        if value is not None
    ]
    return max(values or [0.1])


def _type_score(entity: dict[str, Any], feature: CylinderFeature) -> float:
    semantic_type = entity.get("semantic_type")
    if semantic_type == "diameter":
        return 1.0 if feature.internal is True else 0.9 if feature.internal is None else 0.0
    if semantic_type == "radius":
        return 1.0 if feature.internal is False else 0.9 if feature.internal is None else 0.0
    return 0.0


def _measured_value(entity: dict[str, Any], feature: CylinderFeature) -> float:
    return feature.diameter / 2.0 if entity.get("semantic_type") == "radius" else feature.diameter


def _score_candidate(entity: dict[str, Any], feature: CylinderFeature) -> dict[str, Any] | None:
    type_score = _type_score(entity, feature)
    if type_score == 0:
        return None
    nominal = entity.get("nominal")
    if nominal is None:
        return None
    measured = _measured_value(entity, feature)
    delta = abs(float(nominal) - measured)
    epsilon = _tolerance_epsilon(entity)
    dimension_score = 1.0 if delta <= epsilon else 0.7 if delta <= 2 * epsilon else 0.0
    context_score = 0.5  # neutral until view/leader association is available
    heuristic = 0.1 if entity.get("diameter_symbol_present") else 0.0
    score = (
        TYPE_WEIGHT * type_score
        + DIMENSION_WEIGHT * dimension_score
        + CONTEXT_WEIGHT * context_score
        + heuristic
    )
    if dimension_score == 0:
        score *= 0.3
    if score < CANDIDATE_THRESHOLD:
        return None
    return {
        "cad_feature_id": feature.id,
        "score": round(score, 6),
        "score_components": {
            "type": type_score,
            "dimension": dimension_score,
            "context": context_score,
            "heuristic": heuristic,
        },
        "measured_value": round(measured, 6),
        "dimension_delta": round(delta, 6),
    }


def _cad_feature_record(feature: CylinderFeature) -> dict[str, Any]:
    return {
        "id": feature.id,
        "type": "cylindrical_feature",
        "center": [round(value, 6) for value in feature.center],
        "axis": [round(value, 8) for value in feature.axis],
        "diameter": round(feature.diameter, 6),
        "internal": feature.internal,
        "source_ids": feature.source_ids,
    }


def _within_tolerance(entity: dict[str, Any], measured: float) -> bool | None:
    nominal = entity.get("nominal")
    tolerance = entity.get("tolerance")
    if nominal is None or not tolerance:
        return None
    lower = float(nominal) + float(tolerance.get("lower") or 0.0)
    upper = float(nominal) + float(tolerance.get("upper") or 0.0)
    return lower - 1e-8 <= measured <= upper + 1e-8


def build_manufacturing_specification(
    measurement_plan: dict[str, Any],
    analysis: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    """Build an auditable deterministic-first 2D/3D correspondence document."""

    entities = list(measurement_plan.get("drawing_entities") or [])
    cylinders = cylinders_from_analysis(analysis)
    cad_by_id = {feature.id: feature for feature in cylinders}
    mappings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    specialized_entity_id = None
    measurements = measurement_plan.get("measurements") or []
    if measurements:
        specialized_entity_id = (measurements[0].get("source") or {}).get("drawing_entity_id")
    specialized_features = comparison.get("features") or []

    for entity in entities:
        entity_id = entity["id"]
        if entity_id == specialized_entity_id and specialized_features:
            cad_feature_ids = [item["step_feature_id"] for item in specialized_features]
            measured_values = [float(item["actual_diameter"]) for item in specialized_features]
            mapping = {
                "drawing_entity_id": entity_id,
                "cad_feature_ids": cad_feature_ids,
                "status": "matched",
                "method": "deterministic_rigid_pattern_match",
                "confidence": 0.99,
                "score_components": {
                    "type": 1.0,
                    "dimension": 1.0,
                    "context": 1.0,
                    "pattern_registration": 1.0,
                },
                "rationale": "孔数量、直径与二维阵列经过旋转/镜像/平移不变配准后对应。",
                "provenance": {"created_by": "system", "reviewed_by": None},
            }
            mappings.append(mapping)
            rows.append(
                {
                    "drawing_entity": entity,
                    "mapping_status": "matched",
                    "cad_feature_ids": cad_feature_ids,
                    "candidate_count": len(cad_feature_ids),
                    "measured_values": measured_values,
                    "result": comparison.get("result"),
                    "method": mapping["method"],
                    "confidence": mapping["confidence"],
                }
            )
            continue

        candidates = [
            candidate
            for feature in cylinders
            if (candidate := _score_candidate(entity, feature)) is not None
        ]
        candidates.sort(key=lambda item: (-item["score"], item["cad_feature_id"]))
        retained = (
            [item for item in candidates if item["score"] >= candidates[0]["score"] * NEAR_TIE_RATIO]
            if candidates
            else []
        )
        requested_quantity = int(entity.get("quantity") or 1)
        if len(retained) == 1 and requested_quantity == 1:
            selected = retained[0]
            feature = cad_by_id[selected["cad_feature_id"]]
            measured = _measured_value(entity, feature)
            passed = _within_tolerance(entity, measured)
            status = "matched"
            result = "pass" if passed is True else "fail" if passed is False else "not_evaluated"
            cad_feature_ids = [feature.id]
            confidence = min(0.95, selected["score"])
            rationale = "类型兼容且尺寸候选唯一；空间上下文尚使用保守中性分。"
        elif retained:
            status = "ambiguous"
            result = "not_evaluated"
            cad_feature_ids = [item["cad_feature_id"] for item in retained]
            confidence = retained[0]["score"]
            rationale = "存在多个近似并列的几何候选，需要视图、引线或人工上下文消歧。"
        else:
            status = "unmapped"
            result = "not_evaluated"
            cad_feature_ids = []
            confidence = 0.0
            rationale = "当前最小实现没有找到满足类型与尺寸门控的三维特征。"

        mapping = {
            "drawing_entity_id": entity_id,
            "cad_feature_ids": cad_feature_ids,
            "status": status,
            "method": "weighted_deterministic_scoring",
            "confidence": round(confidence, 6),
            "candidates": retained,
            "rationale": rationale,
            "provenance": {"created_by": "system", "reviewed_by": None},
        }
        mappings.append(mapping)
        rows.append(
            {
                "drawing_entity": entity,
                "mapping_status": status,
                "cad_feature_ids": cad_feature_ids,
                "candidate_count": len(retained),
                "measured_values": [item["measured_value"] for item in retained],
                "result": result,
                "method": mapping["method"],
                "confidence": mapping["confidence"],
            }
        )

    summary = {
        "drawing_entities": len(entities),
        "matched": sum(item["status"] == "matched" for item in mappings),
        "ambiguous": sum(item["status"] == "ambiguous" for item in mappings),
        "unmapped": sum(item["status"] == "unmapped" for item in mappings),
    }
    return {
        "schema_version": "1.0.0",
        "method": "deterministic_first_context_aware_correspondence",
        "scoring": {
            "weights": {"type": TYPE_WEIGHT, "dimension": DIMENSION_WEIGHT, "context": CONTEXT_WEIGHT},
            "candidate_threshold": CANDIDATE_THRESHOLD,
            "near_tie_ratio": NEAR_TIE_RATIO,
        },
        "drawing_entities": entities,
        "cad_features": [_cad_feature_record(feature) for feature in cylinders],
        "mappings": mappings,
        "comparison_rows": rows,
        "unmapped_entity_ids": [item["drawing_entity_id"] for item in mappings if item["status"] == "unmapped"],
        "summary": summary,
    }
