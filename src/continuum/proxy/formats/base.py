# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""LLM API 格式适配器协议——每个 LLM API 格式 = 一个适配器实例。

与宿主 SessionAdapter 同构：新格式 = 写一个适配器插件，核心路由零改动。
passthrough 原则：格式不认识就原样转发，绝不猜测。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMFormatAdapter(ABC):
    """LLM API 格式适配器协议。"""

    name: str = "unknown"

    @abstractmethod
    def detect(self, path: str, headers: dict[str, str]) -> bool:
        """判断请求是否属于本格式。"""

    @abstractmethod
    def extract_conversation(self, body: dict[str, Any]) -> list[dict[str, str]]:
        """从请求 body 提取对话内容。
        返回 [{"role": "user"|"assistant", "content": str}, ...]。"""

    @abstractmethod
    def inject_system(self, body: dict[str, Any], assembly_text: str) -> dict[str, Any]:
        """在 system prompt 位置追加装配包文本，返回修改后 body（不修改原始 body）。"""

    @abstractmethod
    def extract_usage(self, response_body: dict[str, Any]) -> dict[str, int]:
        """从 LLM 响应提取 token 用量。返回 {"base": int, "completion": int}。"""
