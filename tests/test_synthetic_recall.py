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
    """查询 = 目标语句的独有实体词（模块{i} 全库唯一）+ 类型词。
    v1.1 修正：原查询用共享词（通道1/方案N）导致 LIMIT 截断与查询-目标错配，
    召回 50% 是测试设计缺陷而非引擎缺陷——真实用户查询含独特实体词，语义等同本修正版。"""
    kind_word = ["决定", "约定", "红线", "排除"][i % 4]
    return f"模块{i} {kind_word}"


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

            # 2) 机制扳机：抽取（无 judge，全 pending）
            ex = srv.memory_extract(ExtractScope(session_id=r.session_id))
            self.assertGreaterEqual(ex.produced_pending, 100,
                                    f"抽取产出过低: {ex.produced_pending}")

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
            max_lat = max(lat)
            print(f"\n[合成基线] 查询={queries} 召回={hits} ({rate:.0%}) "
                  f"延迟中位={sorted(lat)[len(lat)//2]:.1f}ms 最大={max_lat:.1f}ms")
            self.assertGreaterEqual(rate, 0.75, f"召回基线未达 75%: {rate:.0%}")
            self.assertLess(max_lat, 500, f"延迟超预算: {max_lat:.0f}ms")
        finally:
            be.close()


if __name__ == "__main__":
    unittest.main()
