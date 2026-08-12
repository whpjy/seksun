from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from service.agent.events import AgentRunStore
from service.agent.model import QwenModelClient
from service.agent.model_router import choose_review_model
from service.agent.tools import ComparisonToolRegistry
from service.agent.vision import artifact_image_content, multimodal_user_content


SYSTEM_PROMPT = """You are an auditable engineering drawing comparison agent.
Use the provided tools to inspect the extracted 2D requirements, exact CAD features,
and deterministic comparison evidence. Do not invent measurements. The CAD tool
results are authoritative for geometry and tolerance decisions. Explain the public
reason for each action concisely. Before concluding, inspect all three evidence tools.
When comparison.result is not_evaluated, operate in requirement-discovery mode:
summarize visible/extracted annotation candidates, state that they are not yet bound
to CAD features, do not issue pass/fail findings, and recommend which characteristic
an engineer should select for the next deterministic adapter.
Use render_spatial_view for active spatial exploration. For a failed comparison, first
request a useful non-standard overview, inspect its returned image, then request a
complementary second view focused on the failed CAD feature. Set based_on_view_id on
follow-up views and explain what ambiguity the new direction resolves. For a passing
comparison one useful non-standard view is sufficient. Never repeat an equivalent
direction and never request more than three spatial views.
After all tools are inspected, return only one valid JSON object with this shape:
{
  "headline": "short Chinese conclusion",
  "overview": "concise Chinese interpretation",
  "drawing_requirement": "what the drawing requires",
  "correspondence": "what deterministic evidence establishes",
  "findings": [{"pdf_feature_id":"...", "cad_feature_id":"...", "actual":"...", "result":"pass|fail"}],
  "uncertainties": ["only limitations supported by evidence"],
  "recommendation": "next engineering action"
}.
Do not use Markdown. Never claim uniqueness, confidence, or spatial observation unless
the tool evidence explicitly contains it. Explicitly report uncertainty when evidence
is incomplete."""


def _safe_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep observable output while excluding hidden reasoning and credentials."""
    blocked = {"reasoning_content", "authorization", "api_key", "access_token"}

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: scrub(item)
                for key, item in value.items()
                if key.lower() not in blocked
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(raw)


def _safe_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist prompts and image labels, not large embedded image byte strings."""
    safe = json.loads(json.dumps(messages))
    for message in safe:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if item.get("type") == "image_url":
                item["image_url"] = {"url": "<embedded PNG omitted from audit JSON>"}
    return safe


