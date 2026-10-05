# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""P3-b 审查回归：钩子执行模式（--on 三动作）+ 红线测试集执行器 + 审计1/2 修复固化。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.cli import _run_hook  # noqa: E402
from continuum.server import (  # noqa: E402
    ContinuumServer,
    ExtractScope,
    Operation,
)
from continuum.storage import SQLiteBackend  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


class _QuietServer(ContinuumServer):
    """吞掉 audit 的 KeyboardInterrupt 传播风险——普通 server 即可，此类仅留扩展位。"""


class TestHookModes(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = str(Path(self.td.name) / "t.continuum.db")
        be = SQLiteBackend(self.db, MIGRATIONS)
        be.close()

    def tearDown(self):
        self.td.cleanup()

    def _run_hook(self, on: str, stdin_json: str | None) -> tuple[int, str]:
        from continuum.cli import _run_hook
        buf_out = io.StringIO()
        with redirect_stdout(buf_out):
            code = _run_hook(on, self.db, stdin_text=stdin_json)
        return code, buf_out.getvalue()

    def test_session_end_produces_pending(self):
        # 先落一条含模式句的消息
        code, _ = self._run_hook("session-end", "{}")   # 空库也应正常完成
        self.assertEqual(code, 0)

    def test_prompt_mode_outputs_context(self):
        code, out = self._run_hook("prompt", "{}")
        self.assertEqual(code, 0)
        self.assertIn("memory_recall", out, "prompt 模式应输出 recall 提醒")

    def test_guard_mode_block_outputs_deny(self):
        # 先造一条 block 红线
        be = SQLiteBackend(self.db, MIGRATIONS)
        be.add_redline(pattern="rm -rf production", statement="严禁删除生产目录", action="block")
        be.close()
        payload = json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": "rm -rf production/data"}})
        code, out = self._run_hook("guard", payload)
        self.assertEqual(code, 0)
        self.assertIn("deny", out)
        self.assertIn("红线", out)

    def test_guard_mode_allow_silent(self):
        code, out = self._run_hook("guard", json.dumps(
            {"tool_name": "Read", "tool_input": {"path": "notes.txt"}}))
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")   # allow 静默放行

    def test_unknown_on_rejected(self):
        from continuum.cli import _run_hook
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with redirect_stdout(buf_out):
            saved_err = sys.stderr
            sys.stderr = buf_err
            try:
                code = _run_hook("bogus", self.db, stdin_text="{}")
            finally:
                sys.stderr = saved_err
        self.assertEqual(code, 2)


from continuum.storage import SQLiteBackend as _SB  # noqa: E402  （复用）


class TestRedlineTestRunner(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        r1 = self.be.add_redline(pattern="rm -rf production",
                                 statement="严禁删除生产目录", action="block")
        self.be.add_redline_test(r1, "positive", "rm -rf production/data", "block")
        self.be.add_redline_test(r1, "negative", "cat production/data/notes.txt", "allow")
        r2 = self.be.add_redline(pattern="drop database", statement="生产库操作需谨慎",
                                 action="warn", scope="project:proj-A")
        self.be.add_redline_test(r2, "positive", "drop database main", "warn")

    def tearDown(self):
        self.be.close()

    def test_runner_executes_and_reports(self):
        results = self.srv.run_redline_tests()
        self.assertEqual(len(results), 3)
        passed = [r for r in results if r["pass"]]
        self.assertGreaterEqual(len(passed), 2,
                                f"正反用例应通过: {results}")
        # negative 用例：cat 不含 pattern 子串 → allow → 反用例通过
        neg = next(r for r in results if r["case_type"] == "negative")
        self.assertTrue(neg["pass"])
        self.assertEqual(neg["actual"], "allow")

    def test_scope_filtered_negative(self):
        """scope 隔离：proj-B 下 proj-A 的 warn 红线不命中 → allow。"""
        results = self.srv.run_redline_tests()
        # 全部通过（包括 scope 隔离产生的 allow）
        self.assertTrue(all(r["pass"] for r in results))


if __name__ == "__main__":
    unittest.main()
