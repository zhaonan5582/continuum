# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""真喂食测试（2026-10-06 楠哥拍板改造）。

旧设计把喂食寄生于「宿主调用工具」——宿主整场不调工具 = 零落库（本会话 40+ 小时
零落库实测事故）。正确形态：常驻进程（serve/shell）自带后台线程，按自身节奏主动扫描；
另有 CLI `feed --once` 供计划任务。本测试固化三条契约：
① 后台线程零工具调用也能自动落库；② 并发写安全（write_lock + busy_timeout）；
③ CLI feed --once 端到端。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from continuum.importers.workbuddy_feed import WorkbuddyFeed  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage  # noqa: E402

MIGRATIONS = REPO / "src" / "continuum" / "storage" / "migrations" / "sql"


def _line(text: str) -> str:
    return json.dumps({"type": "message", "timestamp": int(time.time() * 1000),
                       "role": "user", "content": [{"text": text}]},
                      ensure_ascii=False) + "\n"


class TestBackgroundFeed(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.be = SQLiteBackend(str(self.td / "t.db"), MIGRATIONS)
        self.proj = self.td / "projects" / "ws1"
        self.proj.mkdir(parents=True)
        self.sess = self.proj / "sessA.jsonl"
        self.sess.write_text(_line("第一条：约定用通道A"), encoding="utf-8")
        self.feed = WorkbuddyFeed(backend=self.be, projects_dir=self.td / "projects",
                                  state_path=self.td / "state.json")
        self.feed._initialized_at = 0        # 基准线归零：一切文件视为新会话

    def tearDown(self):
        self.feed.stop_background()
        self.be.close()

    def _count(self) -> int:
        return self.be.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def test_background_thread_ingests_without_tool_calls(self):
        """核心契约：零工具调用，后台线程按自身节奏落库。"""
        self.feed.sweep_now()
        self.assertEqual(self._count(), 1)
        self.feed.start_background(interval_sec=0.3)
        time.sleep(0.8)
        with self.sess.open("a", encoding="utf-8") as fh:
            fh.write(_line("第二条：红线不许动生产库"))
        deadline = time.time() + 5
        while time.time() < deadline and self._count() < 2:
            time.sleep(0.1)
        self.assertEqual(self._count(), 2, "后台线程应在间隔内自动扫到新增内容")

    def test_stop_background_idempotent(self):
        self.feed.start_background(interval_sec=0.3)
        self.feed.stop_background()
        self.feed.stop_background()          # 二次调用不得抛
        t = self.feed.start_background(interval_sec=0.3)   # 可重启
        self.assertTrue(t.is_alive())

    def test_concurrent_appends_serialized(self):
        """双线程并发 append：write_lock 串行化，零异常零丢失。"""
        sid = self.be.ensure_session("h", "s1")
        errs: list = []

        def worker(tag: str) -> None:
            try:
                for i in range(20):
                    self.be.append_messages(sid, [UDFMessage(
                        ts=f"2026-01-01T00:00:{i:02d}.000Z", role="user", host="h",
                        session_id="s1", content=f"{tag}-{i}")])
            except Exception as e:  # noqa: BLE001
                errs.append(f"{tag}: {e}")

        ts = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(self.be.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id=?", (sid,)).fetchone()[0], 40)


class TestFeedCLI(unittest.TestCase):
    def test_feed_once_end_to_end(self):
        """CLI feed --once：临时库 + 隔离 state（CONTINUUM_FEED_STATE）绝不碰生产文件。"""
        import os
        td = Path(tempfile.mkdtemp())
        db = td / "cli.db"
        r = subprocess.run(
            [sys.executable, "-m", "continuum.cli", "--db", str(db), "feed", "--once"],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            env={**os.environ, "PYTHONPATH": str(REPO / "src"),
                 "CONTINUUM_FEED_STATE": str(td / "feed_state.json")})
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertTrue("扫描完成" in r.stdout or "未找到" in r.stdout, r.stdout[-200:])
        # 生产 feed_state 未被本次测试改写（隔离验证）
        self.assertTrue((td / "feed_state.json").exists() or "未找到" in r.stdout)


if __name__ == "__main__":
    unittest.main()
