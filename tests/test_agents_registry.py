# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""多宿主探测测试（docs/10 第一层：注册表 + 只读探测）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from continuum.agents import build_registry, detect_all  # noqa: E402


class TestAgentRegistry(unittest.TestCase):
    def test_registry_covers_first_batch(self):
        keys = {p.key for p in build_registry()}
        self.assertTrue({"workbuddy", "codex", "claude-code", "hermes"} <= keys,
                        f"首批宿主必须齐全，实际: {keys}")

    def test_detect_empty_home(self):
        with tempfile.TemporaryDirectory() as td:
            rows = detect_all(Path(td))
            self.assertTrue(all(not r.found for r in rows), "空 home 不得探出任何宿主")

    def test_detect_with_fake_hosts(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".workbuddy" / "projects" / "ws1").mkdir(parents=True)
            (home / ".workbuddy" / "projects" / "ws1" / "s1.jsonl").write_text("{}\n", encoding="utf-8")
            (home / ".codex" / "sessions" / "2026").mkdir(parents=True)
            for i in range(3):
                (home / ".codex" / "sessions" / "2026" / f"s{i}.jsonl").write_text("{}\n", encoding="utf-8")
            rows = {r.profile.key: r for r in detect_all(home)}
            self.assertTrue(rows["workbuddy"].found)
            self.assertEqual(rows["workbuddy"].session_count, 1)
            self.assertTrue(rows["codex"].found)
            self.assertEqual(rows["codex"].session_count, 3)
            self.assertFalse(rows["claude-code"].found)
            self.assertFalse(rows["hermes"].found)

    def test_mechanisms_reflect_support(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".claude" / "projects" / "x").mkdir(parents=True)
            (home / ".claude" / "projects" / "x" / "a.jsonl").write_text("{}\n", encoding="utf-8")
            rows = {r.profile.key: r for r in detect_all(home)}
            cc = rows["claude-code"]
            self.assertIn("hooks", cc.mechanisms, "CC 声明支持 hooks")
            self.assertIn("session-file", cc.mechanisms)
            self.assertIn("proxy", cc.mechanisms, "代理机制与宿主无关，恒可用")
            # codex 不声明 hooks
            (home / ".codex").mkdir(exist_ok=True)
            rows = {r.profile.key: r for r in detect_all(home)}
            self.assertNotIn("hooks", rows["codex"].mechanisms)


class TestFullRegistry(unittest.TestCase):
    """全量注册表（35+ 宿主）与安全红线（永读不得碰凭据）。"""

    def test_registry_is_broad(self):
        from continuum.agents import build_registry
        regs = build_registry()
        self.assertGreaterEqual(len(regs), 30, f"主流宿主覆盖不足: {len(regs)}")
        keys = {a.key for a in regs}
        for must in ("claude-code", "codex", "workbuddy", "cursor", "cline", "aider",
                     "continue", "gemini-cli", "opencode", "goose", "hermes", "zed",
                     "qwen-code", "roo-code", "kilo-code", "openhands", "trae",
                     "windsurf", "amp", "crush", "kimi", "kiro", "copilot-cli"):
            self.assertIn(must, keys, f"缺主流宿主: {must}")

    def test_format_kinds_declared(self):
        from continuum.agents import build_registry
        kinds = {a.format_kind for a in build_registry()}
        self.assertTrue({"jsonl", "json", "sqlite"} <= kinds, f"格式分类异常: {kinds}")
        for a in build_registry():
            self.assertIn(a.support_level,
                          ("parser_ready", "profile_only", "planned"))

    def test_never_read_guard(self):
        from continuum.agents import is_never_read
        for bad in ("/x/.claude/.credentials.json", "/x/auth.json",
                    "/x/.continue/.env", "/x/secrets.json", "/x/mcp-auth.json",
                    "/x/config.toml", "/x/state.vscdb".replace("vscdb", "key")):
            self.assertTrue(is_never_read(bad), f"凭据未拦截: {bad}")
        for ok in ("/x/projects/s1.jsonl", "/x/sessions/rollout-1.jsonl",
                   "/x/threads.db", "/x/sessions/a.json"):
            self.assertFalse(is_never_read(ok), f"会话文件被误拦: {ok}")

    def test_all_profiles_declare_never_read(self):
        from continuum.agents import build_registry
        for a in build_registry():
            self.assertTrue(a.never_read, f"{a.key} 未声明安全红线")


if __name__ == "__main__":
    unittest.main()
