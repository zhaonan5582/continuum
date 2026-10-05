# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Google Gemini 格式适配器。"""

from __future__ import annotations

from typing import Any

from continuum.proxy.formats.base import LLMFormatAdapter


class GeminiAdapter(LLMFormatAdapter):
    name = "gemini"

    def detect(self, path: str, headers: dict[str, str]) -> bool:
        return "/models/" in path and ("generateContent" in path or "streamGenerateContent" in path)

    def extract_conversation(self, body: dict[str, Any]) -> list[dict[str, str]]:
        contents = body.get("contents") or []
        out = []
        for c in contents:
            if not isinstance(c, dict):
                continue
            role = c.get("role", "")
            if role not in ("user", "model"):  # Gemini 用 model 代替 assistant
                continue
            parts = c.get("parts") or []
            text = "\n".join(
                p.get("text", "") for p in parts
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            )
            if text:
                out.append({"role": "user" if role == "user" else "assistant", "content": text})
        return out

    def inject_system(self, body: dict[str, Any], assembly_text: str) -> dict[str, Any]:
        import copy
        body = copy.deepcopy(body)
        si = body.get("systemInstruction") or body.get("system_instruction")
        if si is None:
            body["systemInstruction"] = {"parts": [{"text": assembly_text}]}
        elif isinstance(si, dict) and isinstance(si.get("parts"), list):
            si["parts"].append({"text": f"\n\n{assembly_text}"})
        elif isinstance(si, dict) and isinstance(si.get("text"), str):
            si["text"] = f"{si['text']}\n\n{assembly_text}"
        return body

    def extract_usage(self, response_body: dict[str, Any]) -> dict[str, int]:
        usage = response_body.get("usageMetadata") or {}
        return {
            "base": usage.get("promptTokenCount", 0),
            "completion": usage.get("candidatesTokenCount", 0),
        }
