from __future__ import annotations

import math
from typing import Any

from service.comparison import cylinders_from_analysis

TYPE_WEIGHT = 0.4
DIMENSION_WEIGHT = 0.4
CONTEXT_WEIGHT = 0.2
CANDIDATE_THRESHOLD = 0.7
NEAR_TIE_RATIO = 0.9
CONTEXT_NEAR_TIE_RATIO = 0.99


def _has_verified_drawing_context(entity: dict[str, Any]) -> bool:
    """Return whether the 2D entity is bound to a real drawing view/target.

    A unique numeric hit is still only a candidate when extraction has not
    associated the callout with a view and its referenced geometry.
    """

    view_id = str(entity.get("view_id") or "")
    return entity.get("status") == "bound" and bool(view_id) and not view_id.endswith("UNASSIGNED")


def _point(value: Any, fallback: Any = None) -> list[float]:
    source = value if isinstance(value, (list, tuple)) and len(value) == 3 else fallback
    return [round(float(item), 6) for item in (source or [0, 0, 0])]


def _axis(value: Any) -> list[float]:
    if isinstance(value, str):
        return {"X": [1.0, 0.0, 0.0], "Y": [0.0, 1.0, 0.0], "Z": [0.0, 0.0, 1.0]}.get(value.upper(), [0.0, 0.0, 1.0])
    return _point(value, [0, 0, 1])


def _feature(feature_id: str, kind: str, value: float, center: Any, axis: Any, **extra: Any) -> dict[str, Any]:
    result = {
        "id": feature_id,
        "type": kind,
        "value": round(float(value), 6),
        "center": _point(center),
        "axis": _axis(axis),
        "source_ids": extra.pop("source_ids", []),
        "evidence": int(extra.pop("evidence", 1)),
    }
    for key in ("start", "end"):
        if extra.get(key) is not None:
            extra[key] = _point(extra[key])
    result.update(extra)
    return result


def _model_bounds(analysis: dict[str, Any]) -> tuple[list[float], list[float], list[float]]:
    box = ((analysis.get("measurements") or {}).get("bounding_box") or {})
    minimum = _point(box.get("min"))
    maximum = _point(box.get("max"))
    size = _point(box.get("size"), [maximum[i] - minimum[i] for i in range(3)])
    return minimum, maximum, size


