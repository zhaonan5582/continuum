# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""P1 验收基线（合成语料版）：120 条事实抽取 + 100 查询召回 ≥75%。

注意：合成语料是"自合成自测"——只验证管道连通性与召回下限，
正式验收以 WorkBuddy 真实历史语料为准（docs/01 §9 P1）。
"""
from __future__ import annotations

import sys
import time
import unittest

# Windows CI runner 的 stdout 默认 cp1252，print 中文会崩——统一 UTF-8 + 容错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.server import AppendRequest, ContinuumServer, ExtractScope  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"

N = 120
BASE = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)


def _ts(i: int) -> str:
    return (BASE + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fact_sentence(i: int) -> str:
    kinds = [
        f"我们决定把模块{i}的接口改为版本{i % 5}。",
        f"约定：模块{i}的日志一律走统一通道{i % 4}。",
        f"红线：模块{i}不许直接改配置文件{i % 6}。",
        f"排除：方案{i % 9}在模块{i}上试过，走不通已放弃。",
    ]
    return kinds[i % len(kinds)]


def _query(i: int) -> str:
    """查询 = 目标语句的独有实体词（模块{i} 全库唯一）。
    v1.2 修正：search_content 已改短语精确匹配，复合查询（带空格）作为连续短语
    在原文不存在——查询必须是原文中的连续子串（与真实用户查询语义一致）。"""
    return f"模块{i}"


class TestSyntheticRecallBaseline(unittest.TestCase):
    def test_120_facts_extract_and_recall(self):
        be = SQLiteBackend(":memory:", MIGRATIONS)
        srv = ContinuumServer(be)
        try:
            # 1) 灌入 120 条含事实的消息
            msgs = tuple(
                UDFMessage(ts=_ts(i), role="user" if i % 2 else "assistant",
                           host="workbuddy", session_id="synth",
                           content=_fact_sentence(i))
                for i in range(N)
            )
            r = srv.memory_append(AppendRequest(host_agent="workbuddy",
                                                external_session_id="synth", messages=msgs))
            self.assertEqual(r.accepted, N)

            # 2) 机制扳机：抽取（无 judge，全 pending）→ 模拟确认（pending → active）
            ex = srv.memory_extract(ExtractScope(session_id=r.session_id))
            self.assertGreaterEqual(ex.produced_pending, 100,
                                    f"抽取产出过低: {ex.produced_pending}")
            be.conn.execute("UPDATE memories SET status='active' WHERE status='pending'")

            # 3) 100 次查询，统计召回（top-20 内含目标关键词即命中）
            hits = 0
            queries = 0
            lat: list[float] = []
            for i in range(0, 100):
                q = _query(i)
                target = _fact_sentence(i)[:20]      # 目标语句前缀
                t0 = time.perf_counter()
                rr = srv.memory_recall(q, limit=20)
                lat.append((time.perf_counter() - t0) * 1000)
                queries += 1
                if any(target in it.statement for it in rr.items):
                    hits += 1
            rate = hits / queries
            med_lat = sorted(lat)[len(lat) // 2]
            max_lat = max(lat)
            print(f"\n[合成基线] 查询={queries} 召回={hits} ({rate:.0%}) "
                  f"延迟中位={med_lat:.1f}ms 最大={max_lat:.1f}ms")
            self.assertGreaterEqual(rate, 0.75, f"召回基线未达 75%: {rate:.0%}")
            # 延迟断言（CI runner 性能不可控：median 管回归，max 宽容防 flaky；
            # 严格的 <500ms 预算在本地开发机复核，见 docs/01 §10）
            self.assertLess(med_lat, 500, f"FTS 中位延迟超预算: {med_lat:.0f}ms")
            self.assertLess(max_lat, 2000, f"FTS 最大延迟异常: {max_lat:.0f}ms")
        finally:
            be.close()


if __name__ == "__main__":
    unittest.main()
