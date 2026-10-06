# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""喂食引擎（宿主无关）——多宿主接入的采集层实现（docs/10 机制 M2）。

分工（关键设计）：
- **SessionFeeder（本模块）**：宿主无关的机制——扫描遍历、字节 offset 增量、
  老会话判据（部署基准线）、状态持久化、后台线程、幂等落库。**不含任何宿主知识**。
- **FeedSource（由各宿主实现）**：只回答三件事——文件在哪（roots/扩展名）、
  怎么认会话（external_id/project_id）、怎么把增量字节解析成 UDF 消息。

这样新增宿主 = 实现一个 FeedSource（或复用通用 Source），引擎零改动。
"""

from __future__ import annotations

import glob
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from continuum.storage.backend import StorageBackend
from continuum.udf import UDFMessage

DEFAULT_STATE_PATH = Path(os.environ.get(
    "CONTINUUM_FEED_STATE") or (Path.home() / ".continuum" / "feed_state.json"))


@dataclass
class SweepResult:
    triggered: bool
    reason: str = "none"          # rounds / hours / forced / error / none
    files_scanned: int = 0
    new_messages: int = 0
    skipped_dedup: int = 0
    old_files_marked: int = 0
    by_host: dict[str, int] = field(default_factory=dict)   # 各宿主新增条数（可观测性）


class FeedSource(Protocol):
    """宿主侧解析契约——引擎只依赖这四项。"""

    host: str                     # "workbuddy" / "codex" / "claude-code" / ...
    format_kind: str              # jsonl / json / sqlite / ...
    patterns: tuple[str, ...]     # 扫描 glob（相对 root，"**/*.jsonl" 等）

    def roots(self) -> tuple[Path, ...]:
        """扫描根目录（可能多个）。"""
        ...

    def external_id(self, path: Path) -> str:
        """文件 → 会话外部 ID。"""
        ...

    def project_id(self, path: Path) -> str | None:
        """文件 → 项目标识（可选）。"""
        ...

    def parse_increment(self, data: bytes, *, session_id: str,
                        project_id: str | None) -> list[UDFMessage]:
        """增量字节（完整行）→ UDF 消息列表。解析失败必须容错（跳过坏行）。"""
        ...

    def max_content_bytes(self) -> int:
        """单条内容字节上限（防御巨型行）。"""
        ...


class SessionFeeder:
    """通用喂食引擎。多 Source 并行扫描；状态 key = 文件绝对路径。"""

    def __init__(
        self,
        backend: StorageBackend,
        sources: list[FeedSource],
        state_path: Path = DEFAULT_STATE_PATH,
        every_n_calls: int = 20,
        every_hours: float = 4.0,
    ) -> None:
        self.backend = backend
        self.sources = list(sources)
        self.state_path = Path(state_path)
        self.every_n_calls = every_n_calls
        self.every_hours = every_hours
        self._offsets: dict[str, int] = {}
        self._calls_since_sweep = 0
        self._last_sweep_ts: float = time.time()
        self._initialized_at: float = 0.0
        self._bg_thread: "threading.Thread | None" = None
        self._stop_event = threading.Event()
        self._load_state()

    # ---- 生命周期 ----

    @classmethod
    def build_default(cls, backend: StorageBackend) -> "SessionFeeder | None":
        """按本机探测结果装配（只纳入目录存在的宿主）；无任何宿主 → None。"""
        from continuum.importers.sources import build_sources
        sources = build_sources()
        return cls(backend=backend, sources=sources) if sources else None

    def _load_state(self) -> None:
        """读 offset 表 + initialized_at（部署基准线一经确立不变）。"""
        self._offsets = {}
        try:
            if self.state_path.exists():
                d = json.loads(self.state_path.read_text(encoding="utf-8"))
                self._offsets = {k: int(v) for k, v in (d.get("offsets") or {}).items()}
                saved = float(d.get("initialized_at") or 0)
                if saved > 0:
                    self._initialized_at = saved
        except (json.JSONDecodeError, OSError, ValueError):
            self._offsets = {}
        if self._initialized_at <= 0:
            self._initialized_at = time.time()

    def _save_state(self) -> None:
        """合并式写入（2026-10-07 实测事故修复）：

        serve 与 shell 可能同时常驻（各持一份 offset 内存映像），覆盖式写入会互相
        抹掉对方的登记（实测：171 个老会话标记被反复重标）。故写前重读盘上最新，
        按 key 合并（内存优先），initialized_at 取更早者（基准线只前进不后退）。
        """
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            disk: dict = {}
            init = self._initialized_at
            if self.state_path.exists():
                try:
                    d = json.loads(self.state_path.read_text(encoding="utf-8"))
                    disk = {k: int(v) for k, v in (d.get("offsets") or {}).items()}
                    saved = float(d.get("initialized_at") or 0)
                    if saved > 0:
                        init = min(saved, self._initialized_at)
                except (json.JSONDecodeError, OSError, ValueError):
                    pass
            merged = {**disk, **self._offsets}       # 内存映像优先（本进程刚扫过的更新）
            self.state_path.write_text(json.dumps({
                "initialized_at": init,
                "offsets": merged,
            }, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass                    # 持久化失败不致命：offset 丢 → 幂等重扫

    # ---- 触发 ----

    def maybe_sweep(self) -> SweepResult:
        """工具调用入口顺带调用：未达标只计数。绝不抛异常。"""
        self._calls_since_sweep += 1
        hours = (time.time() - self._last_sweep_ts) / 3600
        if self._calls_since_sweep < self.every_n_calls and hours < self.every_hours:
            return SweepResult(triggered=False)
        reason = "hours" if hours >= self.every_hours else "rounds"
        self._calls_since_sweep = 0
        self._last_sweep_ts = time.time()
        try:
            return self._sweep(reason)
        except Exception as e:  # noqa: BLE001 - 喂食失败绝不挡住宿主
            print(f"[continuum-feed] sweep 失败（已忽略）: {e}", file=sys.stderr)
            return SweepResult(triggered=False, reason="error")

    def sweep_now(self) -> SweepResult:
        """强制立即扫描（绕过计数扳机）。仍吞异常。"""
        try:
            with self.backend.write_lock:
                r = self._sweep("forced")
            self._calls_since_sweep = 0
            self._last_sweep_ts = time.time()
            return r
        except Exception as e:  # noqa: BLE001
            print(f"[continuum-feed] 强制 sweep 失败（已忽略）: {e}", file=sys.stderr)
            return SweepResult(triggered=False, reason="error")

    def start_background(self, interval_sec: float = 120.0) -> threading.Thread:
        """后台线程（daemon）——进程自身时钟驱动，不依赖任何宿主行为（宪法 10）。"""
        if self._bg_thread is not None and self._bg_thread.is_alive():
            return self._bg_thread
        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                try:
                    self.sweep_now()
                except Exception:          # pragma: no cover
                    pass
                self._stop_event.wait(interval_sec)

        t = threading.Thread(target=_loop, name="continuum-feed", daemon=True)
        t.start()
        self._bg_thread = t
        hosts = ",".join(s.host for s in self.sources)
        print(f"[continuum-feed] 后台喂食已启动（{hosts}，每 {interval_sec:.0f}s 主动扫描）",
              file=sys.stderr)
        return t

    def stop_background(self) -> None:
        self._stop_event.set()
        t = self._bg_thread
        if t is not None and t.is_alive():
            t.join(timeout=5)
        self._bg_thread = None

    # ---- 扫描 ----

    def _sweep(self, reason: str) -> SweepResult:
        result = SweepResult(triggered=True, reason=reason)
        for src in self.sources:
            for path in self._iter_files(src):
                key = str(path)
                try:
                    st = path.stat()
                except OSError:
                    continue
                offset = self._offsets.get(key)
                if offset is None:
                    ctime = getattr(st, "st_ctime", st.st_mtime)
                    if ctime < self._initialized_at:
                        self._offsets[key] = st.st_size      # 老会话：只记 offset 不回灌
                        result.old_files_marked += 1
                        continue
                    offset = 0                                # 新会话：从 0 全扫
                if st.st_size <= offset:
                    continue
                n_new, n_dup, new_offset = self._read_increment(src, path, offset)
                self._offsets[key] = new_offset
                result.files_scanned += 1
                result.new_messages += n_new
                result.skipped_dedup += n_dup
                if n_new:
                    result.by_host[src.host] = result.by_host.get(src.host, 0) + n_new
        self._save_state()
        if result.new_messages or result.old_files_marked:
            by = " ".join(f"{k}+{v}" for k, v in result.by_host.items()) or "-"
            print(f"[continuum-feed] sweep({reason}): +{result.new_messages} msgs [{by}], "
                  f"{result.files_scanned} files, 老会话标记 {result.old_files_marked}",
                  file=sys.stderr)
        return result

    @staticmethod
    def _iter_files(src: FeedSource):
        for root in src.roots():
            if not root.is_dir():
                continue
            for pattern in src.patterns:
                for f in sorted(glob.iglob(str(root / pattern), recursive=True)):
                    yield Path(f)

    def _read_increment(self, src: FeedSource, path: Path, offset: int) -> tuple[int, int, int]:
        """读 offset 后的完整行并入库。返回 (新入库, 幂等跳过, 新 offset)。"""
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()
        if not data.endswith(b"\n"):
            cut = data.rfind(b"\n")       # 尾行残缺 → 截到最后一个完整行
            if cut == -1:
                return 0, 0, offset
            data = data[: cut + 1]
        new_offset = offset + len(data)

        external_id = src.external_id(path)
        project_id = src.project_id(path)
        msgs = src.parse_increment(data, session_id=external_id, project_id=project_id)
        if not msgs:
            return 0, 0, new_offset
        sid = self.backend.ensure_session(src.host, external_id, project_id=project_id)
        ids, skipped = self.backend.append_messages(sid, msgs)
        return len(ids), skipped, new_offset


def is_never_read(path: Path | str) -> bool:
    """安全红线透传（凭据文件永不读取——见 agents.registry）。"""
    from continuum.agents import is_never_read as _f
    return _f(path)
