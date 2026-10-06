# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""壳前端静态一致性测试（无需浏览器）：DOM id 引用完整性 + JS 语法。"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

INDEX = (Path(__file__).resolve().parents[1]
         / "src" / "continuum" / "shell" / "static" / "index.html")


class TestShellStatic(unittest.TestCase):
    def setUp(self):
        self.src = INDEX.read_text(encoding="utf-8")
        self.js = re.search(r"<script>([\s\S]*)</script>", self.src).group(1)
        self.html = self.src[: self.src.index("<script>")]

    def test_hide_class_defined(self):
        """.hide 功能类必须在 CSS 中定义（2026-10-06 实测：改版丢规则导致
        会话浏览器「收起/搜索」全部失效——JS 加类无样式可匹配）。"""
        self.assertIn(".hide", self.src)
        self.assertRegex(self.src, r"\.hide\s*\{[^}]*display:\s*none")

    def test_js_referenced_ids_all_defined(self):
        """JS 引用的每个 #id 必须在 HTML 中定义（改版后 id 失配 = 页面静默坏）。"""
        used = set(re.findall(r'\$\("#([\w-]+)"\)', self.js))
        defined = set(re.findall(r'id="([\w-]+)"', self.html))
        missing = sorted(used - defined)
        self.assertEqual(missing, [], f"JS 引用了未定义的 DOM id: {missing}")

    def test_i18n_16_packs_symmetric(self):
        """16 语言包（i18n.js）键集合必须全部对称——单边缺键 = 该语言显示回退。"""
        i18n_path = INDEX.parent / "i18n.js"
        src = i18n_path.read_text(encoding="utf-8")
        packs = re.findall(r'"([\w-]+)":\s*\{', src)
        self.assertGreaterEqual(len(packs), 16, f"语言包不足 16 种: {packs}")
        # 逐包提取（textual 切分："lang": { ... },）
        sections = re.split(r'"[\w-]+":\s*\{', src)[1:]
        key_sets = {lang: set(re.findall(r'["\']([a-z_]+)["\']\s*:', body))
                    for lang, body in zip(packs, sections)}
        en_keys = key_sets.get("en", set())
        asym = {l: sorted(key_sets[l] ^ en_keys) for l in packs
                if key_sets[l] != en_keys}
        self.assertEqual(asym, {}, f"语言包键不对称: {asym}")

    def test_html_references_i18n_js(self):
        """index.html 必须引用 i18n.js（语言包外置后不得遗漏）。"""
        self.assertIn('<script src="i18n.js"></script>', self.src)

    def test_js_syntax_if_node_available(self):
        """JS 语法整体验证（node 可用时；CI/开发机均有，纯 Python 环境跳过）。"""
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用，跳过 JS 语法验证")
        r = subprocess.run([node, "-e",
                            f"new Function({self.js!r}); console.log('ok')"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, f"JS 语法错误:\n{r.stderr[:500]}")


if __name__ == "__main__":
    unittest.main()
