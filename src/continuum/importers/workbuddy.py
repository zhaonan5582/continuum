# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""WorkBuddy 会话导入器（P1 验收数据通道）。

实测格式（2026-10-05，基于 ~/.workbuddy/projects/<workspace>/<sessionId>.jsonl）：
- 每行一个 JSON 对象，type 枚举：message / file-history-snapshot / ai-title /
  reasoning / function_call / function_call_result；
- **正文仅 type=="message"**（content 为块列表：input_text/output_text 等，取每块的
  text 拼接）；其余类型跳过（reasoning/function_call* 的导入留待需要时评估）；
- timestamp = 毫秒整数（UTC），全量存在；
- role = user / assistant。

安全：只读 jsonl；绝不触碰 workbuddy.db（主库属 WorkBuddy，非本产品管辖）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from continuum.storage.backend import StorageBackend
from continuum.udf import UDFMessage, UDFMeta


@dataclass(frozen=True)
class ImportReport:
    session_id: int
    external_session_id: str
    total_lines: int
    imported_messages: int
    skipped_non_message: int
    skipped_empty_content: int
    skipped_oversized: int = 0     # 超过 MAX_CONTENT_BYTES 的巨型行（跳过并计数，防整批失败）


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _content_to_text(content) -> str:
    """content 块列表 → 纯文本（拼接所有块的 text 字段）。字符串则原样。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


_INJECT_RE = re.compile(
    r"<system-reminder[\s\S]*?</system-reminder>"   # 宿主注入的上下文块（整体剥）
    r"|</?user_query>"                              # user_query 只剥标签——【内容是用户话语本体，必须保留】
)


def _strip_injections(text: str) -> str:
    """剥离宿主注入的元数据块（system-reminder/user_query 标记等）。
    这些是宿主运行时注入，不是用户话语——记忆层只存用户真实内容。"""
    return _INJECT_RE.sub("", text)


def import_workbuddy_session(
    jsonl_path: str | Path,
    backend: StorageBackend,
    *,
    host: str = "workbuddy",
    project_id: str | None = None,
    title: str | None = None,
) -> ImportReport:
    """把一个 WorkBuddy 会话 jsonl 导入 Continuum（幂等：重导自动去重）。"""
    path = Path(jsonl_path)
    if not path.exists():
        raise FileNotFoundError(f"会话文件不存在: {path}")
    external_id = path.stem            # <sessionId>.jsonl → sessionId

    messages: list[UDFMessage] = []
    total_lines = 0
    skipped_non_message = 0
    skipped_empty = 0
    skipped_oversized = 0
    max_bytes = 10_000_000  # 与 DESIGN_CONSTANTS["MAX_CONTENT_BYTES"] 一致（导入防御，探针24）

    with path.open(encoding="utf-8") as f:
        for line in f:
            total_lines += 1
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                skipped_non_message += 1
                continue
            if d.get("type") != "message":
                skipped_non_message += 1
                continue
            # 宿主内部消息过滤（字段级精准，非启发式猜测）：
            # isCompactInternal=压缩摘要行 / isMeta=continuation 与元消息——都不是用户话语
            provider = d.get("providerData") or {}
            if provider.get("isCompactInternal") or provider.get("isMeta"):
                skipped_non_message += 1
                continue
            ts_ms = d.get("timestamp")
            if not isinstance(ts_ms, int):
                skipped_non_message += 1
                continue
            text = _strip_injections(_content_to_text(d.get("content")))
            if not text.strip():
                skipped_empty += 1
                continue
            if len(text.encode("utf-8", errors="replace")) > max_bytes:
                skipped_oversized += 1   # 巨型行跳过不导入（防整批失败），计数可见
                continue
            role = d.get("role") if d.get("role") in ("user", "assistant") else "assistant"
            messages.append(UDFMessage(
                ts=_ms_to_iso(ts_ms),
                role=role,
                host=host,
                session_id=external_id,
                content=text,
                meta=UDFMeta(),
            ))

    sid = backend.ensure_session(host, external_id, title=title, project_id=project_id)
    ids, skipped_dup = backend.append_messages(sid, messages)
    backend.audit("core", "import.workbuddy", f"sessions/{sid}", {
        "file": path.name, "lines": total_lines,
        "imported": len(ids), "skipped_dup": skipped_dup,
        "skipped_non_message": skipped_non_message, "skipped_empty": skipped_empty,
        "skipped_oversized": skipped_oversized,
    })
    return ImportReport(
        session_id=sid, external_session_id=external_id, total_lines=total_lines,
        imported_messages=len(ids), skipped_non_message=skipped_non_message,
        skipped_empty_content=skipped_empty, skipped_oversized=skipped_oversized,
    )