def _json_object(content: str) -> dict[str, Any] | None:
    candidate = content.strip()
    candidates = [candidate]
    fenced = re.findall(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", candidate, re.IGNORECASE)
    candidates.extend(fenced)
    first_brace = candidate.find("{")
    if first_brace >= 0:
        try:
            parsed, _ = json.JSONDecoder().raw_decode(candidate[first_brace:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    for item in candidates:
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _deterministic_review(directory: Path, model_content: str) -> dict[str, Any]:
    comparison = json.loads((directory / "comparison.json").read_text(encoding="utf-8"))
    if comparison.get("result") == "not_evaluated":
        discovery = comparison.get("discovery") or {}
        candidate_count = int(discovery.get("candidate_count", 0))
        return {
            "headline": "图纸需求已发现，等待选择检验特性",
            "overview": model_content or (
                f"系统提取到 {candidate_count} 个标注候选，但当前确定性适配器无法将其绑定到STEP特征。"
            ),
            "drawing_requirement": (
                f"已发现 {candidate_count} 个尺寸/工程标注候选；尚未形成经验证的测量计划。"
            ),
            "correspondence": "STEP精确几何与投影视图已生成，但本次没有建立确定性的2D/3D尺寸对应关系。",
            "findings": [],
            "uncertainties": [
                "候选标注仅来自PDF文本与位置发现，尚未完成视图归属、引线目标、公差语义和CAD特征绑定。"
            ],
            "recommendation": "请先选择要审核的尺寸类型或标注，随后为该类型建立确定性提取与几何映射适配器。",
            "source": "deterministic_discovery_fallback",
        }
    expected = comparison.get("expected") or {}
    registration = comparison.get("registration") or {}
    findings = [
        {
            "pdf_feature_id": item.get("pdf_feature_id"),
            "cad_feature_id": item.get("step_feature_id"),
            "actual": item.get("actual_diameter"),
            "result": item.get("result"),
        }
        for item in comparison.get("features", [])
    ]
    failed = [item for item in findings if item.get("result") == "fail"]
    quantity = expected.get("quantity")
    nominal = expected.get("nominal_diameter")
    lower = expected.get("lower_limit")
    upper = expected.get("upper_limit")
    return {
        "headline": "检查通过" if comparison.get("result") == "pass" else "发现尺寸差异",
        "overview": model_content or "AI未返回可解析摘要，以下内容由确定性证据生成。",
        "drawing_requirement": (
            f"图纸要求 {quantity} × Ø{nominal} mm，允许范围 {lower}～{upper} mm。"
        ),
        "correspondence": (
            f"确定性二维孔组配准已匹配 {len(findings)} 个特征，"
            f"最大位置残差 {registration.get('maximum_residual_mm')} mm。"
        ),
        "findings": findings,
        "uncertainties": [
            (
                "当前已执行AI选择方向的任意正交投影探索；"
                "尚未执行实体剖切、面隐藏/隔离或面ID回溯。"
                if any((directory / "spatial").glob("*/explore_*.png"))
                else "本次未生成任意方向探索视图；尚未执行实体剖切、面隐藏/隔离或面ID回溯。"
            )
        ],
        "recommendation": (
            "复核不合格特征：" + ", ".join(str(item.get("pdf_feature_id")) for item in failed)
            if failed
            else "保存本次几何证据并进入下一项检查。"
        ),
        "source": "deterministic_fallback",
    }


def _normalize_review(directory: Path, content: str) -> dict[str, Any]:
    review = _json_object(content)
    fallback = _deterministic_review(directory, content)
    if review is None:
        return fallback
    raw_uncertainties = review.get("uncertainties", [])
    uncertainties = (
        [str(item) for item in raw_uncertainties]
        if isinstance(raw_uncertainties, list)
        else [str(raw_uncertainties)]
        if raw_uncertainties
        else []
    )
    normalized = {
        "headline": str(review.get("headline") or fallback["headline"]),
        "overview": str(review.get("overview") or fallback["overview"]),
        "drawing_requirement": str(
            review.get("drawing_requirement") or fallback["drawing_requirement"]
        ),
        "correspondence": str(review.get("correspondence") or fallback["correspondence"]),
        # Findings stay deterministic: the model may explain them but may not rewrite measurements.
        "findings": fallback["findings"],
        "uncertainties": uncertainties or fallback["uncertainties"],
        "recommendation": str(review.get("recommendation") or fallback["recommendation"]),
        "source": "model_interpretation_with_deterministic_findings",
    }
    return normalized


def run_model_review(
    directory: Path,
    store: AgentRunStore,
    *,
    default_model: str | None = None,
) -> dict[str, Any]:
    registry = ComparisonToolRegistry(directory)
    client = QwenModelClient()
    selected_model, escalation_reasons = choose_review_model(directory)
    model = default_model or selected_model
    max_rounds = max(3, int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "12")))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": multimodal_user_content(
                directory,
                "审核当前PDF与STEP比较任务。以下图像是PDF第一页以及OCCT生成的固定正交投影。"
                "图像仅用于视觉观察；所有尺寸、对应和公差结论必须通过工具证据确认。"
                "依次检查图纸要求、CAD特征和确定性比较证据，最后返回指定JSON。",
            ),
        },
    ]
    interactions: list[dict[str, Any]] = []
    inspected: set[str] = set()
    spatial_views_completed = 0
    total_usage: dict[str, int] = {}

    store.emit(
        "model.started",
        "AI开始审核图纸、CAD特征和几何证据",
        data={
            "mode": "mock" if client.uses_mock else "live",
            "escalated": bool(escalation_reasons),
            "escalation_reasons": escalation_reasons,
        },
        model=model,
    )

    final_content = ""
    for round_index in range(max_rounds):
        reply = client.complete(
            model=model,
            messages=messages,
            tools=registry.definitions,
            round_index=round_index,
        )
        for key, value in reply.usage.items():
            if isinstance(value, int):
                total_usage[key] = total_usage.get(key, 0) + value
        interactions.append(
            {
                "round": round_index + 1,
                "model": model,
                "input": {"messages": _safe_messages(messages), "tools": registry.definitions},
                "output": _safe_response(reply.raw),
                "usage": reply.usage,
            }
        )
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": reply.content,
        }
        if reply.tool_calls:
            assistant_message["tool_calls"] = reply.tool_calls
        messages.append(assistant_message)

        if not reply.tool_calls:
            required_spatial_views = 2 if json.loads(
                (directory / "comparison.json").read_text(encoding="utf-8")
            ).get("result") == "fail" else 1
            if spatial_views_completed < required_spatial_views:
                store.emit(
                    "exploration.continued",
                    "空间证据尚不充分，继续生成互补观察方向",
                    data={
                        "completed_views": spatial_views_completed,
                        "required_views": required_spatial_views,
                    },
                    model=model,
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "当前空间探索数量不足。请调用render_spatial_view生成一个与已有方向"
                            "明显不同的互补视图；若比较不合格，请设置focus_feature_id为失败的"
                            "CAD特征ID，并用based_on_view_id关联上一探索图。观察返回图片后再总结。"
                        ),
                    }
                )
                continue
            final_content = reply.content.strip()
            break

        round_images: list[dict[str, Any]] = []
        spatial_requested_this_round = False
        for call in reply.tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name", ""))
            try:
                arguments = json.loads(function.get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be a JSON object")
                if name == "render_spatial_view":
                    if spatial_requested_this_round:
                        raise ValueError(
                            "Only one spatial view may be requested per model round; "
                            "inspect the returned image before choosing another direction"
                        )
                    spatial_requested_this_round = True
                store.emit(
                    "tool.requested",
                    f"AI请求执行 {name}",
                    data={"tool": name, "arguments": arguments},
                    model=model,
                )
                result = registry.call(name, arguments)
                model_images = result.pop("_model_images", [])
                if isinstance(model_images, list):
                    round_images.extend(model_images)
                inspected.add(name)
                if name == "render_spatial_view":
                    spatial_views_completed += 1
                store.emit(
                    "tool.completed",
                    f"工具 {name} 已返回可验证证据",
                    data={"tool": name, "result": result},
                    model=model,
                )
                content = json.dumps(result, ensure_ascii=False)
            except Exception as exc:
                store.emit(
                    "tool.failed",
                    f"工具 {name or 'unknown'} 执行失败",
                    data={"tool": name, "error": str(exc)},
                    model=model,
                )
                content = json.dumps({"error": str(exc)}, ensure_ascii=False)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": content,
                }
            )
        if round_images:
            messages.append(
                {
                    "role": "user",
                    "content": artifact_image_content(
                        directory,
                        round_images,
                        "这是你请求的OCCT任意方向空间投影。观察图像并结合工具返回的"
                        "方向、轮廓统计和精确几何证据继续审核，不要从像素估算尺寸。",
                    ),
                }
            )

    store.write_model_io(interactions)
    required = {
        "inspect_drawing_requirements",
        "inspect_cad_features",
        "inspect_comparison_evidence",
        "render_spatial_view",
    }
    missing = sorted(required - inspected)
    if missing:
        store.emit(
            "review.incomplete",
            "AI未检查全部必要证据，系统保留确定性结论并标记审核不完整",
            data={"missing_tools": missing},
            model=model,
        )
    if not final_content:
        final_content = "AI审核达到工具调用轮次上限；最终结果仅采用确定性几何证据。"
    structured_review = _normalize_review(directory, final_content)
    store.emit(
        "model.completed",
        "AI审核完成",
        data={
            "review": structured_review,
            "usage": total_usage,
            "missing_tools": missing,
        },
        model=model,
    )
    return {
        "model": model,
        "mode": "mock" if client.uses_mock else "live",
        "review_summary": structured_review["overview"],
        "structured_review": structured_review,
        "usage": total_usage,
        "inspected_tools": sorted(inspected),
        "missing_tools": missing,
        "spatial_views_completed": spatial_views_completed,
        "escalation_reasons": escalation_reasons,
    }
