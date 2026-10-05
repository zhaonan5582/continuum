# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""MCP server 装配骨架（P0）——延迟导入 mcp 包，核心保持零第三方依赖。

设计（宪法 7：核心零宿主）：
- `continuum-core` 包本身不依赖 mcp；只有本模块在**被调用时**才导入 mcp；
- 未安装 mcp 时给出带安装指引的明确错误（诚实接口，不静默）；
- 七工具逐一带 name/description 注册为 MCP tools（P0 阶段 append 可用，
  其余六工具挂 ContinuumServer 的契约 stub——调用方会收到含期次的错误）。
"""

from __future__ import annotations

import functools
import threading

from continuum.server.tools import (
    AppendRequest,
    AuditQuery,
    CompactRange,
    ContinuumServer,
    ExtractScope,
    Operation,
)
from continuum.udf import UDFMessage

# MCP 2.x 在 anyio worker 线程调用同步工具；backend 的 SQLite 连接按单线程语义
# 管理（WAL 允许并发读，但写事务交错不安全）——七工具在此串行化。
# 本地单用户记忆库，串行吞吐远超需求。
_TOOL_LOCK = threading.Lock()
_FEED = None   # WorkbuddyFeed 实例由 build_mcp_server 注入（第 0 扳机接线）；None=未接线


def _sync(fn):
    """把同步工具函数包成进程内串行执行（跨线程安全）+ 顺带喂食扳机。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _TOOL_LOCK:
            if _FEED is not None:
                try:
                    _FEED.maybe_sweep()   # 内部自吞异常，绝不挡宿主调用
                except Exception:          # pragma: no cover - 双层防护
                    pass
            return fn(*args, **kwargs)
    return wrapper


class MCPNotInstalled(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "MCP 支持未安装。安装：pip install 'mcp>=1.0'（mcp 1.x 与 2.x 均兼容）。"
            " 核心包（continuum-core）本身零依赖，本模块仅在构建 MCP server 时需要 mcp。"
        )


def build_mcp_server(cs: ContinuumServer, feed=None):
    """把 ContinuumServer 的七工具装配为 MCP server。依赖 mcp>=1.0（延迟导入）。

    mcp 2.x 把 FastMCP 更名为 MCPServer（mcp.server.mcpserver），
    @tool() 装饰器与 run() 接口两版一致——双版本按可用性自动选择。
    feed：可选的增量喂食器（如 WorkbuddyFeed）——第 0 扳机接线，每次工具
    调用顺带触发；None=不接线（其他宿主/测试不受影响）。"""
    global _FEED
    _FEED = feed
    server_cls = None
    try:
        from mcp.server.fastmcp import FastMCP as server_cls  # mcp 1.x
    except ImportError:
        try:
            from mcp.server.mcpserver import MCPServer as server_cls  # mcp 2.x
        except ImportError as e:  # pragma: no cover - 环境相关
            raise MCPNotInstalled() from e

    mcp = server_cls(
        "continuum",
        instructions=(
            "Continuum：跨 agent 持久记忆与连续人格层。"
            "记忆管『知道』，hook 管『不能违反』。"
        ),
    )

    @mcp.tool()
    @_sync
    def memory_append(host_agent: str, external_session_id: str, messages: list[dict],
                      title: str | None = None, project_id: str | None = None) -> dict:
        """原文落库（UDF v1）。由适配器机制推送（每条用户消息后/每 5 轮增量），不等会话结束。"""
        # host/session_id 是会话级参数，在此填充进每条消息（tools 层一致性检查仍然兜底）
        msgs = []
        for m in messages:
            if isinstance(m, UDFMessage):
                msgs.append(m)
            else:
                msgs.append(_parse({**m, "host": host_agent,
                                    "session_id": external_session_id}))
        r = cs.memory_append(AppendRequest(
            host_agent=host_agent, external_session_id=external_session_id,
            messages=tuple(msgs), title=title, project_id=project_id,
        ))
        return {"session_id": r.session_id, "accepted": r.accepted,
                "skipped": r.skipped, "message_ids": list(r.message_ids)}

    @mcp.tool()
    @_sync
    def memory_extract(session_id: int | None = None, since_ts: str | None = None,
                       kind: str | None = None) -> dict:
        """触发沉淀（P1：手动/边界；P2：挂机自动）。低置信度进待确认队列，不冒充高置信度。"""
        r = cs.memory_extract(ExtractScope(session_id=session_id, since_ts=since_ts, kind=kind))
        return {"produced_active": r.produced_active, "produced_pending": r.produced_pending,
                "scanned_messages": r.scanned_messages}

    @mcp.tool()
    @_sync
    def memory_recall(query: str, time_hint: str | None = None, limit: int = 20) -> dict:
        """检索（P1 快速路径：结构化过滤主力；P2 增强：judge 停止判断+相对时间解析）。
        每条结果必带 evidence_level 与原文指针。"""
        r = cs.memory_recall(query, time_hint=time_hint, limit=limit)
        return {"items": [i.__dict__ for i in r.items], "rounds_used": r.rounds_used,
                "latency_ms": r.latency_ms, "stopped_by": r.stopped_by}

    @mcp.tool()
    @_sync
    def memory_audit(action: str | None = None, target_like: str | None = None,
                     since_ts: str | None = None, limit: int = 50) -> dict:
        """溯源查询——「这条记忆哪来的」的答案入口（宪法 2）。"""
        r = cs.memory_audit(AuditQuery(action=action, target_like=target_like,
                                       since_ts=since_ts, limit=limit))
        return {"entries": list(r.entries), "total_matched": r.total_matched}

    @mcp.tool()
    @_sync
    def memory_compact(session_id: int, from_seq: int, to_seq: int) -> dict:
        """按条判决压缩（P2）：逐条 full/truncate/drop + 理由 + 原文指针。产物是清单，不是摘要。"""
        r = cs.memory_compact(CompactRange(session_id=session_id, from_seq=from_seq, to_seq=to_seq))
        return {"session_id": r.session_id,
                "entries": [e.__dict__ for e in r.entries],
                "chars_before": r.chars_before, "chars_after": r.chars_after, "note": r.note}

    @mcp.tool()
    @_sync
    def memory_assemble(project_id: str | None = None) -> dict:
        """冷启动装配包（P2）：persona 最新版 + L0 + 当前项目 L1 + 最近现场，总预算 ≤8K token。"""
        r = cs.memory_assemble(project_id)
        return {"project_id": r.project_id, "persona_md": r.persona_md, "snapshot_md": r.snapshot_md,
                "recent_verbatim": list(r.recent_verbatim), "token_estimate": r.token_estimate,
                "warnings": list(r.warnings)}

    @mcp.tool()
    @_sync
    def memory_guard(kind: str, target: str, detail: dict | None = None) -> dict:
        """红线门禁判定（P3）：写/删/外发前查红线表 → allow/warn/block。记忆管知道，hook 管不能违反。"""
        r = cs.memory_guard(Operation(kind=kind, target=target, detail=detail or {}))
        return {"verdict": r.verdict, "matched_redlines": list(r.matched_redlines), "reason": r.reason}

    return mcp


def _parse(d: dict):
    from continuum.udf import parse_udf
    return parse_udf(d)
