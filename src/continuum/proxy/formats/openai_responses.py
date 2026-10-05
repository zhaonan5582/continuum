# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""OpenAI Responses API 适配器（POST /v1/responses；codex wire_api="responses"、OpenAI 新 SDK）。

请求体形态：input 为 string 或 message 列表（content 可为 string 或
input_text/output_text 块列表）；system 等价物是 instructions 字段。
响应 usage 字段名为 input_tokens / output_tokens（与 Chat Completions 不同）。
"""

from __future__ import annotations

from typing import Any

from continuum.proxy.formats.base import LLMFormatAdapter


class OpenAIResponsesAdapter(LLMFormatAdapter):
    name = "openai-responses"

    def detect(self, path: str, headers: dict[str, str]) -> bool:
        return path.rstrip("/").endswith("/responses")

    def extract_conversation(self, body: dict[str, Any]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        items = body.get("input")
        if isinstance(items, str):
            if items.strip():
                out.append({"role": "user", "content": items})
            return out
        if not isinstance(items, list):
            return out
        for m in items:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                continue
            content = m.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and isinstance(b.get("text"), str)
                )
            else:
                text = ""
            if text:
                out.append({"role": m["role"], "content": text})
        return out

    def inject_system(self, body: dict[str, Any], assembly_text: str) -> dict[str, Any]:
        import copy
        body = copy.deepcopy(body)
        existing = body.get("instructions")
        if isinstance(existing, str) and existing:
            body["instructions"] = f"{existing}\n\n{assembly_text}"
        else:
            body["instructions"] = assembly_text
        return body

    def extract_usage(self, response_body: dict[str, Any]) -> dict[str, int]:
        usage = response_body.get("usage") or {}
        return {
            "base": usage.get("input_tokens", 0),
            "completion": usage.get("output_tokens", 0),
        }
