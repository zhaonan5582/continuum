# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""codex 会话适配器（~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl）。

实测格式（2026-10-05）：
- 行结构 {ordinal, timestamp(ISO), type, payload}；
- 正文 = type=="response_item" 且 payload.type=="message"；
- payload.role: user/assistant/**developer（系统注入——跳过）**；
- payload.content = 块列表（input_text/output_text → text 拼接）。
"""

from __future__ import annotations

import json

from continuum.adapters.base import SessionAdapter
from continuum.udf import UDFMessage, UDFMeta


def _blocks_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and isinstance(b.get("text"), str)]
        return "\n".join(p for p in parts if p)
    return ""


class CodexSessionAdapter(SessionAdapter):
    host = "codex"

    def to_udf(self, raw_lines: list[str]) -> list[UDFMessage]:
        out: list[UDFMessage] = []
        for line in raw_lines:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "response_item":
                continue
            p = d.get("payload") or {}
            if p.get("type") != "message":
                continue
            role = p.get("role")
            if role not in ("user", "assistant"):
                continue                    # developer = 系统注入，跳过
            text = _blocks_to_text(p.get("content"))
            if not text.strip():
                continue
            out.append(UDFMessage(
                ts=d.get("timestamp") or "1970-01-01T00:00:00.000Z",
                role=role, host=self.host,
                session_id=str(d.get("id") or "unknown")[:64],
                content=text, meta=UDFMeta(),
            ))
        return out
