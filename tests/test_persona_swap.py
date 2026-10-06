# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""换人测试（P2 版核心验收，docs/01 §9）：

构造长会话 A（≥100 事实 + 10 约定 + 5 红线）→ 机制扳机沉淀 + 确认 →
模拟压缩（会话 B 冷启动，只装配不读历史）→ 考核：
(a) 事实召回 ≥90%（recall 结构化主力）
(b) 约定复述 100%（装配包 L1）
(c) 红线知识 100%（装配包 L1；guard 拦截属 P3）
(d) 装配包 ≤8K token
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Windows CI runner 编码容错

from continuum.server import (  # noqa: E402
    AppendRequest,
    ContinuumServer,
    ExtractScope,
)
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"

N_FACT = 120
N_CONV = 10
N_RED = 5
BASE = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)


def _ts(i: int) -> str:
    return (BASE + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TestPersonaSwap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """会话 A：长任务 + 机制扳机沉淀 + 确认（模拟 judge/人工确认后的正式状态）。"""
        cls.be = SQLiteBackend(":memory:", MIGRATIONS)
        cls.srv = ContinuumServer(cls.be)
        msgs = []
        i = 0
        # 120 条事实
        for k in range(N_FACT):
            msgs.append(UDFMessage(ts=_ts(i), role="user", host="workbuddy", session_id="A",
                                   content=f"记住：项目模块{k}的状态为状态{k % 7}，负责人是组{k % 5}。"))
            i += 1
        # 10 条约定
        for k in range(N_CONV):
            msgs.append(UDFMessage(ts=_ts(i), role="user", host="workbuddy", session_id="A",
                                   content=f"约定{k}：模块组{k}的部署一律走通道{k}，以后都这样。"))
            i += 1
        # 5 条红线
        for k in range(N_RED):
            msgs.append(UDFMessage(ts=_ts(i), role="user", host="workbuddy", session_id="A",
                                   content=f"红线{k}：严禁删除服务{k}的生产配置，绝对不要动。"))
            i += 1
        # 少量噪声（压缩压力）
        for k in range(20):
            msgs.append(UDFMessage(ts=_ts(i), role="assistant", host="workbuddy", session_id="A",
                                   content=f"噪声消息{k}：这是无关的过渡性对话内容。"))
            i += 1
        cls.srv.memory_append(AppendRequest(host_agent="workbuddy", external_session_id="A", messages=tuple(msgs)))
        # 机制扳机沉淀 + 确认（模拟 P2 judge 高置信通过）
        cls.srv.memory_extract(ExtractScope(session_id=None, confirm=True))

    @classmethod
    def tearDownClass(cls):
        cls.be.close()

    def test_a_fact_recall_90(self):
        """(a) 会话 B（新会话）装配后，事实召回 ≥90%。"""
        hits = 0
        queries = 0
        lat = []
        for k in range(0, N_FACT, 1)[:100]:          # 抽 100 个事实
            t0 = time.perf_counter()
            r = self.srv.memory_recall(f"模块{k}", limit=20)
            lat.append((time.perf_counter() - t0) * 1000)
            queries += 1
            if any(f"模块{k}的状态为状态{k % 7}" in it.statement for it in r.items):
                hits += 1
        rate = hits / queries
        print(f"\n[换人测试-a] 事实召回 {hits}/{queries} = {rate:.0%}，"
              f"延迟中位 {sorted(lat)[len(lat)//2]:.1f}ms")
        self.assertGreaterEqual(rate, 0.90, f"事实召回未达 90%: {rate:.0%}")

    def test_b_conventions_fully_in_package(self):
        """(b) 10 条约定 100% 出现在装配包 L1。"""
        pkg = self.srv.memory_assemble(project_id=None)
        for k in range(N_CONV):
            self.assertIn(f"模块组{k}的部署一律走通道{k}", pkg.snapshot_md,
                          f"约定{k} 丢失于装配包")
        self.assertIn("约定", pkg.snapshot_md)

    def test_c_redlines_fully_in_package(self):
        """(c) 5 条红线 100% 出现在装配包（红线知识不丢；guard 拦截属 P3）。"""
        pkg = self.srv.memory_assemble(project_id=None)
        for k in range(N_RED):
            self.assertIn(f"严禁删除服务{k}的生产配置", pkg.snapshot_md,
                          f"红线{k} 丢失于装配包")

    def test_d_assemble_budget(self):
        """(d) 装配包 ≤8K token（超预算必须有 warning）。"""
        pkg = self.srv.memory_assemble(project_id=None)
        self.assertLessEqual(pkg.token_estimate, 8_000,
                             f"装配包超预算: {pkg.token_estimate}")
        if pkg.token_estimate > 7_000:
            self.assertTrue(pkg.warnings, "接近预算应有提示")


import time  # noqa: E402  （置底供 test_a 使用）


class TestPersonaBlindSwap(unittest.TestCase):
    """P3 验收①：同底座人格盲测——版本 A→B 切换，装配包必须跟着换
    （状态块 + few-shot 样本都进 persona_md 与 L0，不再有硬编码占位）。"""

    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        v_a = self.be.persona_create(
            "你是老王：干练直接，汇报必须带数字。", change_reason="初版 A")
        self.be.persona_add_sample(
            v_a, user_utterance="状态如何？", agent_response="3 个模块全绿，0 阻塞。")
        v_b = self.be.persona_create(
            "你是小陈：细致温和，先列计划再动手。", change_reason="切换 B", parent_version=v_a)
        self.be.persona_add_sample(
            v_b, user_utterance="状态如何？", agent_response="我先列个清单再答。")

    def tearDown(self):
        self.be.close()

    def test_switch_follows_latest_version(self):
        md = self.srv.persona_current_md()
        self.assertIn("小陈", md)
        self.assertNotIn("老王", md)                      # A 被切换掉
        self.assertIn("我先列个清单再答", md)              # few-shot 跟随当前版本
        self.assertIn("persona v2", md)

    def test_assemble_l0_carries_persona(self):
        pkg = self.srv.memory_assemble(project_id=None)
        self.assertIn("小陈", pkg.snapshot_md)             # L0 不再是硬编码占位
        self.assertIn("细致温和", pkg.snapshot_md)
        self.assertIn("小陈", pkg.persona_md)

    def test_empty_library_placeholder(self):
        """冷库（无人格版本）保持占位——正常态非故障，文案面向用户（非开发黑话）。"""
        be2 = SQLiteBackend(":memory:", MIGRATIONS)
        try:
            md = ContinuumServer(be2).persona_current_md()
            self.assertIn("尚未录入人格", md)
            self.assertIn("continuum persona version", md)
            self.assertNotIn("P3", md)          # 开发期黑话不外泄
        finally:
            be2.close()


if __name__ == "__main__":
    unittest.main()
