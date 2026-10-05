# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""P1-a 验收测试：启发式抽取 + 实体挂载 + 四字段强制 + 消歧幂等。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.extract import extract_candidates_from_text, run_extraction  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage, UDFMeta  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


def _msg(ts, role, content):
    return UDFMessage(ts=ts, role=role, host="workbuddy", session_id="s1", content=content)


class TestHeuristics(unittest.TestCase):
    def test_decision_pattern(self):
        self.assertEqual(extract_candidates_from_text("我们决定用 SQLite 做存储。")[0][0], "decision")

    def test_redline_pattern(self):
        self.assertEqual(extract_candidates_from_text("不许动红线文件！")[0][0], "redline")

    def test_exclusion_pattern(self):
        self.assertEqual(extract_candidates_from_text("Neo4j 方案试过了，走不通。")[0][0], "exclusion")

    def test_no_match_no_output(self):
        self.assertEqual(extract_candidates_from_text("今天天气不错"), [])

    def test_short_sentence_skipped(self):
        self.assertEqual(extract_candidates_from_text("好的。"), [])


class TestRunExtraction(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.sid = self.be.ensure_session("workbuddy", "s1")
        self.ids, _ = self.be.append_messages(self.sid, [
            _msg("2026-10-05T10:00:00.000Z", "user", "我们决定用 SQLite 做存储，不许再改数据库。"),
            _msg("2026-10-05T10:00:10.000Z", "assistant", "好的，已加入排除清单：Neo4j 试过走不通。"),
            _msg("2026-10-05T10:00:20.000Z", "user", "今天天气不错。"),
        ])
        self.msgs = list(self.be.iter_session_messages(self.sid))

    def tearDown(self):
        self.be.close()

    def test_extraction_produces_pending_with_four_fields(self):
        produced, _, scanned = run_extraction(self.be, self.sid, list(zip(self.ids, self.msgs)))
        self.assertGreater(produced, 0)
        self.assertEqual(scanned, 3)
        rows = self.be.conn.execute("SELECT * FROM memories").fetchall()
        for r in rows:
            # 宪法 2：四强制字段齐全（source_message_id/created_at/evidence_level/valid_from）
            self.assertIsNotNone(r["source_message_id"])
            self.assertIsNotNone(r["created_at"])
            self.assertIsNotNone(r["valid_from"])
            self.assertIn(r["evidence_level"], ("tested", "cited", "inferred"))
            self.assertEqual(r["status"], "pending")          # 无 judge 一律 pending
            self.assertEqual(r["stated_by"] in ("user", "agent"), True)

    def test_stated_by_split(self):
        run_extraction(self.be, self.sid, list(zip(self.ids, self.msgs)))
        rows = self.be.conn.execute(
            "SELECT stated_by, statement FROM memories WHERE statement LIKE '%SQLite%'"
        ).fetchall()
        self.assertEqual(rows[0]["stated_by"], "user")        # user 说的 → user
        agent_rows = self.be.conn.execute(
            "SELECT stated_by FROM memories WHERE statement LIKE '%Neo4j%'"
        ).fetchall()
        self.assertEqual(agent_rows[0]["stated_by"], "agent")  # agent 说的 → agent

    def test_extraction_idempotent(self):
        pair = list(zip(self.ids, self.msgs))
        p1, _, _ = run_extraction(self.be, self.sid, pair)
        p2, _, _ = run_extraction(self.be, self.sid, pair)   # 重跑不重复写入
        self.assertEqual(p2, 0)
        n = self.be.conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
        self.assertEqual(n, p1)

    def test_entity_mention_attach(self):
        """已有实体出现在 statement 中 → 记 mention；无命中不新建实体（P1 只挂不建）。"""
        self.be.find_or_create_entity("project", "SQLite 项目", canonical_name="SQLite")
        run_extraction(self.be, self.sid, list(zip(self.ids, self.msgs)))
        n = self.be.conn.execute(
            "SELECT COUNT(*) c FROM mem_mentions"
        ).fetchone()["c"]
        self.assertGreaterEqual(n, 1)
        entities = self.be.conn.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"]
        self.assertEqual(entities, 1)                          # 只挂不建


if __name__ == "__main__":
    unittest.main()
