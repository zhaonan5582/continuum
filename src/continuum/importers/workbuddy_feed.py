# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""WorkBuddy 会话增量喂食器（第 0 扳机的 WorkBuddy 接线，2026-10-06）。

设计要点（docs/01 §5.1 第 0 扳机 + 宪法 10 机制扳机）：
- 扳机体 = 宿主常驻拉起的 MCP serve 进程：每次工具调用顺带跑 should_sweep
  （每 N 次调用 / 每 T 小时，取先到），达标才真扫目录——零新增进程、
  无监听循环、无后台线程（符合"禁止常驻监听"约束）。
- 扫描对象 = ~/.workbuddy/projects/<workspace>/<sessionId>.jsonl 的**增量字节**
  （字节 offset，残行留到下一轮；append 幂等兜底，重扫不脏库）。
- 首见文件判据：文件创建时间早于 feed 初始化时刻（initialized_at，持久化）→
  老会话只记 offset 不回灌——存量导入走显式 import-workbuddy（用户主权：
  哪些历史入库由用户决定）；晚于初始化时刻 → 新会话，从 0 全扫。
- offset 持久化 ~/.continuum/feed_state.json：serve 重启不丢、重启间隙不漏。
- 喂食失败绝不挡住宿主工具调用：全程吞异常（双层防护，调用侧再兜一层）。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from continuum.importers.workbuddy import (
    _content_to_text,
    _dialog_ts,
    _ms_to_iso,
    _strip_injections,
)
from continuum.storage.backend import StorageBackend
from continuum.udf import UDFMessage, UDFMeta

DEFAULT_PROJECTS_DIR = Path.home() / ".workbuddy" / "projects"
DEFAULT_STATE_PATH = Path.home() / ".continuum" / "feed_state.json"
HOST = "workbuddy"
_MAX_CONTENT_BYTES = 10_000_000  # 与导入器同款防御（探针24）


@dataclass
class SweepResult:
    triggered: bool
    reason: str = "none"          # rounds / hours / none / error
    files_scanned: int = 0
    new_messages: int = 0
    skipped_dedup: int = 0
    old_files_marked: int = 0


