# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""OpenAI 兼容格式适配器（覆盖面最广：OpenAI / DeepSeek / vLLM / Ollama / Groq / 国产）。"""

from __future__ import annotations

from typing import Any

from continuum.proxy.formats.base import LLMFormatAdapter


class OpenAICompatAdapter(LLMFormatAdapter):
    name = "openai-compat"

    def detect(self, path: str, headers: dict[str, str]) -> bool:
        return path.rstrip("/").endswith("/chat/completions")

    def extract_conversation(self, body: dict[str, Any]) -> list[dict[str, str]]:
        messages = body.get("messages") or []
        return [
            {"role": m.get("role", "unknown"), "content": m.get("content", "")}
            for m in messages
            if isinstance(m, dict) and m.get("role") in ("user", "assistant")
            and isinstance(m.get("content"), str)
        ]

    def inject_system(self, body: dict[str, Any], assembly_text: str) -> dict[str, Any]:
        import copy
        body = copy.deepcopy(body)
        messages = body.get("messages") or []
        # 已有 system 消息 → 追加；否则在头部插入
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                existing = m.get("content", "")
                m["content"] = f"{existing}\n\n{assembly_text}" if existing else assembly_text
                return body
        messages.insert(0, {"role": "system", "content": assembly_text})
        body["messages"] = messages
        return body

    def extract_usage(self, response_body: dict[str, Any]) -> dict[str, int]:
        usage = response_body.get("usage") or {}
        return {
            "base": usage.get("prompt_tokens", 0),
            "completion": usage.get("completion_tokens", 0),
        }
