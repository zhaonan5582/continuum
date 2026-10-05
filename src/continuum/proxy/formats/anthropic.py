# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Anthropic 格式适配器（Claude API 直连）。"""

from __future__ import annotations

from typing import Any

from continuum.proxy.formats.base import LLMFormatAdapter


class AnthropicAdapter(LLMFormatAdapter):
    name = "anthropic"

    def detect(self, path: str, headers: dict[str, str]) -> bool:
        if path.rstrip("/").endswith("/messages"):
            # 区分 OpenAI（也有 /messages 但路径不同）与 Anthropic
            return bool(headers.get("x-api-key") or headers.get("anthropic-version"))
        return False

    def extract_conversation(self, body: dict[str, Any]) -> list[dict[str, str]]:
        messages = body.get("messages") or []
        out = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                continue
            content = m.get("content")
            if isinstance(content, list):
                text = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and isinstance(b.get("text"), str)
                )
            elif isinstance(content, str):
                text = content
            else:
                text = ""
            if text:
                out.append({"role": m["role"], "content": text})
        return out

    def inject_system(self, body: dict[str, Any], assembly_text: str) -> dict[str, Any]:
        import copy
        body = copy.deepcopy(body)
        system = body.get("system")
        if system is None:
            body["system"] = assembly_text
        elif isinstance(system, str):
            body["system"] = f"{system}\n\n{assembly_text}"
        elif isinstance(system, list):
            # Anthropic system 可以是块列表
            body["system"] = system + [{"type": "text", "text": f"\n\n{assembly_text}"}]
        return body

    def extract_usage(self, response_body: dict[str, Any]) -> dict[str, int]:
        usage = response_body.get("usage") or {}
        return {
            "base": usage.get("input_tokens", 0),
            "completion": usage.get("output_tokens", 0),
        }
