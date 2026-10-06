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
from continuum.server import ContinuumServer  # noqa: E402
from continuum.server.tools import AppendRequest  # noqa: E402

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

    def test_invalid_udf_rejects_whole_batch(self):
        """批内一条非法 → 整批拒绝（全或无），绝不留半批落库。"""
        with self.assertRaises(ValueError):
            self.be.append_messages(self.sid, [
                _msg("2026-10-05T10:01:00.000Z", "user", "合法1"),
                _msg("bad-ts", "user", "非法"),
                _msg("2026-10-05T10:02:00.000Z", "user", "合法2"),
            ])
        n = self.be.conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE content IN ('合法1','合法2')"
        ).fetchone()["c"]
        self.assertEqual(n, 0, "整批拒绝后不应有任何落库")

    def test_same_second_same_content_is_deduped(self):
        """已知取舍（文档化）：秒级 ts 下同秒同角色同内容视为重推被去重。
        适配器规范要求毫秒级 ts（udf.schema.json ts description），现实中概率≈0。"""
        ids, skipped = self.be.append_messages(self.sid, [
            _msg("2026-10-05T10:00:00.000Z", "user", "好"),
            _msg("2026-10-05T10:00:00.000Z", "user", "好"),
        ])
        self.assertEqual((len(ids), skipped), (1, 1))

    def test_short_query_uses_like_fallback(self):
        """短查询契约变更（2026-10-06 楠哥定 99% 召回目标）：旧契约「trigram ≥3 字符、
        短查询显式空结果」已推翻——原文层必须覆盖短查询，否则"对话全量入库 → 任何内容
        可寻回"不成立（实测 2 字中文召回仅 30%）。新契约：LIKE 兜底命中；无关短串不误报。"""
        self.be.append_messages(self.sid, [_msg("2026-10-05T10:00:00.000Z", "user", "收敛扇驱逐公式")])
        hits = self.be.search_content("收")
        self.assertEqual(len(hits), 1)
        self.assertIn("收敛扇", hits[0]["content"])
        self.assertEqual(self.be.search_content("缠"), [])   # 无关短串：不误报


if __name__ == "__main__":
    unittest.main()


class TestSpecialCharsRoundtrip(unittest.TestCase):
    """商业化前排查（2026-10-06）：特殊字符存储往返必须逐字节一致。
    注意：验证对象是存储层（messages.content），不是 recall（FTS 分词会改写匹配）。"""

    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)

    def tearDown(self):
        self.be.close()

    def test_weird_chars_verbatim(self):
        weird = ["emoji 🧠🛡️✨", "引号\"双引号和'单引号", r"反斜杠C://path//to",
                 "换行\n第二行", "<script>alert(1)</script>", "中文＋日本語한국어"]
        msgs = [UDFMessage(ts=f"2026-01-01T00:00:{i:02d}.000Z", role="user",
                           host="w", session_id="x", content=c)
                for i, c in enumerate(weird)]
        self.srv.memory_append(AppendRequest(host_agent="w", external_session_id="x",
                                             messages=tuple(msgs)))
        sid = self.be.conn.execute("SELECT id FROM sessions").fetchone()["id"]
        stored = [r["content"] for r in self.be.conn.execute(
            "SELECT content FROM messages WHERE session_id=? ORDER BY seq", (sid,))]
        self.assertEqual(weird, stored)
