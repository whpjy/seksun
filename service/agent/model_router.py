from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def choose_review_model(directory: Path) -> tuple[str, list[str]]:
    default_model = os.getenv("AGENT_DEFAULT_MODEL", "qwen3.7-plus-2026-05-26")
    escalation_model = os.getenv("AGENT_ESCALATION_MODEL", "qwen3.8-max")
    threshold = float(os.getenv("AGENT_ESCALATION_RESIDUAL_MM", "0.15"))
    reasons: list[str] = []
    try:
        comparison: dict[str, Any] = json.loads(
            (directory / "comparison.json").read_text(encoding="utf-8")
        )
        registration = comparison.get("registration") or {}
        residual = float(registration.get("maximum_residual_mm", 0.0))
        expected = int((comparison.get("expected") or {}).get("quantity", 0))
        matched = int((comparison.get("summary") or {}).get("matched", 0))
        if residual > threshold:
            reasons.append(f"registration_residual>{threshold}mm")
        if expected and matched != expected:
            reasons.append("matched_quantity_differs_from_expected")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        reasons.append("comparison_evidence_incomplete")
    return (escalation_model if reasons else default_model), reasons
