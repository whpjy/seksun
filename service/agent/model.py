from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class ModelReply:
    content: str
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any]
    usage: dict[str, Any]


class QwenModelClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        self.base_url = os.getenv(
            "QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.mode = os.getenv("AGENT_MODEL_MODE", "auto").strip().lower()
        self.timeout = float(os.getenv("AGENT_MODEL_TIMEOUT_SECONDS", "120"))

    @property
    def uses_mock(self) -> bool:
        return self.mode == "mock" or (self.mode == "auto" and not self.api_key)

    def complete(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        round_index: int,
    ) -> ModelReply:
        if self.uses_mock:
            return self._mock_reply(messages, round_index)
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
        }
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        else:
            payload["response_format"] = {"type": "json_object"}
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if response.is_error:
            detail = response.text[-1000:]
            raise RuntimeError(f"Qwen request failed ({response.status_code}): {detail}")
        raw = response.json()
        message = raw["choices"][0]["message"]
        return ModelReply(
            content=message.get("content") or "",
            tool_calls=message.get("tool_calls") or [],
            raw=raw,
            usage=raw.get("usage") or {},
        )

    @staticmethod
    def _mock_reply(messages: list[dict[str, Any]], round_index: int) -> ModelReply:
        tools = [
            "inspect_drawing_view_graph",
            "inspect_drawing_requirements",
            "inspect_cad_features",
            "inspect_comparison_evidence",
            "render_spatial_view",
            "render_spatial_view",
        ]
        if round_index < len(tools):
            name = tools[round_index]
            arguments = {}
            if name == "render_spatial_view":
                arguments = (
                    {
                        "direction": [1, -1, 1],
                        "x_direction": [1, 1, 0],
                        "purpose": "生成轴测方向投影以复核孔组空间布局",
                    }
                    if round_index == 4
                    else {
                        "direction": [-1, -1, 0.45],
                        "x_direction": [1, -1, 0],
                        "purpose": "根据首个斜视图，从互补方向聚焦复核异常孔",
                        "focus_feature_id": "HFO06",
                        "based_on_view_id": "explore_01",
                    }
                )
            call = {
                "id": f"mock-call-{round_index + 1}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }
            return ModelReply("", [call], {"mock": True, "tool_calls": [call]}, {})
        comparison: dict[str, Any] = {}
        for message in reversed(messages):
            if message.get("role") == "tool":
                try:
                    candidate = json.loads(message.get("content", "{}"))
                except json.JSONDecodeError:
                    continue
                if "result" in candidate and "features" in candidate:
                    comparison = candidate
                    break
        result = comparison.get("result", "unknown")
        failed = comparison.get("summary", {}).get("failed", 0)
        content = (
            f"已完成图纸要求、CAD特征和确定性比较证据检查。"
            f"几何验证结果为 {result}，失败特征数为 {failed}；最终裁决以OCCT测量证据为准。"
        )
        return ModelReply(content, [], {"mock": True, "content": content}, {})