@dataclass
class WorkbuddyFeed:
    backend: StorageBackend
    projects_dir: Path = DEFAULT_PROJECTS_DIR
    state_path: Path = DEFAULT_STATE_PATH
    every_n_calls: int = 20       # 与 DESIGN_CONSTANTS["SWEEP_EVERY_N_ROUNDS"] 语义一致
    every_hours: float = 4.0      # 与 DESIGN_CONSTANTS["SWEEP_EVERY_HOURS"] 一致
    _offsets: dict[str, int] = field(default_factory=dict)
    _calls_since_sweep: int = 0
    _last_sweep_ts: float = field(default_factory=time.time)
    _initialized_at: float = 0.0

    def __post_init__(self):
        self._load_state()          # 只在构造时读一次；每轮 sweep 重读既浪费又会覆盖基准线

    # ---- 生命周期 ----

    @classmethod
    def build_default(cls, backend: StorageBackend) -> "WorkbuddyFeed | None":
        """projects 目录存在才返回实例（否则 None=不接线，其他宿主不受影响）。"""
        if not DEFAULT_PROJECTS_DIR.is_dir():
            return None
        return cls(backend=backend)

    def _load_state(self) -> None:
        """读 offset 表 + initialized_at。

        initialized_at 只在状态文件里有有效值时才采纳（部署基准线一经确立不变）；
        文件不存在/损坏时保留当前值——首次部署基准线=构造时刻，测试可预设。"""
        self._offsets = {}
        try:
            if self.state_path.exists():
                d = json.loads(self.state_path.read_text(encoding="utf-8"))
                self._offsets = {k: int(v) for k, v in (d.get("offsets") or {}).items()}
                saved = float(d.get("initialized_at") or 0)
                if saved > 0:
                    self._initialized_at = saved
        except (json.JSONDecodeError, OSError, ValueError):
            self._offsets = {}          # 状态损坏 → 从头建立（幂等去重兜底）
        if self._initialized_at <= 0:
            self._initialized_at = time.time()   # 首次部署时刻 = 初始化基准线

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({
                "initialized_at": self._initialized_at,
                "offsets": self._offsets,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass                        # 持久化失败不致命：offset 丢 → 幂等重扫

    # ---- 触发与扫描 ----

    def maybe_sweep(self) -> SweepResult:
        """工具调用入口顺带调用：未达标只计数，达标才真扫。绝不抛异常。"""
        self._calls_since_sweep += 1
        hours = (time.time() - self._last_sweep_ts) / 3600
        if self._calls_since_sweep < self.every_n_calls and hours < self.every_hours:
            return SweepResult(triggered=False)
        reason = "hours" if hours >= self.every_hours else "rounds"
        self._calls_since_sweep = 0
        self._last_sweep_ts = time.time()
        try:
            return self._sweep(reason)
        except Exception as e:  # noqa: BLE001 - 喂食失败绝不挡住宿主工具调用
            print(f"[continuum-feed] sweep 失败（已忽略）: {e}", file=sys.stderr)
            return SweepResult(triggered=False, reason="error")

    def sweep_now(self) -> SweepResult:
        """强制立即扫描（绕过计数扳机）——消费语义场景用：memory_assemble 拿
        「最新」装配包之前先补增量，装配包才配得上"最新"。仍吞异常。
        成功后重置扳机节律（强制扫计入正常节律，避免紧随的 maybe_sweep 重复扫描）。"""
        try:
            r = self._sweep("forced")
            self._calls_since_sweep = 0
            self._last_sweep_ts = time.time()
            return r
        except Exception as e:  # noqa: BLE001
            print(f"[continuum-feed] 强制 sweep 失败（已忽略）: {e}", file=sys.stderr)
            return SweepResult(triggered=False, reason="error")

    def _sweep(self, reason: str) -> SweepResult:
        result = SweepResult(triggered=True, reason=reason)
        for path in sorted(self.projects_dir.rglob("*.jsonl")):
            key = str(path)
            try:
                st = path.stat()
            except OSError:
                continue
            offset = self._offsets.get(key)
            if offset is None:
                # 首见：老会话（创建早于部署基准线）只记 offset 不回灌
                ctime = getattr(st, "st_ctime", st.st_mtime)
                if ctime < self._initialized_at:
                    self._offsets[key] = st.st_size
                    result.old_files_marked += 1
                    continue
                offset = 0                # 新会话：从 0 全扫
            if st.st_size <= offset:
                continue
            n_new, n_dup, new_offset = self._read_increment(path, offset)
            self._offsets[key] = new_offset
            result.files_scanned += 1
            result.new_messages += n_new
            result.skipped_dedup += n_dup
        self._save_state()
        if result.new_messages or result.old_files_marked:
            print(f"[continuum-feed] sweep({reason}): +{result.new_messages} msgs, "
                  f"{result.files_scanned} files, 老会话标记 {result.old_files_marked}",
                  file=sys.stderr)
        return result

    def _read_increment(self, path: Path, offset: int) -> tuple[int, int, int]:
        """读 offset 之后的完整行并入库。返回 (新入库, 幂等跳过, 新 offset)。"""
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()
        if not data.endswith(b"\n"):
            cut = data.rfind(b"\n")       # 尾行残缺 → 截到最后一个完整行
            if cut == -1:
                return 0, 0, offset
            data = data[: cut + 1]
        new_offset = offset + len(data)

        ws_name = path.parent.name        # <workspace 编码名> 作 project_id
        external_id = path.stem           # <sessionId> 作会话外部 ID
        msgs: list[UDFMessage] = []
        for raw in data.splitlines():
            try:
                d = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            ts_ms = _dialog_ts(d)      # 单源判定：type/内部标记（含 skipRun 中断行）/时间戳
            if ts_ms is None:
                continue
            text = _strip_injections(_content_to_text(d.get("content")))
            if not text.strip():
                continue
            if len(text.encode("utf-8", errors="replace")) > _MAX_CONTENT_BYTES:
                continue
            role = d.get("role") if d.get("role") in ("user", "assistant") else "assistant"
            msgs.append(UDFMessage(
                ts=_ms_to_iso(ts_ms), role=role, host=HOST,
                session_id=external_id, content=text, meta=UDFMeta(),
            ))
        if not msgs:
            return 0, 0, new_offset
        sid = self.backend.ensure_session(HOST, external_id, project_id=ws_name)
        ids, skipped = self.backend.append_messages(sid, msgs)   # ids=message_id 元组
        return len(ids), skipped, new_offset
