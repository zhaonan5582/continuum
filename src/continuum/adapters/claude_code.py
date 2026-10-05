# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Claude Code 参考适配器（档位 B 首发宿主之一）。

三件套（全部生成器，用户复制到宿主配置即生效——机制强制）：
1. to_udf：解析 ~/.claude/projects/<工作区编码>/<sessionId>.jsonl
   （type=user/assistant 的 message 行，content 块列表 → text 拼接）
2. hooks 配置 JSON：SessionEnd → extract；UserPromptSubmit → 注入装配提醒；
   PreToolUse → memory_guard
3. CLAUDE.md 注入片段：告诉 agent 何时该 recall（档位 B 的提示词注入部分）
"""

from __future__ import annotations

import json
from pathlib import Path

from continuum.adapters.base import HookAdapter, SessionAdapter
from continuum.udf import UDFMessage, UDFMeta


class ClaudeCodeSessionAdapter(SessionAdapter):
    host = "claude-code"

    def to_udf(self, raw_lines: list[str]) -> list[UDFMessage]:
        out: list[UDFMessage] = []
        for line in raw_lines:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") != "message":
                continue
            role = d.get("role") if d.get("role") in ("user", "assistant") else "assistant"
            content = d.get("content")
            text = _blocks_to_text(content)
            if not text.strip():
                continue
            ts = _ts_of(d)
            out.append(UDFMessage(
                ts=ts, role=role, host=self.host,
                session_id=str(d.get("sessionId") or "unknown"),
                content=text, meta=UDFMeta(),
            ))
        return out


def _blocks_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and isinstance(b.get("text"), str)]
        return "\n".join(p for p in parts if p)
    return ""


def _ts_of(d: dict) -> str:
    """Claude Code 行时间戳：优先 ISO 字符串；毫秒 int 兜底（WorkBuddy 同款）。"""
    ts = d.get("timestamp")
    if isinstance(ts, str) and ts:
        return ts
    if isinstance(ts, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")
    return "1970-01-01T00:00:00.000Z"


class ClaudeCodeHookAdapter(HookAdapter):
    host = "claude-code"

    def iter_raw_lines(self, source) -> Iterator[str]:
        """source = jsonl 路径。"""
        with Path(source).open(encoding="utf-8") as f:
            for line in f:
                yield line

    # ---- 档位 B 生成器 ----

    @staticmethod
    def hooks_config_template(server_cmd: str = "continuum serve") -> dict:
        """生成 Claude Code settings 的 hooks 配置块（用户合并进 ~/.claude/settings.json）。
        三个钩子全部机制强制：SessionEnd 沉淀 / UserPromptSubmit 提醒 recall /
        PreToolUse 门禁。"""
        return {
            "hooks": {
                "SessionEnd": [{"hooks": [{"type": "command",
                                           "command": f"{server_cmd} --on session-end"}]}],
                "UserPromptSubmit": [{"hooks": [{"type": "command",
                                                 "command": f"{server_cmd} --on prompt"}]}],
                "PreToolUse": [{"hooks": [{"type": "command",
                                           "command": f"{server_cmd} --on guard"}]}],
            }
        }

    @staticmethod
    def claude_md_snippet() -> str:
        """CLAUDE.md 注入片段（装配包之外的常驻提示）。"""
        return """<!-- CONTINUUM BEGIN（由 Continuum 档位 B 维护，勿手改） -->
## 持久记忆（Continuum）

- 冷启动：本文件随装配包注入的记忆状态是**当前事实**，不要向用户重新追问其中内容。
- 需要更多历史：调用 `memory_recall`（可带 time_hint，如「上周」）。
- 未经 `memory_guard` 允许，不得删除记忆库内容。
- 本节由 Continuum 维护，手动修改会在下次装配时被覆盖。
<!-- CONTINUUM END -->
"""
