# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""宿主 FeedSource 实现（docs/10 采集层）。

三类解析器：
1. **精确适配器**（实测格式）：WorkbuddySource / CodexSource；
2. **通用 JSONL 嗅探器**（GenericJsonlSource）——**普适性关键**：对每行做结构指纹
   匹配（type=message 单层 / {type,payload} 双层 / {role,content} OpenAI 风格 /
   嵌套 message 对象），覆盖多数 JSONL 宿主而无需逐个实测；
3. **安全**：所有 Source 的扫描都经 `is_never_read()` 过滤（凭据永不读）。

新增宿主 = 加一个 FeedSource（或在 agents/registry 指向 jsonl_generic）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from continuum.importers.feed_base import FeedSource  # noqa: F401  (契约)
from continuum.udf import UDFMessage, UDFMeta

_MAX_BYTES = 10_000_000


# ---------- 通用工具（各 Source 共用） ----------

def _blocks_to_text(content) -> str:
    """content（str | block 列表 | dict）→ 纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                t = b.get("text") or b.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts)
    if isinstance(content, dict):
        t = content.get("text")
        return t if isinstance(t, str) else ""
    return ""


def _safe_ms(ts) -> str | None:
    """毫秒整数 / ISO 字符串 → ISO8601。无法解析返回 None。"""
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ")
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(ts, str) and ts:
        try:
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return ts if ts.endswith("Z") or "+" in ts else ts + "Z"
        except ValueError:
            return None
    return None


def _sniff_message(d: dict) -> tuple[str, str] | None:
    """结构嗅探：从任意一行 JSON 里认出 (role, text)；认不出返回 None。

    依次尝试四种已知形态（覆盖 workbuddy/CC 系 / codex / OpenAI 风格 / 嵌套）：
    A. {type:"message", role, content}
    B. {type: <任意>, payload:{type:"message", role, content}}   （codex rollout）
    C. {role, content}
    D. {message:{role, content}} / {message:{content}}           （含 type=user/assistant 的 CC 形态）
    """
    def _pick(role, content, ts=None):
        r = role if role in ("user", "assistant") else None
        if r is None or role in ("system", "developer", "tool"):
            return None
        text = _blocks_to_text(content)
        return (r, text) if text.strip() else None

    # A
    if d.get("type") == "message" and "role" in d:
        got = _pick(d.get("role"), d.get("content"))
        if got:
            return got
    # B（codex rollout：response_item / event_msg 下的 payload）
    payload = d.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "message":
        got = _pick(payload.get("role"), payload.get("content"))
        if got:
            return got
    # C
    if "role" in d and "content" in d:
        got = _pick(d.get("role"), d.get("content"))
        if got:
            return got
    # D（CC 常见：type=user/assistant + message 对象）
    msg = d.get("message")
    if isinstance(msg, dict):
        role = msg.get("role") or d.get("type")
        got = _pick(role, msg.get("content"))
        if got:
            return got
    return None


def _ts_of(d: dict) -> str | None:
    """行级时间戳：顶层 timestamp/ts/create_time（毫秒或 ISO）。"""
    for k in ("timestamp", "ts", "create_time", "created_at", "time"):
        if k in d:
            v = _safe_ms(d[k])
            if v:
                return v
    payload = d.get("payload")
    if isinstance(payload, dict):
        for k in ("timestamp", "ts", "time"):
            if k in payload:
                v = _safe_ms(payload[k])
                if v:
                    return v
    msg = d.get("message")
    if isinstance(msg, dict) and "timestamp" in msg:
        v = _safe_ms(msg["timestamp"])
        if v:
            return v
    return None


# ---------- 通用 JSONL 嗅探 Source（覆盖多数 JSONL 宿主） ----------

@dataclass
class GenericJsonlSource:
    """结构嗅探式 Source：无需逐宿主实测，靠指纹认消息。

    已知覆盖（docs/10 附录的 JSONL 族）：Claude Code / Gemini CLI / Qwen Code /
    Copilot CLI / Grok CLI / Kiro / Kimi / Pi / CodeBuddy Code / Open Interpreter /
    Cursor(CLI) / Antigravity 等；未识别行一律跳过（宁可漏不可错）。
    """

    host: str
    root: Path
    patterns: tuple[str, ...] = ("**/*.jsonl",)
    format_kind: str = "jsonl"

    def roots(self) -> tuple[Path, ...]:
        return (self.root,)

    def external_id(self, path: Path) -> str:
        return path.stem

    def project_id(self, path: Path) -> str | None:
        # 常见布局：<root>/<project>/<session>.jsonl → 用父目录名（root 本身时返回 None）
        return path.parent.name if path.parent != self.root else None

    def max_content_bytes(self) -> int:
        return _MAX_BYTES

    def parse_increment(self, data: bytes, *, session_id: str,
                        project_id: str | None) -> list[UDFMessage]:
        out: list[UDFMessage] = []
        for raw in data.splitlines():
            if not raw.strip():
                continue
            try:
                d = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            got = _sniff_message(d)
            if not got:
                continue
            role, text = got
            if len(text.encode("utf-8", errors="replace")) > self.max_content_bytes():
                continue
            ts = _ts_of(d) or datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ")
            out.append(UDFMessage(ts=ts, role=role, host=self.host,
                                  session_id=session_id, content=text, meta=UDFMeta()))
        return out


# ---------- 精确适配器 ----------

@dataclass
class WorkbuddySource:
    """WorkBuddy（实测格式：type=message + providerData 过滤）。"""

    host: str = "workbuddy"
    root: Path = Path.home() / ".workbuddy" / "projects"
    patterns: tuple[str, ...] = ("**/*.jsonl",)
    format_kind: str = "jsonl"

    def roots(self) -> tuple[Path, ...]:
        return (self.root,)

    def external_id(self, path: Path) -> str:
        return path.stem

    def project_id(self, path: Path) -> str | None:
        return path.parent.name

    def max_content_bytes(self) -> int:
        return _MAX_BYTES

    def parse_increment(self, data: bytes, *, session_id: str,
                        project_id: str | None) -> list[UDFMessage]:
        from continuum.importers.workbuddy import (
            _content_to_text, _dialog_ts, _ms_to_iso, _strip_injections,
        )
        out: list[UDFMessage] = []
        for raw in data.splitlines():
            if not raw.strip():
                continue
            try:
                d = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            ts_ms = _dialog_ts(d)
            if ts_ms is None:
                continue
            text = _strip_injections(_content_to_text(d.get("content")))
            if not text.strip():
                continue
            if len(text.encode("utf-8", errors="replace")) > self.max_content_bytes():
                continue
            role = d.get("role") if d.get("role") in ("user", "assistant") else "assistant"
            out.append(UDFMessage(ts=_ms_to_iso(ts_ms), role=role, host=self.host,
                                  session_id=session_id, content=text, meta=UDFMeta()))
        return out


@dataclass
class CodexSource:
    """codex CLI（实测 rollout 格式：{type, payload} 双层，过滤 developer/world_state）。"""

    host: str = "codex"
    roots_: tuple[Path, ...] = ()

    patterns: tuple[str, ...] = ("**/*.jsonl",)
    format_kind: str = "jsonl"

    def __post_init__(self) -> None:
        if not self.roots_:
            h = Path.home() / ".codex"
            self.roots_ = (h / "sessions", h / "archived_sessions")

    def roots(self) -> tuple[Path, ...]:
        return self.roots_

    def external_id(self, path: Path) -> str:
        # rollout-<时间>-<uuid>.jsonl → uuid 部分；退化用 stem
        stem = path.stem
        return stem.split("-", 6)[-1] if stem.startswith("rollout-") else stem

    def project_id(self, path: Path) -> str | None:
        return path.parent.name if path.parent.name not in ("sessions", "archived_sessions") else None

    def max_content_bytes(self) -> int:
        return _MAX_BYTES

    def parse_increment(self, data: bytes, *, session_id: str,
                        project_id: str | None) -> list[UDFMessage]:
        out: list[UDFMessage] = []
        for raw in data.splitlines():
            if not raw.strip():
                continue
            try:
                d = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if d.get("type") != "response_item":
                continue
            payload = d.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "message":
                continue
            role = payload.get("role")
            if role not in ("user", "assistant"):     # 过滤 developer（宿主注入）
                continue
            text = _blocks_to_text(payload.get("content"))
            if not text.strip():
                continue
            if len(text.encode("utf-8", errors="replace")) > self.max_content_bytes():
                continue
            ts = _ts_of(d) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            out.append(UDFMessage(ts=ts, role=role, host=self.host,
                                  session_id=session_id, content=text, meta=UDFMeta()))
        return out


# ---------- 装配 ----------

def build_sources(home: Path | None = None) -> list[FeedSource]:
    """按注册表探测结果装配可用 Source（只纳入目录存在的宿主）。

    - WorkBuddy / codex：精确适配器（实测格式）
    - 其余 JSONL 族宿主：通用嗅探器（目录存在即纳入）
    """
    from continuum.agents import build_registry

    h = home if home is not None else Path.home()
    out: list[FeedSource] = []

    def _guarded(src: FeedSource) -> FeedSource | None:
        """安全红线：若扫描根整体落在'永不读'范围则丢弃（防御性）。"""
        from continuum.agents import is_never_read
        if any(is_never_read(r) for r in src.roots()):
            return None
        return src

    for prof in build_registry(home):
        if prof.key == "workbuddy":
            root = h / ".workbuddy" / "projects"
            if root.is_dir():
                s = _guarded(WorkbuddySource(root=root))
                if s:
                    out.append(s)
        elif prof.key == "codex":
            if any(r.is_dir() for r in (h / ".codex" / "sessions",
                                        h / ".codex" / "archived_sessions")):
                s = _guarded(CodexSource())
                if s:
                    out.append(s)
        elif prof.format_kind == "jsonl":
            # 其余 JSONL 族宿主（含 parser_ready 的 CC：格式由嗅探识别）→ 通用嗅探器
            for root in prof.session_roots:
                if root.is_dir():
                    s = _guarded(GenericJsonlSource(host=prof.key, root=root))
                    if s:
                        out.append(s)
    return out
