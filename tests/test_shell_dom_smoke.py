# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""壳前端 DOM-stub 冒烟（需 shell 服务运行 + node；均缺失则跳过）。

用法：先起壳 `continuum shell --db ... --no-browser --port 8501`，再跑全量测试。
本测试抓的是"页面 JS 运行时错误 + 关键渲染失败"——API 层与静态层之外的第三层防护。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest
import urllib.request
from pathlib import Path

NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parent / "shell_dom_smoke.js"
SHELL_URL = os.environ.get("SHELL_URL", "http://127.0.0.1:8501")


def _shell_alive() -> bool:
    try:
        with urllib.request.urlopen(SHELL_URL + "/api/overview", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


@unittest.skipUnless(NODE, "需要 node")
@unittest.skipUnless(_shell_alive(), "需要 shell 服务运行在 8501（continuum shell）")
class TestShellDomSmoke(unittest.TestCase):
    def test_page_js_runs_and_renders(self):
        r = subprocess.run([NODE, str(SCRIPT)], capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        self.assertEqual(r.returncode, 0,
                         f"DOM 冒烟失败:\n{r.stdout[-800:]}\n{r.stderr[-400:]}")


if __name__ == "__main__":
    unittest.main()
