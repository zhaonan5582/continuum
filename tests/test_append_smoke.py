# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""append 幂等落库冒烟：会话 → 落库 → 重推去重 → 迭代 → FTS → 审计。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage, UDFMeta  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"

TS1 = "2026-10-05T10:00:00.000Z"
TS2 = "2026-10-05T10:00:05.000Z"


def _msg(ts, role, content, tokens=None):
    return UDFMessage(
        ts=ts, role=role, host="workbuddy", session_id="sess-001",
        content=content, meta=UDFMeta(tokens=tokens),
    )


class TestAppendSmoke(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.sid = self.be.ensure_session("workbuddy", "sess-001", title="收敛扇调试", project_id="continuum")

    def tearDown(self):
        self.be.close()

    def test_full_smoke(self):
        # 1) 落库
        ids, skipped = self.be.append_messages(
            self.sid,
            [_msg(TS1, "user", "把驱逐公式补第四维：当前项目相关性"),
             _msg(TS2, "assistant", "已加入 EVICT_CROSS_PROJECT_DECAY_FLOOR=0.3", tokens=18)],
        )
        self.assertEqual(len(ids), 2)
        self.assertEqual(skipped, 0)

        # 2) 幂等：同一批消息重推 → 全部跳过，不产生重复行
        ids2, skipped2 = self.be.append_messages(
            self.sid,
            [_msg(TS1, "user", "把驱逐公式补第四维：当前项目相关性"),
             _msg(TS2, "assistant", "已加入 EVICT_CROSS_PROJECT_DECAY_FLOOR=0.3", tokens=18)],
        )
        self.assertEqual((len(ids2), skipped2), (0, 2))
        n = self.be.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        self.assertEqual(n, 2)

        # 3) 迭代回读：seq 升序、原文一致
        got = list(self.be.iter_session_messages(self.sid))
        self.assertEqual([m.ts for m in got], [TS1, TS2])
        self.assertIn("EVICT_CROSS_PROJECT_DECAY_FLOOR", got[1].content)

        # 4) FTS（trigram，中文）命中
        hits = self.be.search_content("驱逐公式")
        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(any("第四维" in h["content"] for h in hits))

        # 5) 审计：session.ensure + append 均留痕
        acts = [r["action"] for r in self.be.conn.execute("SELECT action FROM audit_log")]
        self.assertIn("session.ensure", acts)
        self.assertIn("append", acts)

    def test_session_idempotent(self):
        sid_a = self.be.ensure_session("workbuddy", "sess-001")
        self.assertEqual(sid_a, self.sid)

    def test_invalid_udf_rejected_at_storage(self):
        bad = UDFMessage(ts="not-a-time", role="user", host="w", session_id="x", content="c")
        with self.assertRaises(ValueError):
            self.be.append_messages(self.sid, [bad])


if __name__ == "__main__":
    unittest.main()
