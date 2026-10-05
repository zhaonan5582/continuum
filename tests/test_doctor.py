# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""doctor 体检器测试：配置解析三分支 + server 真实握手。"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from continuum import doctor  # noqa: E402
from continuum.doctor import check_server_live  # noqa: E402


class TestCheckConfigFiles(unittest.TestCase):
    def setUp(self):
        self._orig_json = doctor.WORKBUDDY_MCP_JSON
        self._orig_toml = doctor.CODEX_CONFIG_TOML
        # 假文件写临时目录——绝不碰真实 home（CI runner 无 ~/.workbuddy，2026-10-05 run#22 教训）
        self._td = tempfile.TemporaryDirectory()
        self._tmp = Path(self._td.name)

    def tearDown(self):
        doctor.WORKBUDDY_MCP_JSON = self._orig_json
        doctor.CODEX_CONFIG_TOML = self._orig_toml
        self._td.cleanup()

    def _run(self) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            doctor.check_config_files()
        return buf.getvalue()

    def test_valid_entry_detected(self):
        p = self._tmp / "test_valid.json"
        p.write_text(json.dumps({"mcpServers": {"continuum": {
            "command": "python", "args": ["-m", "continuum.cli", "serve"]}}}),
            encoding="utf-8")
        doctor.WORKBUDDY_MCP_JSON = p
        doctor.CODEX_CONFIG_TOML = self._tmp / "nonexistent.toml"
        out = self._run()
        self.assertIn("continuum 条目存在", out)

    def test_missing_entry_reported(self):
        p = self._tmp / "test_empty.json"
        p.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        doctor.WORKBUDDY_MCP_JSON = p
        doctor.CODEX_CONFIG_TOML = self._tmp / "nonexistent.toml"
        out = self._run()
        self.assertIn("无 continuum 条目", out)

    def test_broken_json_reported(self):
        p = self._tmp / "test_broken.json"
        p.write_text("{broken", encoding="utf-8")
        doctor.WORKBUDDY_MCP_JSON = p
        doctor.CODEX_CONFIG_TOML = self._tmp / "nonexistent.toml"
        out = self._run()
        self.assertIn("JSON 解析失败", out)


class TestServerLive(unittest.TestCase):
    def test_real_handshake_with_own_serve(self):
        """用本仓库 serve 命令做真实握手（:memory: db，不落盘）。"""
        serve_cmd = [sys.executable, "-m", "continuum.cli", "serve", "--db", ":memory:"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            ok = check_server_live(serve_cmd, {"PYTHONPATH": str(REPO / "src")})
        self.assertTrue(ok)
        self.assertIn("initialize OK", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
