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

    def test_js_referenced_ids_all_defined(self):
        """JS 引用的每个 #id 必须在 HTML 中定义（改版后 id 失配 = 页面静默坏）。"""
        used = set(re.findall(r'\$\("#([\w-]+)"\)', self.js))
        defined = set(re.findall(r'id="([\w-]+)"', self.html))
        missing = sorted(used - defined)
        self.assertEqual(missing, [], f"JS 引用了未定义的 DOM id: {missing}")

    def test_i18n_keys_symmetric(self):
        """zh/en 语言包键集合必须对称（单边缺键 = 另一语言显示回退中文）。"""
        zh = re.search(r"zh: \{([\s\S]*?)\n  en: \{", self.src).group(1)
        en = re.search(r"en: \{([\s\S]*?)\n\};", self.src).group(1)
        # 键格式为带引号的 "key":（收紧，避免值文本里的 word: 误报）
        key_re = re.compile(r'["\']([a-z_]+)["\']\s*:')
        zh_keys = set(key_re.findall(zh))
        en_keys = set(key_re.findall(en))
        self.assertEqual(zh_keys, en_keys,
                         f"语言包键不对称: zh 有 en 无={sorted(zh_keys-en_keys)}, "
                         f"en 有 zh 无={sorted(en_keys-zh_keys)}")

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
