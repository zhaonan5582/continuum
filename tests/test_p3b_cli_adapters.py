# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""P3-b 验收：CLI persona 命令族 + Claude Code 适配器（to_udf/hooks/CLAUDE.md 片段）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.adapters import (  # noqa: E402
    ClaudeCodeHookAdapter,
    ClaudeCodeSessionAdapter,
)
from continuum.cli import build_parser, main  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


class TestPersonaCLI(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = str(Path(self.td.name) / "test.continuum.db")

    def tearDown(self):
        self.td.cleanup()

    def _run(self, argv: list[str]) -> int:
        return main(["--db", self.db, *argv])

    def test_version_add_list_roundtrip(self):
        self.assertEqual(self._run(["persona", "version", "--text", "不废话 直接干",
                                    "--reason", "初版"]), 0)
        self.assertEqual(self._run(["persona", "add",
                                    "--user", "这事怎么办",
                                    "--agent", "直接干别问",
                                    "--tag", "示范"]), 0)
        # list 输出包含样本（捕获 stdout 验证）
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            self._run(["persona", "list"])
        out = buf.getvalue()
        self.assertIn("v1", out)
        self.assertIn("直接干别问", out)

    def test_current_without_persona_fails_gracefully(self):
        self.assertEqual(self._run(["persona", "current"]), 1)


class TestClaudeCodeAdapter(unittest.TestCase):
    def setUp(self):
        self.sa = ClaudeCodeSessionAdapter()
        self.ha = ClaudeCodeHookAdapter(server=object())

    def test_to_udf_filters_and_converts(self):
        lines = [
            json.dumps({"type": "message", "role": "user", "sessionId": "cc-1",
                        "timestamp": "2026-10-05T10:00:00.000Z",
                        "content": [{"type": "input_text", "text": "决定采用方案A。"}]}),
            json.dumps({"type": "file-history-snapshot", "id": "s1", "timestamp": 1}),
            json.dumps({"type": "message", "role": "assistant", "sessionId": "cc-1",
                        "timestamp": "2026-10-05T10:00:05.000Z",
                        "content": [{"type": "output_text", "text": "好的。"},
                                    {"type": "output_text", "text": "已记录。"}]}),
        ]
        out = self.sa.to_udf(lines)
        self.assertEqual(len(out), 2)                     # snapshot 被滤
        self.assertEqual(out[0].host, "claude-code")
        self.assertIn("已记录。", out[1].content)          # 多块拼接

    def test_to_udf_bad_line_skipped(self):
        out = self.sa.to_udf(["not json", json.dumps({"type": "message", "role": "user",
                                 "sessionId": "s", "timestamp": "2026-10-05T10:00:00.000Z",
                                 "content": "ok"})])
        self.assertEqual(len(out), 1)

    def test_hooks_config_template(self):
        cfg = self.ha.hooks_config_template()
        hooks = cfg["hooks"]
        self.assertIn("SessionEnd", hooks)               # 沉淀扳机
        self.assertIn("UserPromptSubmit", hooks)         # 提醒注入
        self.assertIn("PreToolUse", hooks)               # 门禁
        self.assertIn("continuum serve", json.dumps(hooks))

    def test_claude_md_snippet_markers(self):
        s = self.ha.claude_md_snippet()
        self.assertIn("CONTINUUM BEGIN", s)
        self.assertIn("memory_recall", s)
        self.assertIn("memory_guard", s)


class TestGitattributesShellLF(unittest.TestCase):
    def test_sh_files_lf(self):
        """跨平台纪律：.sh 必须 LF（shebang 兼容）。"""
        ga = (Path(__file__).resolve().parents[1] / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", ga)


if __name__ == "__main__":
    unittest.main()
