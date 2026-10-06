# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""WorkBuddy 喂食器（兼容层）——引擎已泛化为 SessionFeeder（2026-10-07 N1）。

历史：本模块曾是喂食器的全部实现（宿主相关逻辑与机制混在一起）。
现按 docs/10 拆分：机制 → `feed_base.SessionFeeder`（宿主无关：offset/老会话判据/
状态持久化/后台线程）；解析 → `sources.WorkbuddySource`。本模块只保留**旧签名兼容壳**，
保证既有 serve/shell/CLI/测试调用面零改动。

新代码请直接用：`from continuum.importers.feed_base import SessionFeeder`
（多宿主：`SessionFeeder.build_default(backend)` 按探测结果装配全部已装宿主）。
"""

from __future__ import annotations

from pathlib import Path

from continuum.importers.feed_base import (  # noqa: F401  (re-export 兼容)
    DEFAULT_STATE_PATH,
    SweepResult,
    SessionFeeder,
)
from continuum.importers.sources import WorkbuddySource

DEFAULT_PROJECTS_DIR = Path.home() / ".workbuddy" / "projects"
HOST = "workbuddy"


class WorkbuddyFeed(SessionFeeder):
    """单源（WorkBuddy）喂食器——兼容旧签名，内部走通用引擎。"""

    def __init__(self, backend, projects_dir: Path | str = DEFAULT_PROJECTS_DIR,
                 state_path: Path | str = DEFAULT_STATE_PATH,
                 every_n_calls: int = 20, every_hours: float = 4.0) -> None:
        super().__init__(
            backend,
            [WorkbuddySource(root=Path(projects_dir))],
            state_path=Path(state_path),
            every_n_calls=every_n_calls,
            every_hours=every_hours,
        )

    @classmethod
    def build_default(cls, backend) -> "WorkbuddyFeed | None":
        """projects 目录存在才返回（旧行为保持）。"""
        if not DEFAULT_PROJECTS_DIR.is_dir():
            return None
        return cls(backend=backend)
