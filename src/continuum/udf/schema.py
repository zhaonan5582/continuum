# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""UDF v1 —— 统一对话格式（契约冻结：UDF_VERSION=1，只增不改语义）。

这是适配器的唯一输出格式，也是核心唯一接受的输入格式（宪法 7：零宿主）。
冻结规则：
- 现有字段名与语义不可更改；
- 新增字段必须可选（向后兼容），并在 udf.schema.json 同步 + UDF_VERSION 递增；
- 任何修改必须走变更记录 + 机械交叉检索。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from continuum.version import UDF_VERSION, DESIGN_CONSTANTS

_MAX_CONTENT_BYTES = DESIGN_CONSTANTS["MAX_CONTENT_BYTES"]

_ROLES = ("user", "assistant", "tool")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


class UDFError(ValueError):
    """UDF 校验失败。"""


@dataclass(frozen=True)
class UDFMeta:
    """可选元数据块。tool=触发本次 tool 消息的工具名；tokens=该消息 token 数（可空，非负）。"""

    tool: str | None = None
    tokens: int | None = None

    def validate(self) -> list[str]:
        errs: list[str] = []
        if self.tokens is not None and (not isinstance(self.tokens, int) or isinstance(self.tokens, bool) or self.tokens < 0):
            errs.append(f"tokens 必须是非负整数: {self.tokens!r}")
        return errs

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


@dataclass(frozen=True)
class UDFMessage:
    """一条对话消息（冻结字段集 v1）。"""

    ts: str                 # ISO8601（含时区）
    role: str               # user | assistant | tool
    host: str               # 宿主标识（"workbuddy" / "claude-code" / ...）
    session_id: str         # 宿主侧会话外部 ID
    content: str            # 消息正文（原文，不做任何改写）
    meta: UDFMeta = field(default_factory=UDFMeta)

    def validate(self) -> list[str]:
        """返回全部校验错误（空列表 = 通过）。"""
        errs: list[str] = []
        if not _TS_RE.match(self.ts or ""):
            errs.append(f"ts 不是 ISO8601: {self.ts!r}")
        if self.role not in _ROLES:
            errs.append(f"role 必须是 {_ROLES} 之一: {self.role!r}")
        if not self.host or not self.host.strip():
            errs.append("host 不能为空")
        if len(self.host) > 128:
            errs.append(f"host 超长: {len(self.host)} > 128")
        if not self.session_id or not self.session_id.strip():
            errs.append("session_id 不能为空")
        if len(self.session_id) > 256:
            errs.append(f"session_id 超长: {len(self.session_id)} > 256")
        if self.content is None:
            errs.append("content 不能为 None（空字符串允许）")
        else:
            nbytes = len(self.content.encode("utf-8", errors="replace"))
            if nbytes > _MAX_CONTENT_BYTES:
                errs.append(f"content 超过单条上限 {_MAX_CONTENT_BYTES} 字节（当前 {nbytes}）——超大内容应由适配器分段")
        return errs

    def to_udf_dict(self) -> dict[str, Any]:
        """导出为 UDF v1 JSON 兼容 dict。"""
        return {
            "udf_version": UDF_VERSION,
            "ts": self.ts,
            "role": self.role,
            "host": self.host,
            "session_id": self.session_id,
            "content": self.content,
            "meta": self.meta.to_dict(),
        }


def now_iso() -> str:
    """当前 UTC 时间的 ISO8601（供测试与工具使用；生产由适配器携带宿主侧时间）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_udf(data: dict[str, Any]) -> UDFMessage:
    """从 UDF dict 解析并校验。失败抛 UDFError（聚合全部错误）。"""
    if not isinstance(data, dict):
        raise UDFError("UDF 必须是 JSON object")
    known = {"udf_version", "ts", "role", "host", "session_id", "content", "meta"}
    unknown = set(data) - known
    meta = data.get("meta")
    if meta is not None and not isinstance(meta, dict):
        raise UDFError(f"meta 必须是 object，得到 {type(meta).__name__}")
    msg = UDFMessage(
        ts=data.get("ts", ""),
        role=data.get("role", ""),
        host=data.get("host", ""),
        session_id=data.get("session_id", ""),
        content=data.get("content", ""),
        meta=UDFMeta(
            tool=meta.get("tool") if isinstance(meta, dict) else None,
            tokens=meta.get("tokens") if isinstance(meta, dict) else None,
        ),
    )
    errs = msg.validate()
    errs += (msg.meta.validate() if isinstance(msg.meta, UDFMeta) else ["meta 校验失败"])
    if unknown:
        errs.append(f"未知字段（UDF v1 冻结，禁止私有扩展）: {sorted(unknown)}")
    if "udf_version" in data and data["udf_version"] != UDF_VERSION:
        errs.append(f"udf_version 不匹配: 期望 {UDF_VERSION}, 得到 {data['udf_version']!r}")
    # 语义级时间校验（正则只查形状；25:99 这类值必须在这里拦下）
    if not errs or all("ts 不是" not in e for e in errs):
        try:
            datetime.fromisoformat(msg.ts.replace("Z", "+00:00"))
        except ValueError as e:
            errs.append(f"ts 不是有效时刻: {msg.ts!r} ({e})")
    if errs:
        raise UDFError("; ".join(errs))
    return msg


def loads_udf(raw: str) -> UDFMessage:
    """从 UDF JSON 字符串解析（UDF 是 JSONL 的行单位）。"""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise UDFError(f"非法 JSON: {e}") from e
    return parse_udf(data)
