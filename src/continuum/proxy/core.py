# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""代理核心：格式检测 → 对话提取 → 注入 → 转发 → token 计量。"""
from __future__ import annotations

import json
import time
from typing import Any

from continuum.proxy.formats import detect_format

_INJECT_HEADER = "X-Continuum-Injected-Tokens"
_BASE_HEADER = "X-Continuum-Base-Tokens"
_TOTAL_HEADER = "X-Continuum-Total-Tokens"


def process_request(
    path: str,
    headers: dict[str, str],
    body: dict[str, Any],
    assembly_text: str | None,
    judge=None,
) -> tuple[str | None, dict[str, Any] | None, dict[str, str]]:
    """处理一个 LLM API 请求。

    返回 (match_expr_or_none, modified_body_or_none, response_headers)。
    - 格式命中 + 有装配包 → 返回修改后 body（注入了装配包）
    - 格式命中 + 无装配包 → 原样 body
    - 格式不命中 → (None, None, {}) = passthrough
    - 红线 block → (None, None, {"X-Continuum-Blocked": reason})
    """
    fmt = detect_format(path, headers)
    if fmt is None:
        return None, None, {}   # passthrough

    resp_headers: dict[str, str] = {}

    # 红线检查（代理层直接拒绝，不转发给 LLM——不花 token）
    if assembly_text is not None:
        conversation = fmt.extract_conversation(body)
        last_user = next(
            (m["content"] for m in reversed(conversation) if m["role"] == "user"), None
        )
        if last_user and judge is not None:
            v = judge.judge_message_retention("hook", last_user, has_downstream_memory=False)
            if v[0] == "block":
                resp_headers[_INJECT_HEADER] = "0"
                resp_headers[_TOTAL_HEADER] = "0"
                resp_headers["X-Continuum-Blocked"] = v[1][:200]
                return None, None, resp_headers   # 红线拒绝信号

    # 注入装配包
    if assembly_text:
        body = fmt.inject_system(body, assembly_text)
        resp_headers[_INJECT_HEADER] = str(
            len(assembly_text.encode("utf-8")) // 4   # 粗估 token
        )

    return fmt.name, body, resp_headers


def process_response(
    fmt_name: str | None,
    response_body: dict[str, Any],
) -> dict[str, str]:
    """从 LLM 响应提取 token 用量并标注。"""
    if fmt_name is None:
        return {}
    for fmt in _get_all_formats():
        if fmt.name == fmt_name:
            usage = fmt.extract_usage(response_body)
            return {
                _BASE_HEADER: str(usage.get("base", 0)),
                _TOTAL_HEADER: str(usage.get("base", 0) + usage.get("completion", 0)),
            }
    return {}


def _get_all_formats():
    from continuum.proxy.formats import all_formats
    return all_formats()
