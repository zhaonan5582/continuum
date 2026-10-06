# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""append 序号契约：幂等重扫不留洞（2026-10-06 生产库实测事故固化）。

事故：`INSERT OR IGNORE` 命中重复时仍消耗 seq → 幂等重扫一次在生产库留下
875 个 seq 洞（seq 语义 = per-session ordinal strictly increasing，紧凑更干净）。
修复：IGNORE 时回退序号。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage  # noqa: E402

MIGRATIONS = REPO / "src" / "continuum" / "storage" / "migrations" / "sql"


def _msg(i: int) -> UDFMessage:
    return UDFMessage(ts=f"2026-01-01T00:00:{i:02d}.000Z", role="user",
                      host="h", session_id="s1", content=f"msg{i}")


class TestAppendSeqHoles(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.sid = self.be.ensure_session("h", "s1")

    def tearDown(self):
        self.be.close()

    def _seqs(self) -> list[int]:
        return [r[0] for r in self.be.conn.execute(
            "SELECT seq FROM messages WHERE session_id=? ORDER BY seq", (self.sid,))]

    def test_rescan_leaves_no_holes(self):
        self.be.append_messages(self.sid, [_msg(1), _msg(2), _msg(3)])
        self.assertEqual(self._seqs(), [1, 2, 3])
        # 幂等重扫：3 条 IGNORE + 1 条新 → 新消息应拿 seq=4（不留洞）
        ids, skipped = self.be.append_messages(self.sid, [_msg(1), _msg(2), _msg(3), _msg(4)])
        self.assertEqual(len(ids), 1)
        self.assertEqual(skipped, 3)
        self.assertEqual(self._seqs(), [1, 2, 3, 4])

    def test_all_ignored_keeps_compact(self):
        self.be.append_messages(self.sid, [_msg(1), _msg(2)])
        self.be.append_messages(self.sid, [_msg(1), _msg(2)])   # 全 IGNORE
        self.assertEqual(self._seqs(), [1, 2])


if __name__ == "__main__":
    unittest.main()
