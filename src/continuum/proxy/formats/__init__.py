# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""格式适配器注册表与检测器。"""

from __future__ import annotations

from typing import Any

from continuum.proxy.formats.anthropic import AnthropicAdapter
from continuum.proxy.formats.base import LLMFormatAdapter
from continuum.proxy.formats.gemini import GeminiAdapter
from continuum.proxy.formats.openai_compat import OpenAICompatAdapter
from continuum.proxy.formats.openai_responses import OpenAIResponsesAdapter

# 首发格式（按检测优先级排序）
_FORMATS: list[LLMFormatAdapter] = [
    AnthropicAdapter(),        # path+header 双重检测，先于 OpenAI 排
    GeminiAdapter(),           # path 特征明确
    OpenAIResponsesAdapter(),  # /v1/responses（codex wire_api="responses"）先于兜底
    OpenAICompatAdapter(),     # /chat/completions 兜底（OpenAI/DeepSeek/vLLM/Ollama/Groq/国产）
]


def detect_format(path: str, headers: dict[str, str]) -> LLMFormatAdapter | None:
    """按优先级逐个检测，返回第一个命中的格式适配器。无命中返回 None（passthrough）。"""
    for fmt in _FORMATS:
        if fmt.detect(path, headers):
            return fmt
    return None


def all_formats() -> list[LLMFormatAdapter]:
    return list(_FORMATS)


def register_format(fmt: LLMFormatAdapter) -> None:
    """注册新格式适配器（插件扩展点——新格式 = 写一个适配器 + 调此函数）。"""
    _FORMATS.insert(0, fmt)   # 新注册的优先检测