def _projection_context_score(
    entity: dict[str, Any],
    feature: dict[str, Any],
    model_bounds: tuple[list[float], list[float], list[float]],
) -> tuple[float, str] | None:
    """Score a numeric candidate using its leader target and inferred view shape.

    This is deliberately a ranking signal, not a verified binding.  A region must
    contain several independently traced leaders before it can influence matching.
    """

    target = entity.get("leader_target_pdf") or []
    region = entity.get("view_region_pdf") or []
    if (
        entity.get("status") not in {"context_bound", "ai_view_bound"}
        or int(entity.get("view_region_size") or 0) < 3
        or len(target) != 2
        or len(region) != 4
    ):
        return None
    region_width = float(region[2]) - float(region[0])
    region_height = float(region[3]) - float(region[1])
    if region_width < 20 or region_height < 20:
        return None

    minimum, maximum, sizes = model_bounds
    if max(sizes or [0]) <= 0:
        return None
    center = _point(feature.get("center"))
    direction_hint = entity.get("view_direction_hint")
    x_hint = entity.get("view_x_direction_hint")
    if isinstance(direction_hint, list) and isinstance(x_hint, list):
        direction = _axis(direction_hint)
        x_direction = _axis(x_hint)
        dot_dx = sum(direction[i] * x_direction[i] for i in range(3))
        x_direction = [x_direction[i] - direction[i] * dot_dx for i in range(3)]
        x_length = math.sqrt(sum(value * value for value in x_direction))
        if x_length <= 1e-8:
            return None
        x_direction = [value / x_length for value in x_direction]
        y_direction = [
            direction[1] * x_direction[2] - direction[2] * x_direction[1],
            direction[2] * x_direction[0] - direction[0] * x_direction[2],
            direction[0] * x_direction[1] - direction[1] * x_direction[0],
        ]
        corners = [
            [x, y, z]
            for x in (minimum[0], maximum[0])
            for y in (minimum[1], maximum[1])
            for z in (minimum[2], maximum[2])
        ]
        projected_x = [sum(point[i] * x_direction[i] for i in range(3)) for point in corners]
        projected_y = [sum(point[i] * y_direction[i] for i in range(3)) for point in corners]
        center_x = sum(center[i] * x_direction[i] for i in range(3))
        center_y = sum(center[i] * y_direction[i] for i in range(3))
        model_x = (center_x - min(projected_x)) / max(max(projected_x) - min(projected_x), 1e-9)
        model_y = (center_y - min(projected_y)) / max(max(projected_y) - min(projected_y), 1e-9)
        view_name = str(entity.get("matched_projection_id") or entity.get("view_type") or "dynamic")
        alignment = abs(sum(_axis(feature.get("axis"))[i] * direction[i] for i in range(3)))
    else:
        frames = {
            "front": (0, 2, False),
            "top": (0, 1, False),
            "right": (1, 2, True),
        }
        region_aspect = region_width / region_height
        viable = [
            (abs(math.log(region_aspect / (sizes[x_axis] / sizes[y_axis]))), name, x_axis, y_axis, flip_x)
            for name, (x_axis, y_axis, flip_x) in frames.items()
            if sizes[x_axis] > 1e-9 and sizes[y_axis] > 1e-9
        ]
        if not viable:
            return None
        _, view_name, x_axis, y_axis, flip_x = min(viable)
        model_x = (center[x_axis] - minimum[x_axis]) / sizes[x_axis]
        model_y = (center[y_axis] - minimum[y_axis]) / sizes[y_axis]
        if flip_x:
            model_x = 1 - model_x
        normal_axis = ({0, 1, 2} - {x_axis, y_axis}).pop()
        alignment = abs(_axis(feature.get("axis"))[normal_axis])
    drawing_x = (float(target[0]) - float(region[0])) / region_width
    drawing_y = (float(target[1]) - float(region[1])) / region_height
    registration = entity.get("view_registration") or {}
    matrix = registration.get("matrix_2x3") or []
    if len(matrix) == 6:
        drawing_top_y = 1.0 - drawing_y
        drawing_x, drawing_y = (
            float(matrix[0]) * drawing_x + float(matrix[1]) * drawing_top_y + float(matrix[2]),
            float(matrix[3]) * drawing_x + float(matrix[4]) * drawing_top_y + float(matrix[5]),
        )
    distance = math.hypot(model_x - drawing_x, model_y - drawing_y) / math.sqrt(2)
    spatial = max(0.0, 1.0 - distance)

    # Features measured in a view normally lie in its image plane; cylindrical
    # diameters/radii are the exception and are strongest when viewed along axis.
    semantic = entity.get("semantic_type")
    if semantic in {"diameter", "radius"}:
        visibility = 1.0 if alignment >= 0.8 else 0.55
    else:
        visibility = 1.0 if alignment <= 0.8 else 0.6
    registration_confidence = float(registration.get("confidence") or 0)
    confidence = max(float(entity.get("context_confidence") or 0.5), registration_confidence)
    return max(0.0, min(1.0, spatial * visibility * confidence)), view_name


