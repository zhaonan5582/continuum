# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""`continuum setup` 一键安装测试（全部用临时 home —— 绝不触碰真实配置）。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from continuum.setup import run_setup  # noqa: E402


class TestSetup(unittest.TestCase):
    def _home(self, td: Path) -> Path:
        (td / ".workbuddy").mkdir(parents=True)
        (td / ".codex").mkdir()
        return td

    def test_creates_entry_preserves_existing_and_backs_up(self):
        with tempfile.TemporaryDirectory() as t:
            home = self._home(Path(t))
            cfg = home / ".workbuddy" / "mcp.json"
            cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}},
                                       "customTopKey": 1}), encoding="utf-8")
            actions, _ = run_setup("C:\\db.db", home=home)
            got = {a.host: a for a in actions}
            self.assertEqual(got["workbuddy"].status, "updated")
            data = json.loads(cfg.read_text(encoding="utf-8"))
            self.assertIn("continuum", data["mcpServers"])
            self.assertIn("other", data["mcpServers"], "既有 server 被破坏")
            self.assertEqual(data["customTopKey"], 1, "既有顶层键被破坏")
            baks = list(cfg.parent.glob("mcp.json.bak-continuum-*"))
            self.assertTrue(baks, "写前必须备份")

    def test_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            home = self._home(Path(t))
            run_setup("C:\\db.db", home=home)
            actions, _ = run_setup("C:\\db.db", home=home)
            wb = next(a for a in actions if a.host == "workbuddy")
            self.assertEqual(wb.status, "exists")

    def test_toml_append_and_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            home = self._home(Path(t))
            toml = home / ".codex" / "config.toml"
            toml.write_text("model = \"gpt-5\"\n", encoding="utf-8")
            actions, _ = run_setup("C:\\db.db", home=home)
            cx = next(a for a in actions if a.host == "codex")
            self.assertEqual(cx.status, "updated")
            text = toml.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.continuum]", text)
            self.assertIn('model = "gpt-5"', text, "既有内容被破坏")
            # 幂等
            actions2, _ = run_setup("C:\\db.db", home=home)
            self.assertEqual(next(a for a in actions2 if a.host == "codex").status, "exists")
            self.assertEqual(toml.read_text(encoding="utf-8").count("[mcp_servers.continuum]"), 1)

    def test_mcp_servers_empty_array_converted(self):
        """CC 的 .claude.json 实测 mcpServers 为空数组 → 需转 object。"""
        with tempfile.TemporaryDirectory() as t:
            home = self._home(Path(t))
            (home / ".claude").mkdir()
            cc = home / ".claude.json"
            cc.write_text(json.dumps({"numStartups": 3, "mcpServers": []}), encoding="utf-8")
            run_setup("C:\\db.db", home=home)
            data = json.loads(cc.read_text(encoding="utf-8"))
            self.assertIsInstance(data["mcpServers"], dict)
            self.assertIn("continuum", data["mcpServers"])
            self.assertEqual(data["numStartups"], 3)

    def test_empty_home_no_actions(self):
        with tempfile.TemporaryDirectory() as t:
            actions, notes = run_setup("C:\\db.db", home=Path(t))
            self.assertEqual(actions, [])
            self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
