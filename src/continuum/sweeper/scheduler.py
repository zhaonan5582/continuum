# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Sweeping 调度器骨架（宪法 10：机制扳机——沉淀触发不依赖 agent 自觉）。

P0 职责：触发判定（纯函数，可测）+ 轮次/时间双条件状态机。
P1 职责：sweep 动作注入（结构化预沉淀）。
设计要点：触发条件 = 每 N 轮 或 每 T 小时，取先到（§5.1）；
        落库推送（第 0 扳机）由适配器直调 memory_append，不经过本调度器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from continuum.version import DESIGN_CONSTANTS


def should_sweep(
    rounds_since_sweep: int,
    hours_since_sweep: float,
    *,
    every_n_rounds: int = DESIGN_CONSTANTS["SWEEP_EVERY_N_ROUNDS"],
    every_hours: float = DESIGN_CONSTANTS["SWEEP_EVERY_HOURS"],
) -> bool:
    """纯函数：轮次或时间任一达标即触发（取先到）。"""
    return rounds_since_sweep >= every_n_rounds or hours_since_sweep >= every_hours


@dataclass
class SweepOutcome:
    triggered: bool
    reason: str                      # rounds / hours / none
    rounds_at_trigger: int
    hours_at_trigger: float


@dataclass
class SweeperScheduler:
    """轮次/时间双条件状态机。sweep_fn 由 P1 注入（结构化预沉淀动作）。"""

    backend: object = None                                  # P1 注入沉淀动作时使用
    every_n_rounds: int = DESIGN_CONSTANTS["SWEEP_EVERY_N_ROUNDS"]
    every_hours: float = DESIGN_CONSTANTS["SWEEP_EVERY_HOURS"]
    sweep_fn: Callable[[int], None] | None = None           # P1: def sweep(session_id) -> ...
    _rounds_since: dict[int, int] = field(default_factory=dict)   # session_id -> rounds
    _last_sweep_ts: dict[int, datetime] = field(default_factory=dict)

    def register_round(self, session_id: int, now: datetime | None = None) -> SweepOutcome:
        """适配器每收到一轮对话调用一次（机制扳机的入口）。"""
        now = now or datetime.now(timezone.utc)
        self._rounds_since[session_id] = self._rounds_since.get(session_id, 0) + 1
        last = self._last_sweep_ts.get(session_id)
        if last is None:
            # 首次见到该会话：建立时间基线（否则 hours 恒为 0，时间条件永不触发）
            self._last_sweep_ts[session_id] = now
            last = now
        hours = (now - last).total_seconds() / 3600.0
        rounds = self._rounds_since[session_id]

        triggered = should_sweep(
            rounds, hours, every_n_rounds=self.every_n_rounds, every_hours=self.every_hours
        )
        outcome = SweepOutcome(
            triggered=triggered,
            reason=("rounds" if rounds >= self.every_n_rounds else "hours") if triggered else "none",
            rounds_at_trigger=rounds,
            hours_at_trigger=hours,
        )
        if triggered and self.sweep_fn is not None:
            self.sweep_fn(session_id)
            self._rounds_since[session_id] = 0
            self._last_sweep_ts[session_id] = now
        return outcome
