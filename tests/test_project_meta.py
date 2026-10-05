# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""项目元数据一致性 + MCP 装配骨架测试。"""
from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from continuum.server.mcp import MCPNotInstalled, build_mcp_server  # noqa: E402
from continuum.server.tools import ContinuumServer  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.version import VERSION  # noqa: E402

MIGRATIONS = ROOT / "src" / "continuum" / "storage" / "migrations" / "sql"


class TestProjectMeta(unittest.TestCase):
    """机械交叉检索的固化为测试：版本单源 + 构建裁剪配置存在。"""

    def load_pyproject(self) -> dict:
        with open(ROOT / "pyproject.toml", "rb") as f:
            return tomllib.load(f)

    def test_version_single_source(self):
        data = self.load_pyproject()
        self.assertEqual(data["project"]["version"], VERSION, "pyproject 与 version.py 版本漂移")

    def test_license_declared_agpl(self):
        data = self.load_pyproject()
        self.assertEqual(data["project"]["license"], "AGPL-3.0-only")

    def test_core_has_zero_runtime_deps(self):
        data = self.load_pyproject()
        self.assertEqual(data["project"]["dependencies"], [], "核心包必须零第三方运行时依赖")

    def test_build_exclude_pro(self):
        data = self.load_pyproject()
        find = data["tool"]["setuptools"]["packages"]["find"]
        excludes = find.get("exclude", [])
        self.assertTrue(any("pro" in e for e in excludes), "构建裁剪缺 pro 排除配置")

    def test_udf_schema_json_valid_and_frozen(self):
        import json
        p = ROOT / "src" / "continuum" / "udf" / "udf.schema.json"
        schema = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["udf_version"]["const"], 1)
        self.assertFalse(schema["additionalProperties"], "冻结契约必须拒绝未知字段")
        self.assertEqual(schema["required"], ["ts", "role", "host", "session_id", "content"])


class TestMCPAssembly(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)

    def tearDown(self):
        self.be.close()

    def test_module_import_does_not_require_mcp(self):
        """延迟导入验证：本模块可 import（无需 mcp 已安装）。"""
        import continuum.server.mcp as m  # noqa: F401

    def test_build_without_mcp_raises_with_guidance(self):
        """未安装 mcp 时：明确报错 + 携带安装指引（诚实接口）。"""
        try:
            import mcp  # noqa: F401
            self.skipTest("本环境已安装 mcp，无法测试未安装路径")
        except ImportError:
            pass
        with self.assertRaises(MCPNotInstalled) as cm:
            build_mcp_server(self.srv)
        self.assertIn("pip install", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
