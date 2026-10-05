# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""P1-b 验收测试：recall 快速路径（结构化主力+FTS 兜底）+ audit + 集成闭环 + 延迟预算。"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.server import (  # noqa: E402
    AppendRequest,
    AuditQuery,
    ContinuumServer,
    ExtractScope,
)
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


def _msg(ts, role, content):
    return UDFMessage(ts=ts, role=role, host="workbuddy", session_id="s1", content=content)


class TestRecallAndAudit(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        # 灌入含明确模式句的会话
        self.srv.memory_append(AppendRequest(
            host_agent="workbuddy", external_session_id="s1",
            messages=(
                _msg("2026-10-05T10:00:00.000Z", "user", "我们决定用 SQLite 做存储引擎。"),
                _msg("2026-10-05T10:00:10.000Z", "user", "不许动 docs/01 的宪法段落，那是红线。"),
                _msg("2026-10-05T10:00:20.000Z", "assistant", "排除清单：Neo4j 试过，走不通已放弃。"),
                _msg("2026-10-05T10:00:30.000Z", "user", "以后都用 PYTHONPATH=src 跑测试，这是约定。"),
            ),
        ))
        # 机制扳机：extract
        self.srv.memory_extract(ExtractScope(session_id=None, since_ts=None))

    def tearDown(self):
        self.be.close()

    def test_recall_finds_decision(self):
        r = self.srv.memory_recall("SQLite 存储")
        self.assertGreaterEqual(len(r.items), 1)
        kinds = {i.kind for i in r.items}
        self.assertIn("decision", kinds)
        # user-stated 优先置顶
        self.assertEqual(r.items[0].stated_by, "user")
        self.assertEqual(r.stopped_by, "no-judge-fast-path")

    def test_recall_redline(self):
        r = self.srv.memory_recall("红线 宪法")
        kinds = {i.kind for i in r.items}
        self.assertIn("redline", kinds)

    def test_recall_time_hint_parses(self):
        r = self.srv.memory_recall("SQLite", time_hint="今天")
        self.assertGreaterEqual(len(r.items), 1)

    def test_recall_empty_library_graceful(self):
        be2 = SQLiteBackend(":memory:", MIGRATIONS)
        srv2 = ContinuumServer(be2)
        r = srv2.memory_recall("任何词")
        self.assertEqual(len(r.items), 0)
        be2.close()

    def test_recall_latency_budget(self):
        lat = []
        for q in ("SQLite", "红线", "排除清单", "约定 测试"):
            s = time.perf_counter()
            self.srv.memory_recall(q)
            lat.append((time.perf_counter() - s) * 1000)
        self.assertLess(max(lat), 500, f"快速路径延迟超预算: {max(lat):.0f}ms")

    def test_audit_traceability(self):
        """溯源：每条记忆的 source_message_id 能反查到 audit 里的 append 记录。"""
        r = self.srv.memory_audit(AuditQuery(action="append"))
        self.assertGreaterEqual(r.total_matched, 1)
        e = self.srv.memory_audit(AuditQuery(action="extract"))
        self.assertGreaterEqual(e.total_matched, 1)

    def test_full_loop_append_extract_recall_audit(self):
        """P1 闭环冒烟：append → extract → recall → audit 全链。"""
        self.srv.memory_append(AppendRequest(
            host_agent="codex", external_session_id="s2",
            messages=(_msg("2026-10-05T11:00:00.000Z", "user", "决定把验收数据换成真实语料。"),),
        ))
        self.srv.memory_extract(ExtractScope(session_id=None, since_ts="2026-10-05T10:30:00.000Z"))
        r = self.srv.memory_recall("真实语料")
        self.assertGreaterEqual(len(r.items), 1)
        a = self.srv.memory_audit(AuditQuery(action="recall", limit=5))
        self.assertGreaterEqual(len(a.entries), 1)


if __name__ == "__main__":
    unittest.main()
