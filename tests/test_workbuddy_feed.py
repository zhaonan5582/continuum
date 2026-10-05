# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""WorkbuddyFeed 增量喂食器测试（第 0 扳机 WorkBuddy 接线）。"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from continuum.importers.workbuddy_feed import WorkbuddyFeed, SweepResult  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402

MIGRATIONS = REPO / "src" / "continuum" / "storage" / "migrations" / "sql"


def _msg_line(text: str, role: str = "user", ts_ms: int | None = None) -> str:
    ts = ts_ms if ts_ms is not None else int(time.time() * 1000)
    return json.dumps({"type": "message", "role": role, "timestamp": ts,
                       "content": [{"type": "input_text", "text": text}]})


class TestWorkbuddyFeed(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.proj = Path(self.td.name) / "projects" / "ws-demo"
        self.proj.mkdir(parents=True)
        self.be = SQLiteBackend(":memory:", MIGRATIONS)

    def tearDown(self):
        self.be.close()
        self.td.cleanup()

    def _make_feed(self, initialized_at: float | None = None,
                   every_n_calls: int = 1) -> WorkbuddyFeed:
        feed = WorkbuddyFeed(
            backend=self.be,
            projects_dir=Path(self.td.name) / "projects",
            state_path=Path(self.td.name) / "state.json",
            every_n_calls=every_n_calls,
        )
        if initialized_at is not None:
            feed._initialized_at = initialized_at
        return feed

    def test_new_session_imported_from_zero(self):
        """部署基准线之后创建的会话 → 从 0 全扫入库。"""
        f = self.proj / "sess-a.jsonl"
        f.write_text(_msg_line("第一条用户话语") + "\n"
                     + json.dumps({"type": "reasoning"}) + "\n"   # 非 message 行应被滤
                     + _msg_line("助手回应", role="assistant") + "\n",
                     encoding="utf-8")
        feed = self._make_feed(initialized_at=time.time() - 3600)
        r = feed.maybe_sweep()
        self.assertTrue(r.triggered)
        self.assertEqual(r.new_messages, 2)
        # 入库可查
        sid = self.be.ensure_session("workbuddy", "sess-a")
        rows = self.be.list_session_messages_with_ids(sid, limit=10)
        self.assertEqual(len(rows), 2)
        self.assertIn("第一条用户话语", rows[0][1].content)

    def test_incremental_append_no_dup(self):
        """追加新行 → 只导新增；重扫同段幂等去重。"""
        f = self.proj / "sess-b.jsonl"
        f.write_text(_msg_line("第一轮") + "\n", encoding="utf-8")
        feed = self._make_feed(initialized_at=time.time() - 3600)
        feed.maybe_sweep()
        with f.open("a", encoding="utf-8") as fh:
            fh.write(_msg_line("第二轮增量") + "\n")
        r = feed.maybe_sweep()
        self.assertEqual(r.new_messages, 1)
        # offset 回退重扫 → 幂等去重（不脏库）
        feed._offsets[str(f)] = 0
        r2 = feed._sweep("rounds")
        self.assertEqual(r2.new_messages, 0)
        self.assertGreaterEqual(r2.skipped_dedup, 2)

    def test_old_files_marked_not_imported(self):
        """创建时间早于部署基准线的老会话 → 只记 offset 不回灌（存量走显式导入）。"""
        f = self.proj / "old-sess.jsonl"
        f.write_text(_msg_line("历史存量不应自动入库") + "\n", encoding="utf-8")
        feed = self._make_feed(initialized_at=time.time() + 3600)  # 基准线在未来 → 全部算老
        r = feed.maybe_sweep()
        self.assertEqual(r.old_files_marked, 1)
        self.assertEqual(r.new_messages, 0)
        # offset 已标记 → 后续不重复标记
        r2 = feed.maybe_sweep()
        self.assertEqual(r2.old_files_marked, 0)

    def test_not_triggered_below_threshold(self):
        """未达 20 次调用 / 4h → 只计数不扫描。"""
        f = self.proj / "sess-c.jsonl"
        f.write_text(_msg_line("还没到扳机阈值") + "\n", encoding="utf-8")
        feed = self._make_feed(every_n_calls=20)
        feed._initialized_at = time.time() - 3600
        r = feed.maybe_sweep()
        self.assertIsInstance(r, SweepResult)
        self.assertFalse(r.triggered)
        self.assertEqual(r.new_messages, 0)

    def test_sweep_now_bypasses_counter(self):
        """sweep_now：计数为 0（远未达扳机）也强制扫描；扫后节律重置。"""
        f = self.proj / "sess-force.jsonl"
        f.write_text(_msg_line("强制扫描应立即入库") + "\n", encoding="utf-8")
        feed = self._make_feed(initialized_at=time.time() - 3600)
        self.assertEqual(feed._calls_since_sweep, 0)          # 远未达扳机
        r = feed.sweep_now()
        self.assertTrue(r.triggered)
        self.assertEqual(r.reason, "forced")
        self.assertEqual(r.new_messages, 1)
        self.assertEqual(feed._calls_since_sweep, 0)          # 节律已重置

    def test_sweep_failure_does_not_raise(self):
        """喂食失败绝不向调用方抛异常（宿主工具调用不受影响）。"""
        feed = self._make_feed(initialized_at=time.time() - 3600)
        feed.projects_dir = Path(self.td.name) / "not-exist"   # 扫描中途目录消失等异常
        feed._offsets["ghost"] = 12345                         # 幽灵 offset 触发路径
        r = feed.maybe_sweep()                                 # 不应抛
        self.assertIsInstance(r, SweepResult)


if __name__ == "__main__":
    unittest.main()