def cad_measurement_features(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize OCCT output into auditable linear, radius and diameter candidates."""
    minimum, maximum, sizes = _model_bounds(analysis)
    model_center = [(minimum[i] + maximum[i]) / 2 for i in range(3)]
    features: list[dict[str, Any]] = []

    for cylinder in cylinders_from_analysis(analysis):
        features.append(_feature(
            cylinder.id, "cylindrical_feature", cylinder.diameter,
            cylinder.center, cylinder.axis, diameter=round(cylinder.diameter, 6),
            internal=cylinder.internal, source_ids=cylinder.source_ids,
        ))

    for index, (name, value) in enumerate(zip("XYZ", sizes)):
        start, end = list(model_center), list(model_center)
        start[index], end[index] = minimum[index], maximum[index]
        features.append(_feature(
            f"BBOX-{name}", "bounding_box_dimension", value, model_center, name,
            start=start, end=end, source_ids=["measurements.bounding_box"],
        ))

    thickness = analysis.get("thickness_analysis") or {}
    dominant = float(thickness.get("dominant_thickness") or 0)
    pairs = thickness.get("dominant_pairs") or []
    if dominant > 0 and pairs:
        pair = pairs[0]
        start = _point(pair.get("first_centroid"), model_center)
        normal = _axis(pair.get("normal"))
        end = [start[i] + normal[i] * dominant for i in range(3)]
        features.append(_feature(
            "THICKNESS-DOMINANT", "sheet_thickness", dominant,
            [(start[i] + end[i]) / 2 for i in range(3)], normal,
            start=start, end=end, evidence=int(thickness.get("dominant_evidence") or len(pairs)),
            source_ids=[f"thickness-pair-{i + 1}" for i in range(len(pairs))],
        ))

    holes = {item.get("id"): item for item in analysis.get("hole_axis_groups") or []}
    datum_groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for datum in analysis.get("datum_dimensions") or []:
        value = float(datum.get("value") or 0)
        datum_groups.setdefault((str(datum.get("axis") or ""), round(value * 1000)), []).append(datum)
    for number, ((axis_name, _), group) in enumerate(datum_groups.items(), 1):
        value = float(group[0]["value"])
        axis = _axis(axis_name)
        end = _point((holes.get(group[0].get("feature_id")) or {}).get("center"), model_center)
        start = list(end)
        component = "XYZ".find(axis_name)
        if component >= 0:
            start[component] -= value
        features.append(_feature(
            f"DATUM-{number:03d}", "datum_dimension", value,
            [(start[i] + end[i]) / 2 for i in range(3)], axis,
            start=start, end=end, source_ids=[str(item.get("id")) for item in group], evidence=len(group),
        ))

    # Reuse the reliable faces from thickness detection as axis-aligned datum
    # stations. This exposes e.g. the 4.55 mm flange height without pretending an
    # arbitrary origin coordinate is a measurement.
    station_seen: set[tuple[int, int]] = set()
    for pair_index, pair in enumerate(pairs, 1):
        normal = _axis(pair.get("normal"))
        component = max(range(3), key=lambda i: abs(normal[i]))
        if abs(normal[component]) < 0.999:
            continue
        for side in ("first", "second"):
            offset = float(pair.get(f"{side}_offset") or 0)
            value = abs(offset - minimum[component] * normal[component])
            key = component, round(value * 1000)
            if value <= 0.001 or key in station_seen:
                continue
            station_seen.add(key)
            end = _point(pair.get(f"{side}_centroid"), model_center)
            start = list(end)
            start[component] -= normal[component] * value
            features.append(_feature(
                f"PLANE-STATION-{pair_index:03d}-{side.upper()}", "plane_datum_distance", value,
                [(start[i] + end[i]) / 2 for i in range(3)], normal,
                start=start, end=end, source_ids=[f"thickness-pair-{pair_index}"],
            ))

    for item in analysis.get("plane_distance_features") or []:
        features.append(_feature(
            str(item["id"]), "parallel_plane_distance", item["distance"],
            item.get("center") or model_center, item.get("normal") or "Z",
            start=item.get("start"), end=item.get("end"), source_ids=item.get("source_ids") or [],
            evidence=min(int(item.get("first_faces", 1)), int(item.get("second_faces", 1))),
        ))
    for item in analysis.get("linear_edge_features") or []:
        features.append(_feature(
            str(item["id"]), "linear_edge_length", item["length"],
            item.get("center") or model_center, item.get("direction") or "Z",
            start=item.get("start"), end=item.get("end"), source_ids=item.get("source_ids") or [],
        ))

    radius_analysis = analysis.get("radius_pair_analysis") or {}
    for index, group in enumerate(radius_analysis.get("bend_groups") or [], 1):
        radius = float(group.get("radius") or 0)
        if radius > 0:
            features.append(_feature(
                f"BEND-R{index:03d}", "bend_radius", radius,
                group.get("center") or model_center,
                group.get("axis_vector") or group.get("axis") or "Z",
                radius=radius, side=group.get("side"), evidence=int(group.get("faces") or 1),
                source_ids=[f"bend-group-{index}"],
            ))
    torus_features: list[dict[str, Any]] = []
    for index, patch in enumerate(radius_analysis.get("torus_patches") or [], 1):
        for radius_name, suffix in (("major_radius", "MAJOR"), ("minor_radius", "MINOR")):
            radius = float(patch.get(radius_name) or 0)
            if radius > 0:
                torus_features.append(_feature(
                    f"TORUS-{index:03d}-{suffix}", "torus_radius", radius,
                    patch.get("center") or model_center, patch.get("axis") or "Z",
                    radius=radius, radius_role=suffix.lower(),
                    source_ids=[str(patch.get("face_id"))],
                ))
    # One physical blend can be split into several B-Rep faces or duplicated by
    # touching assembly bodies.  Quantity callouts count spatial locations, not
    # raw faces, so merge torus evidence with the same radius, center and axis.
    grouped_tori: dict[tuple[Any, ...], dict[str, Any]] = {}
    for feature in torus_features:
        key = (
            round(float(feature["value"]), 4),
            *(round(float(value), 4) for value in feature["center"]),
            *(round(abs(float(value)), 4) for value in feature["axis"]),
            feature.get("radius_role"),
        )
        existing = grouped_tori.get(key)
        if existing is None:
            feature["merged_feature_ids"] = [feature["id"]]
            grouped_tori[key] = feature
            continue
        existing["source_ids"] = sorted(
            set(existing.get("source_ids") or []) | set(feature.get("source_ids") or [])
        )
        existing["merged_feature_ids"].append(feature["id"])
        existing["evidence"] = len(existing["source_ids"])
    features.extend(grouped_tori.values())
    return features


def _tolerance_epsilon(entity: dict[str, Any]) -> float:
    """Tolerance used to discover correspondence candidates, not to pass parts.

    Drawing conformance is still evaluated exclusively by `_within_tolerance`
    using an explicit drawing tolerance. A wider discovery window prevents small
    CAD nominal/actual differences from being incorrectly reported as unmapped.
    """

    tolerance = entity.get("tolerance") or {}
    explicit = [abs(float(value)) for value in (tolerance.get("lower"), tolerance.get("upper")) if value is not None]
    nominal = abs(float(entity.get("nominal") or 0))
    # STEP assemblies often contain formed/assembled geometry rather than exact
    # drawing nominals.  Use a wider discovery window so those values remain
    # reviewable candidates; pass/fail still uses only the explicit tolerance.
    return max(explicit or [0.2, nominal * 0.02])


def _type_score(entity: dict[str, Any], feature: dict[str, Any]) -> float:
    semantic, kind = entity.get("semantic_type"), feature["type"]
    if semantic == "diameter" and kind == "cylindrical_feature":
        return 1.0 if feature.get("internal") is True else 0.9 if feature.get("internal") is None else 0.0
    if semantic == "radius":
        if kind in {"bend_radius", "torus_radius", "circular_edge_radius"}:
            return 1.0
        return 0.8 if kind == "cylindrical_feature" and feature.get("internal") is not True else 0.0
    if semantic == "basic_dimension":
        return {
            "bounding_box_dimension": 1.0,
            "sheet_thickness": 0.98,
            "datum_dimension": 0.92,
            "plane_datum_distance": 0.9,
            "parallel_plane_distance": 0.84,
            "linear_edge_length": 0.76,
        }.get(kind, 0.0)
    if semantic == "linear_dimension" and kind in {
        "bounding_box_dimension", "sheet_thickness", "datum_dimension", "plane_datum_distance",
        "parallel_plane_distance", "linear_edge_length",
    }:
        return 1.0
    return 0.0


def _measured_value(entity: dict[str, Any], feature: dict[str, Any]) -> float:
    if entity.get("semantic_type") == "radius" and feature["type"] == "cylindrical_feature":
        return float(feature["diameter"]) / 2
    return float(feature["value"])


def _score_candidate(
    entity: dict[str, Any],
    feature: dict[str, Any],
    model_bounds: tuple[list[float], list[float], list[float]],
) -> dict[str, Any] | None:
    type_score, nominal = _type_score(entity, feature), entity.get("nominal")
    if type_score == 0 or nominal is None:
        return None
    measured = _measured_value(entity, feature)
    delta, epsilon = abs(float(nominal) - measured), _tolerance_epsilon(entity)
    if delta > epsilon:
        return None
    dimension_score = max(0.8, 1 - delta / max(epsilon, 1e-9) * 0.2)
    evidence_score = min(0.8, 0.5 + math.log2(max(int(feature.get("evidence") or 1), 1)) * 0.1)
    projection_context = _projection_context_score(entity, feature, model_bounds)
    context_score = (
        0.25 * evidence_score + 0.75 * projection_context[0]
        if projection_context is not None
        else evidence_score
    )
    heuristic = 0.1 if entity.get("diameter_symbol_present") else 0.0
    score = TYPE_WEIGHT * type_score + DIMENSION_WEIGHT * dimension_score + CONTEXT_WEIGHT * context_score + heuristic
    if score < CANDIDATE_THRESHOLD:
        return None
    return {
        "cad_feature_id": feature["id"], "score": round(score, 6),
        "feature_type": feature["type"],
        "score_components": {
            "type": type_score,
            "dimension": round(dimension_score, 6),
            "context": round(context_score, 6),
            "spatial": round(projection_context[0], 6) if projection_context else None,
            "projection_hint": projection_context[1] if projection_context else None,
            "registration_method": (entity.get("view_registration") or {}).get("method"),
            "registration_rmse": (entity.get("view_registration") or {}).get("rmse_normalized"),
            "heuristic": heuristic,
        },
        "measured_value": round(measured, 6), "dimension_delta": round(delta, 6),
    }


def _within_tolerance(entity: dict[str, Any], measured: float) -> bool | None:
    nominal, tolerance = entity.get("nominal"), entity.get("tolerance")
    if nominal is None or not tolerance:
        return None
    lower = float(nominal) + float(tolerance.get("lower") or 0)
    upper = float(nominal) + float(tolerance.get("upper") or 0)
    return lower - 1e-8 <= measured <= upper + 1e-8


def build_manufacturing_specification(measurement_plan: dict[str, Any], analysis: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    all_entities = list(measurement_plan.get("drawing_entities") or [])
    entities = [item for item in all_entities if item.get("comparison_eligible") is not False]
    recognized_only = [item for item in all_entities if item.get("comparison_eligible") is False]
    cad_features = cad_measurement_features(analysis)
    mappings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    measurements = measurement_plan.get("measurements") or []
    specialized_id = (measurements[0].get("source") or {}).get("drawing_entity_id") if measurements else None
    specialized = comparison.get("features") or []
    model_bounds = _model_bounds(analysis)

    for entity in entities:
        entity_id = entity["id"]
        if entity_id == specialized_id and specialized:
            ids = [item["step_feature_id"] for item in specialized]
            values = [float(item["actual_diameter"]) for item in specialized]
            mapping = {
                "drawing_entity_id": entity_id, "cad_feature_ids": ids, "status": "matched",
                "method": "deterministic_rigid_pattern_match", "confidence": 0.99,
                "verification_status": "verified_geometry",
                "score_components": {"type": 1.0, "dimension": 1.0, "context": 1.0, "pattern_registration": 1.0},
                "rationale": "孔数量、直径与二维阵列经过刚体不变配准后建立唯一对应。",
                "provenance": {"created_by": "system", "reviewed_by": None},
            }
            mappings.append(mapping)
            rows.append({"drawing_entity": entity, "mapping_status": "matched", "verification_status": "verified_geometry", "cad_feature_ids": ids, "candidate_count": len(ids), "measured_values": values, "result": comparison.get("result"), "method": mapping["method"], "confidence": 0.99})
            continue

        candidates = [
            candidate
            for feature in cad_features
            if (candidate := _score_candidate(entity, feature, model_bounds)) is not None
        ]
        candidates.sort(key=lambda item: (-item["score"], item["cad_feature_id"]))
        if candidates:
            # Exact and near-exact nominal evidence should not be drowned by a
            # large population of merely in-window edges. This is a candidate
            # ranking tier, not a conformance tolerance.
            best_delta = min(item["dimension_delta"] for item in candidates)
            delta_band = max(1e-6, _tolerance_epsilon(entity) * 0.12)
            candidates = [
                item for item in candidates
                if item["dimension_delta"] <= best_delta + delta_band
            ]
            if entity.get("semantic_type") == "basic_dimension":
                best_type = max(item["score_components"]["type"] for item in candidates)
                candidates = [
                    item for item in candidates
                    if item["score_components"]["type"] >= best_type - 1e-9
                ]
            candidates.sort(key=lambda item: (-item["score"], item["cad_feature_id"]))
        has_spatial_context = bool(
            candidates
            and candidates[0].get("score_components", {}).get("spatial") is not None
        )
        near_tie_ratio = CONTEXT_NEAR_TIE_RATIO if has_spatial_context else NEAR_TIE_RATIO
        drawing_quantity = int(entity.get("quantity") or 1)
        # nX on a linear/radius callout describes repeated occurrences of one
        # dimension. Only a diameter callout requires an n-member CAD feature
        # set for deterministic correspondence.
        required_candidate_count = drawing_quantity if entity.get("semantic_type") == "diameter" else 1
        near_ties = [
            item
            for item in candidates
            if item["score"] >= candidates[0]["score"] * near_tie_ratio
        ] if candidates else []
        # Diameter quantities describe a feature set, so keep at least that many
        # ranked candidates. Linear/radius nX labels describe repeated instances
        # of one dimension and therefore use one required correspondence.
        retained_count = max(len(near_ties), min(required_candidate_count, len(candidates)))
        retained = candidates[:retained_count]
        has_verified_context = _has_verified_drawing_context(entity)
        if retained and len(retained) == required_candidate_count:
            status, ids = "matched", [item["cad_feature_id"] for item in retained]
            values = [item["measured_value"] for item in retained]
            verification_status = (
                "verified_geometry"
                if has_verified_context
                else "provisional_spatial"
                if has_spatial_context
                else "provisional_unique"
            )
            if has_verified_context:
                checks = [_within_tolerance(entity, value) for value in values]
                result = "fail" if False in checks else "pass" if checks and all(value is True for value in checks) else "not_evaluated"
                confidence = min(0.95, retained[0]["score"])
                rationale = "类型、尺寸和二维几何上下文一致，且候选数量与图纸数量一致。"
            else:
                result = "not_evaluated"
                confidence = retained[0]["score"] * 0.75
                rationale = "类型和数值仅得到一个候选；尚未绑定二维视图和引线目标，需人工确认。"
        elif retained:
            status, ids = "ambiguous", [item["cad_feature_id"] for item in retained]
            verification_status = "needs_disambiguation"
            values, result = [item["measured_value"] for item in retained], "not_evaluated"
            uniqueness_penalty = 1 / math.sqrt(len(retained))
            context_penalty = 1.0 if has_verified_context else 0.75
            confidence = retained[0]["score"] * uniqueness_penalty * context_penalty
            rationale = (
                "候选数量与图纸数量不唯一；保留候选并等待人工消歧。"
                if has_verified_context
                else "二维标注尚未绑定视图和引线目标；数值唯一也不能确认几何对应关系。"
            )
        else:
            status, ids, values, result, confidence = "unmapped", [], [], "not_evaluated", 0.0
            verification_status = "unmapped"
            rationale = "没有找到同时满足几何类型与尺寸门限的三维候选。"
        mapping = {
            "drawing_entity_id": entity_id, "cad_feature_ids": ids, "status": status,
            "method": "weighted_deterministic_scoring", "confidence": round(confidence, 6),
            "verification_status": verification_status,
            "candidates": retained, "rationale": rationale,
            "provenance": {"created_by": "system", "reviewed_by": None},
        }
        mappings.append(mapping)
        rows.append({"drawing_entity": entity, "mapping_status": status, "verification_status": verification_status, "cad_feature_ids": ids, "candidate_count": len(retained), "measured_values": values, "result": result, "method": mapping["method"], "confidence": mapping["confidence"]})

    for entity in recognized_only:
        rows.append({
            "drawing_entity": entity,
            "mapping_status": "not_applicable",
            "verification_status": "recognized_only",
            "cad_feature_ids": [],
            "candidate_count": 0,
            "measured_values": [],
            "result": "not_evaluated",
            "method": "semantic_recognition_only",
            "confidence": round(float(entity.get("confidence") or 0), 6),
        })

    summary = {
        "drawing_entities": len(all_entities),
        "matched": sum(item["status"] == "matched" for item in mappings),
        "ambiguous": sum(item["status"] == "ambiguous" for item in mappings),
        "unmapped": sum(item["status"] == "unmapped" for item in mappings),
    }
    if recognized_only:
        summary["not_applicable"] = len(recognized_only)
    return {
        "schema_version": "1.1.0", "method": "deterministic_first_context_aware_correspondence",
        "entity_filtering": {
            "extracted_entities": len(all_entities),
            "eligible_entities": len(entities),
            "excluded_entities": len(all_entities) - len(entities),
        },
        "scoring": {
            "weights": {"type": TYPE_WEIGHT, "dimension": DIMENSION_WEIGHT, "context": CONTEXT_WEIGHT},
            "candidate_threshold": CANDIDATE_THRESHOLD,
            "near_tie_ratio": NEAR_TIE_RATIO,
            "context_near_tie_ratio": CONTEXT_NEAR_TIE_RATIO,
        },
        "drawing_entities": all_entities, "cad_features": cad_features, "mappings": mappings,
        "comparison_rows": rows,
        "unmapped_entity_ids": [item["drawing_entity_id"] for item in mappings if item["status"] == "unmapped"],
        "summary": summary,
    }
