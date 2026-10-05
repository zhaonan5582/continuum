# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""sweeping 触发判定：轮次/时间取先到；状态机触发后归零并调用注入动作。"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.sweeper import SweeperScheduler, should_sweep  # noqa: E402

T0 = datetime(2026, 10, 5, 10, 0, 0, tzinfo=timezone.utc)


class TestShouldSweep(unittest.TestCase):
    def test_rounds_threshold(self):
        self.assertFalse(should_sweep(19, 0.1))
        self.assertTrue(should_sweep(20, 0.1))

    def test_hours_threshold(self):
        self.assertFalse(should_sweep(1, 3.9))
        self.assertTrue(should_sweep(1, 4.0))

    def test_either_first_wins(self):
        self.assertTrue(should_sweep(25, 0.1))   # 轮次先到
        self.assertTrue(should_sweep(2, 9.0))    # 时间先到


class TestScheduler(unittest.TestCase):
    def test_state_machine_triggers_at_20_rounds(self):
        calls: list[int] = []
        sch = SweeperScheduler(sweep_fn=lambda sid: calls.append(sid), every_n_rounds=3, every_hours=999)
        outcomes = [sch.register_round(1, now=T0 + timedelta(minutes=i)) for i in range(7)]
        # 3 轮触发一次、6 轮触发一次、第 7 轮未达
        self.assertEqual([o.triggered for o in outcomes], [False, False, True, False, False, True, False])
        self.assertEqual(calls, [1, 1])
        # 触发后归零
        self.assertEqual(sch._rounds_since[1], 1)

    def test_hours_trigger_without_rounds(self):
        calls: list[int] = []
        sch = SweeperScheduler(sweep_fn=lambda sid: calls.append(sid), every_n_rounds=100, every_hours=2)
        sch.register_round(7, now=T0)
        o = sch.register_round(7, now=T0 + timedelta(hours=2))
        self.assertTrue(o.triggered)
        self.assertEqual(o.reason, "hours")
        self.assertEqual(calls, [7])

    def test_no_fn_no_crash(self):
        sch = SweeperScheduler(every_n_rounds=1, every_hours=999)
        o = sch.register_round(9, now=T0)
        self.assertTrue(o.triggered)


if __name__ == "__main__":
    unittest.main()
