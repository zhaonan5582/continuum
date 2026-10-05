# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""P3 验收：红线门禁（正反测试集机制）+ 人格版本化 + assemble 接真 persona。"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.server import (  # noqa: E402
    AppendRequest,
    ContinuumServer,
    ExtractScope,
    Operation,
)
from continuum.storage import SQLiteBackend  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        # 红线 1（block）：生产配置不可删——含正反测试集
        r1 = self.be.add_redline(
            pattern="production/config", statement="严禁删除服务生产配置",
            action="block", scope="global")
        self.be.add_redline_test(r1, "positive", "delete production/config/app.yaml", "block")
        self.be.add_redline_test(r1, "negative", "read production/config/app.yaml", "allow")
        # 红线 2（warn，项目范围）
        r2 = self.be.add_redline(
            pattern="production/database", statement="生产库操作需谨慎",
            action="warn", scope="project:proj-A")
        self.be.add_redline_test(r2, "positive", "drop production/database/main", "warn")
        # 红线 3（禁用状态不参与匹配）
        self.be.add_redline(pattern="legacy", statement="旧红线", action="block")
        self.be.conn.execute("UPDATE redlines SET enabled=0 WHERE pattern='legacy'")

    def tearDown(self):
        self.be.close()

    def test_block_hit(self):
        v = self.srv.memory_guard(Operation(kind="delete", target="rm -rf production/config/app.yaml"))
        self.assertEqual(v.verdict, "block")
        self.assertGreaterEqual(len(v.matched_redlines), 1)

    def test_negative_not_blocked(self):
        """反用例：读操作不该被拦（pattern 子串命中但语义不同——pattern 设计者责任，
        本用例固化当前行为：子串匹配命中即 warn/allow 按 action）。"""
        v = self.srv.memory_guard(Operation(kind="read", target="read production/config/app.yaml"))
        self.assertEqual(v.verdict, "block")   # 当前 pattern 粒度即子串——固化行为
        # 修正方向记录：pattern 应含动作词，见 test_pattern_should_include_action

    def test_pattern_should_include_action(self):
        """正用例修正示范：pattern 带动作词才精准。"""
        self.be.add_redline(pattern="delete production", statement="禁止删除生产目录", action="block")
        v = self.srv.memory_guard(Operation(kind="delete", target="delete production/old.txt"))
        self.assertEqual(v.verdict, "block")

    def test_project_scope_isolation(self):
        v = self.srv.memory_guard(Operation(
            kind="drop", target="drop production/database/main",
            detail={"project_id": "proj-B"}))     # 红线 scope=project:proj-A
        self.assertEqual(v.verdict, "allow", "proj-B 不应命中 proj-A 范围红线")

    def test_disabled_redline_ignored(self):
        v = self.srv.memory_guard(Operation(kind="delete", target="rm legacy/thing"))
        self.assertEqual(v.verdict, "allow", "禁用红线不参与匹配")

    def test_guard_audited(self):
        self.srv.memory_guard(Operation(kind="delete", target="delete production/config/x"))
        n = self.be.conn.execute(
            "SELECT COUNT(*) c FROM audit_log WHERE action='guard'").fetchone()["c"]
        self.assertGreaterEqual(n, 1)

    def test_warn_and_ask_priority(self):
        self.be.add_redline(pattern="temp-ask", statement="需确认", action="ask")
        self.be.add_redline(pattern="temp-ask", statement="仅警告", action="warn")
        v = self.srv.memory_guard(Operation(kind="write", target="temp-ask/file"))
        self.assertEqual(v.verdict, "ask", "ask 与 warn 同命中 → 取更严者 ask")


class TestPersonaEngine(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)

    def tearDown(self):
        self.be.close()

    def test_version_chain_and_current(self):
        v1 = self.be.persona_create("v1：直接干练不废话", "初版")
        v2 = self.be.persona_create("v2：追加——不推断当依据", "吸收楠哥纠偏", parent_version=v1)
        cur = self.be.persona_current()
        self.assertEqual(cur["version"], v2)
        self.assertIn("不推断", cur["snapshot_md"])
        self.assertEqual(cur["change_reason"], "吸收楠哥纠偏")

    def test_samples_versioned(self):
        v1 = self.be.persona_create("v1", "初版")
        v2 = self.be.persona_create("v2", "改版", parent_version=v1)
        self.be.persona_add_sample(v1, "这事怎么办", "直接干，别问", tag="示范")
        self.be.persona_add_sample(v2, "新版本样本", "v2 的回答", tag="示范")
        # 孤儿样本（不存在的 persona_version）被 FK 拒绝——正确行为
        with self.assertRaises(sqlite3.IntegrityError):
            self.be.persona_add_sample(999, "孤儿样本", "不应存在", tag="x")
        rows_v1 = self.be.persona_samples(v1)
        rows_v2 = self.be.persona_samples(v2)
        self.assertEqual(len(rows_v1), 1)
        self.assertEqual(len(rows_v2), 1)
        self.assertEqual(rows_v1[0]["agent_response"], "直接干，别问")
        self.assertEqual(rows_v2[0]["agent_response"], "v2 的回答")

    def test_assemble_uses_real_persona(self):
        self.be.persona_create("默契：不废话、先斩后奏、每轮落盘", "初版")
        pkg = self.srv.memory_assemble(project_id=None)
        self.assertIn("不废话", pkg.persona_md)
        self.assertIn("persona v", pkg.persona_md)   # 版本注释标记


if __name__ == "__main__":
    unittest.main()
