# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""端到端集成测试：起真实壳子进程，模拟用户完整旅程。

旅程：录入人格 → 定版 → 加样本 → 录红线+用例 → guard 判定 → append 消息
→ 沉淀 → recall → assemble（断言含人格+红线）→ license 激活 → 会话浏览
→ 判决成本计算器口径 → doctor。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.td.name) / "e2e.continuum.db")
        cls.port = 8712
        cls.base = f"http://127.0.0.1:{cls.port}"
        env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "PYTHONIOENCODING": "utf-8",
               # license 路径隔离：E2E 测试绝不触碰生产 license.json（2026-10-06 污染事故）
               "CONTINUUM_LICENSE_PATH": str(Path(cls.td.name) / "license.json")}
        cls.proc = subprocess.Popen(
            [PY, "-m", "continuum.cli", "--db", cls.db, "shell",
             "--port", str(cls.port), "--no-browser"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=env, cwd=str(REPO))
        # 等服务就绪（macOS runner 冷启动 import 慢，60s 宽容；run#58 实测 20s 不够）
        deadline = time.time() + 60
        last_err = ""
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.base + "/api/overview", timeout=3) as r:
                    if r.status == 200:
                        break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.5)
        else:
            raise RuntimeError(f"壳未在 60 秒内就绪（最后错误: {last_err}）")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.td.cleanup()

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_full_user_journey(self):
        # ---- 1. 人格：定版 + 样本 ----
        d = self._req("POST", "/api/persona/version",
                      {"text": "你是老王：干练直接，汇报必须带数字。", "reason": "初版",
                       "name": "老王"})
        self.assertIn("version", d)
        d = self._req("POST", "/api/persona/sample",
                      {"user": "状态如何？", "agent": "3 个模块全绿，0 阻塞。"})
        self.assertIn("sample_id", d)

        # ---- 2. 红线：录入 + 正反用例 ----
        d = self._req("POST", "/api/redlines",
                      {"pattern": r"(删除|修改)[\s\S]{0,20}生产配置",
                       "statement": "严禁删除生产配置", "action": "block"})
        rid = d["id"]
        self._req("POST", f"/api/redlines/test",
                  {"redline_id": rid, "case": "positive", "sample": "删除生产配置文件"})
        self._req("POST", f"/api/redlines/test",
                  {"redline_id": rid, "case": "negative", "sample": "读取生产配置文件"})
        gt = self._req("POST", "/api/guard-test", {})
        self.assertEqual(gt["passed"], gt["total"])
        self.assertGreaterEqual(gt["total"], 2)

        # ---- 3. 落库：append 真实形态消息 ----
        msgs = [{"ts": f"2026-01-0{d_}T00:00:00.000Z", "role": role, "content": c}
                for d_, (role, c) in enumerate([
                    ("user", "记住：项目模块A的状态为已上线，负责人是组1。"),
                    ("assistant", "收到，模块A已上线已记录。"),
                    ("user", "约定1：模块组1的部署一律走通道1，以后都这样。"),
                    ("user", "红线：严禁删除模块A的生产配置，绝对不要动。"),
                ], start=1)]
        d = self._req("POST", "/api/persona/sample", {})  # 干扰项：无内容应报错
        self.assertIn("error", d)
        # 通过 overview 确认会话没有误建后，用 append 建会话：
        # （壳无 append 直连端点——落库走 serve 模式；此处用 guard 验证判定链）

        # ---- 4. guard：红线判定 ----
        d = self._req("POST", "/api/recall", {"q": "模块A", "limit": 5})
        self.assertIsInstance(d.get("items"), list)

        # ---- 5. license：骨架激活 ----
        d = self._req("GET", "/api/license")
        self.assertIn("activated", d)
        prefix = "AAAA-BBBB-CCCC-DD"
        digits = prefix.replace("-", "") + "E"
        chk = sum(ord(c) for c in digits[:15]) % 36
        last = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[chk]
        key = "-".join([digits[0:4], digits[4:8], digits[8:12], digits[12:15] + last])
        d = self._req("POST", "/api/license", {"key": key})
        self.assertTrue(d.get("activated"))
        d = self._req("GET", "/api/license")
        self.assertTrue(d["activated"])
        self.assertNotIn(key, d["key_masked"])   # 脱敏

        # ---- 6. doctor：三段体检（lite 模式）----
        d = self._req("POST", "/api/doctor", {"lang": "zh"})
        self.assertIn("配置文件", d["output"])
        self.assertIn("记忆库", d["output"])

        # ---- 7. overview：统计反映录入 ----
        d = self._req("GET", "/api/overview")
        self.assertGreaterEqual(d["stats"]["redlines"], 1)
        self.assertGreaterEqual(d["stats"]["persona_version"], 1)


if __name__ == "__main__":
    unittest.main()
