# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""商业化标准测试（v1.2 审查固化）：零外联、DoS 上限、备份通道、规模基准。"""
from __future__ import annotations

import statistics
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFError, UDFMessage, UDFMeta  # noqa: E402
from continuum.version import DESIGN_CONSTANTS  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


class TestZeroNetwork(unittest.TestCase):
    """隐私承诺固化：核心包源码禁止出现任何网络库引用（默认零外联）。"""

    def test_no_network_imports(self):
        src = Path(__file__).resolve().parents[1] / "src" / "continuum"
        bad_keywords = ("urllib", "requests", "httpx", "socket", "http.client", "urllib3")
        offenders = [
            str(p)
            for p in src.rglob("*.py")
            for kw in bad_keywords
            if kw in p.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [], f"核心包出现网络库引用（违反零外联承诺）: {offenders}")


class TestDoSLimits(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.sid = self.be.ensure_session("workbuddy", "s1")

    def tearDown(self):
        self.be.close()

    def test_oversized_content_rejected(self):
        """单条 content 超 MAX_CONTENT_BYTES 整批拒绝（磁盘耗尽 DoS 防线）。"""
        limit = DESIGN_CONSTANTS["MAX_CONTENT_BYTES"]
        oversized = "A" * (limit + 1)
        with self.assertRaises(ValueError):
            self.be.append_messages(
                self.sid,
                [UDFMessage(ts="2026-10-05T10:00:00.000Z", role="user",
                            host="workbuddy", session_id="s1", content=oversized)],
            )
        n = self.be.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        self.assertEqual(n, 0, "超限整批拒绝，零落库")

    def test_content_at_limit_accepted(self):
        limit = DESIGN_CONSTANTS["MAX_CONTENT_BYTES"]
        at_limit = "A" * limit
        ids, _ = self.be.append_messages(
            self.sid,
            [UDFMessage(ts="2026-10-05T10:00:00.000Z", role="user",
                        host="workbuddy", session_id="s1", content=at_limit)],
        )
        self.assertEqual(len(ids), 1)

    def test_empty_host_rejected_at_tools_layer(self):
        from continuum.server import AppendRequest, ContinuumServer
        srv = ContinuumServer(self.be)
        with self.assertRaises(ValueError):
            srv.memory_append(AppendRequest(host_agent="", external_session_id="s",
                                            messages=(_msg("x"),)))
        with self.assertRaises(ValueError):
            self.be.ensure_session("", "s2")


def _msg(content="c", role="user"):
    return UDFMessage(ts="2026-10-05T10:00:00.000Z", role=role, host="workbuddy",
                      session_id="s", content=content)


class TestBackup(unittest.TestCase):
    def test_backup_to_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            be = SQLiteBackend(Path(td) / "main.continuum.db", MIGRATIONS)
            sid = be.ensure_session("workbuddy", "s1")
            be.append_messages(sid, [_msg("要备份的记忆")])
            dest = Path(td) / "backup.continuum.db"
            pages = be.backup_to(dest)
            self.assertGreater(pages, 0)
            be.close()
            # 备份可独立打开且数据完整
            be2 = SQLiteBackend(dest, MIGRATIONS)
            rows = be2.conn.execute("SELECT content FROM messages").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["content"], "要备份的记忆")
            be2.close()


class TestScaleBaseline(unittest.TestCase):
    """规模基线（轻量固化）：1 万消息落库 + FTS 延迟 <500ms 预算。"""

    def test_10k_messages_and_fts_latency(self):
        import tempfile
        from datetime import datetime, timedelta, timezone
        with tempfile.TemporaryDirectory() as td:
            be = SQLiteBackend(Path(td) / "scale.continuum.db", MIGRATIONS)
            sid = be.ensure_session("workbuddy", "s1")
            base = datetime(2026, 10, 5, 0, 0, 0, tzinfo=timezone.utc)
            t0 = time.perf_counter()
            for start in range(0, 10_000, 2_000):
                batch = [
                    UDFMessage(
                        ts=(base + timedelta(seconds=start + i)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                        role="user" if (start + i) % 2 else "assistant",
                        host="workbuddy", session_id="s1",
                        content=f"消息{start+i}：收敛扇驱逐公式中文 English mixed {i}",
                    )
                    for i in range(start, min(start + 2_000, 10_000))
                ]
                be.append_messages(sid, batch)
            elapsed = time.perf_counter() - t0
            # CI runner 性能不可控：放宽到 60s（本地基线 ~2s，回归监控以本地为准）
            self.assertLess(elapsed, 60, f"1 万消息落库过慢: {elapsed:.1f}s")
            lat = []
            for q in ("驱逐公式", "English", "消息9999"):
                for _ in range(3):
                    s = time.perf_counter()
                    be.search_content(q)
                    lat.append((time.perf_counter() - s) * 1000)
            med = sorted(lat)[len(lat) // 2]
            # 同上：median 管回归（<500ms 预算），max 宽容防 CI flaky
            self.assertLess(med, 500, f"FTS 中位延迟超预算: {med:.0f}ms")
            self.assertLess(max(lat), 2000, f"FTS 最大延迟异常: {max(lat):.0f}ms")
            be.close()


if __name__ == "__main__":
    unittest.main()
