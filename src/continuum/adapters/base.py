# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""适配器协议（docs/01 §3：适配层吸收一切宿主差异）。

两类适配器：
- SessionAdapter  会话采集：宿主会话流 → UDF（喂 memory_append）
- HookAdapter     生命周期：把宿主的钩子事件翻译成
                  extract（沉淀扳机）/ assemble（冷启动注入）/ guard（门禁拦截）

档位划分（docs/03）：A 档=纯 MCP（宿主自愿调用）；B 档=HookAdapter 生效（机制强制）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

from continuum.udf import UDFMessage


@dataclass(frozen=True)
class LifecycleEvent:
    """宿主生命周期事件（hook 适配器的标准输入）。"""

    kind: str            # session_start / session_end / idle / prompt_submit / pre_tool_use
    session_id: str
    project_id: str | None = None
    payload: dict | None = None   # pre_tool_use: {tool, target}; prompt_submit: {prompt}


class SessionAdapter(ABC):
    """会话采集适配器：实现者负责把宿主原始格式翻译成 UDF。"""

    host: str = "unknown"        # kebab-case 宿主标识

    @abstractmethod
    def to_udf(self, raw_lines: list[str]) -> list[UDFMessage]:
        """宿主原始行 → UDF v1 列表。解析失败的行应跳过并计数（不抛）。"""


class HookAdapter(ABC):
    """生命周期适配器：把宿主钩子翻译为 Continuum 动作。

    P1-B 的三个动作（机制扳机，全部不经 agent 自觉）：
    - on_session_end / on_idle → memory_extract（沉淀）
    - session_start → memory_assemble 注入（装配包）
    - pre_tool_use → memory_guard（门禁）
    """

    host: str = "unknown"

    def __init__(self, server):
        self.server = server

    @abstractmethod
    def iter_raw_lines(self, source) -> Iterator[str]:
        """从宿主会话源迭代原始行（文件/管道/日志）。"""

    def on_session_end(self, session_id: str, project_id: str | None = None) -> dict:
        """会话结束：强制沉淀（confirm=False，机制扳机产出 pending）。"""
        self._import_pending(session_id)
        r = self.server.memory_extract(
            __import__("continuum.server", fromlist=["ExtractScope"]).ExtractScope(
                session_id=None, since_ts=None))
        return {"extract": {"produced_pending": r.produced_pending, "scanned": r.scanned_messages}}

    def _import_pending(self, session_id: str) -> None:
        """钩子内的增量落库由 SessionAdapter 补齐（默认空实现）。"""

    def guard(self, kind: str, target: str, project_id: str | None = None) -> dict:
        v = self.server.memory_guard(
            __import__("continuum.server", fromlist=["Operation"]).Operation(
                kind=kind, target=target, detail={"project_id": project_id}))
        return {"verdict": v.verdict, "reason": v.reason}

    def assemble_for(self, project_id: str | None = None) -> dict:
        r = self.server.memory_assemble(project_id)
        return {"persona_md": r.persona_md, "snapshot_md": r.snapshot_md,
                "recent_verbatim": list(r.recent_verbatim), "token_estimate": r.token_estimate}
