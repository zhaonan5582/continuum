# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""软件壳（本机回环 UI）API 测试：起真实 HTTP 服务、打真实请求。"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from continuum.server import ContinuumServer  # noqa: E402
from continuum.shell.server import make_server  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402

MIGRATIONS = REPO / "src" / "continuum" / "storage" / "migrations" / "sql"


class TestShellAPI(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        self.httpd = make_server(self.srv, ":memory:", host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        t.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.be.close()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post(self, path, body):
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_index_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            html = r.read().decode("utf-8")
        self.assertIn("Continuum 软件壳", html)

    def test_i18n_js_served(self):
        """语言包必须可达（404 = 页面 I18N 未定义整页崩）。"""
        with urllib.request.urlopen(self.base + "/i18n.js", timeout=10) as r:
            js = r.read().decode("utf-8")
        self.assertIn('"zh"', js)
        self.assertIn('"ar"', js)
        # 路径穿越防护：只允许精确 /i18n.js
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + "/../i18n.js", timeout=10)

    def test_overview(self):
        d = self._get("/api/overview")
        self.assertIn("stats", d)
        self.assertEqual(d["stats"]["sessions"], 0)

    def test_persona_flow(self):
        d = self._post("/api/persona/version",
                       {"text": "你是老王：干练直接。", "reason": "初版"})
        self.assertEqual(d["version"], 1)
        d = self._post("/api/persona/sample",
                       {"user": "状态如何？", "agent": "全绿，0 阻塞。"})
        self.assertEqual(d["sample_id"], 1)
        d = self._get("/api/persona")
        self.assertIn("老王", d["current"]["snapshot_md"])
        self.assertEqual(len(d["samples"]), 1)

    def test_redline_flow_and_guard(self):
        d = self._post("/api/redlines", {
            "pattern": r"(删除|修改)[\s\S]{0,20}参照站",
            "statement": "参照站绝对只读", "action": "block"})
        rid = d["id"]
        self._post("/api/redlines/test",
                   {"redline_id": rid, "case": "positive", "sample": "删除参照站样本"})
        self._post("/api/redlines/test",
                   {"redline_id": rid, "case": "negative", "sample": "查询参照站列表"})
        d = self._get("/api/redlines")
        self.assertEqual(len(d["redlines"]), 1)
        self.assertEqual(d["redlines"][0]["cases"], 2)
        gt = self._post("/api/guard-test", {})
        self.assertEqual(gt["total"], 2)
        self.assertEqual(gt["passed"], 2)

    def test_recall_empty_and_hit(self):
        d = self._post("/api/recall", {"q": ""})
        self.assertEqual(d["items"], [])
        self.srv.be.conn.execute(
            "INSERT INTO sessions(host_agent, external_id, project_id, started_at)"
            " VALUES('workbuddy','s1','p','2026-01-01T00:00:00.000Z')")
        sid = self.be.conn.execute("SELECT id FROM sessions").fetchone()["id"]
        from continuum.udf import UDFMessage, UDFMeta
        self.srv.be.append_messages(sid, [UDFMessage(
            ts="2026-01-01T00:00:01.000Z", role="user", host="workbuddy",
            session_id="s1", content="记住：参照站绝对只读，这是红线。",
            meta=UDFMeta())])
        self._post("/api/extract", {"confirm": True})
        d = self._post("/api/recall", {"q": "参照站"})
        self.assertGreaterEqual(len(d["items"]), 1)

    def test_persona_named_and_activate(self):
        """多套命名人格：新建套自动激活；activate 全局唯一切换；装配读激活套。"""
        self._post("/api/persona/version",
                   {"text": "你是老王。", "reason": "初版", "name": "老王"})
        self._post("/api/persona/sample",
                   {"user": "状态？", "agent": "全绿。", "name_ignored": 1})
        pkg = self.srv.memory_assemble(None)
        self.assertIn("老王", pkg.persona_md)
        self._post("/api/persona/version",
                   {"text": "你是小陈：先列计划。", "reason": "切换", "name": "小陈"})
        pkg = self.srv.memory_assemble(None)
        self.assertIn("小陈", pkg.persona_md)          # 新套自动激活
        d = self._get("/api/persona")
        names = {n["name"] for n in d["named"]}
        self.assertEqual(names, {"老王", "小陈"})
        self.assertEqual(d["current"]["name"], "小陈")
        # 切回老王
        self._post("/api/persona/activate", {"name": "老王"})
        pkg = self.srv.memory_assemble(None)
        self.assertIn("老王", pkg.persona_md)
        self.assertNotIn("小陈", pkg.persona_md)
        # 样本随套走
        d = self._get("/api/persona")
        self.assertEqual(d["current"]["name"], "老王")

    def test_session_browser_pagination(self):
        """会话浏览器：分页 + 内容返回。"""
        self.srv.be.conn.execute(
            "INSERT INTO sessions(host_agent, external_id, project_id, started_at)"
            " VALUES('workbuddy','br','p','2026-01-01T00:00:00.000Z')")
        sid = self.be.conn.execute("SELECT id FROM sessions").fetchone()["id"]
        from continuum.udf import UDFMessage, UDFMeta
        msgs = [UDFMessage(ts=f"2026-01-01T00:00:{i:02d}.000Z", role="user",
                           host="workbuddy", session_id="br", content=f"消息{i}",
                           meta=UDFMeta()) for i in range(1, 8)]
        self.srv.be.append_messages(sid, msgs)
        d = self._get("/api/session/%d/messages?offset=0&limit=3" % sid)
        self.assertEqual(d["total"], 7)
        self.assertEqual(len(d["messages"]), 3)
        d2 = self._get("/api/session/%d/messages?offset=6&limit=3" % sid)
        self.assertEqual(len(d2["messages"]), 1)

    def test_license_skeleton(self):
        """授权骨架：坏格式拒绝；合法校验和激活并持久化。"""
        bad = self._post("/api/license", {"key": "BAD"})
        self.assertIn("error", bad)
        # 构造校验和合法的 key：前 15 位任意 A-Z0-9，末位=前 15 位 ord 和 % 36
        import string
        prefix = "AAAA-BBBB-CCCC-DD"
        digits = prefix.replace("-", "") + "E"   # 16 位占位
        chk = sum(ord(c) for c in digits[:15]) % 36
        last = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[chk]
        key = "-".join([digits[0:4], digits[4:8], digits[8:12], digits[12:15] + last])
        d = self._post("/api/license", {"key": key})
        self.assertTrue(d.get("activated"))
        st = self._get("/api/license")
        self.assertTrue(st["activated"])
        self.assertNotIn(key, st["key_masked"])   # 脱敏显示

    def test_persona_version_defaults_to_active_set(self):
        """定版 name 缺省 = 当前激活套（修 A 套落 default = 版本链断裂，审查实锤）。"""
        self._post("/api/persona/version",
                   {"text": "你是老王。", "reason": "初版", "name": "老王"})
        self._post("/api/persona/version",
                   {"text": "你是小陈。", "reason": "二版", "name": "小陈"})
        # 激活的是小陈（后建自动激活）；name 缺省定版必须落进小陈套
        d = self._post("/api/persona/version", {"text": "你是小陈 v2。", "reason": "修订"})
        self.assertEqual(d["name"], "小陈")
        d2 = self._get("/api/persona")
        self.assertEqual(d2["current"]["name"], "小陈")
        self.assertIn("v2", d2["current"]["snapshot_md"])

    def test_doctor_lang_flows_through_http(self):
        """体检输出语言随请求参数（2026-10-06 通读实锤：_body 二次读取吃空 body →
        lang 丢失 → 英文界面出中文）。"""
        d = self._post("/api/doctor", {"lang": "en"})
        self.assertIn("Config files", d["output"])
        d = self._post("/api/doctor", {"lang": "zh"})
        self.assertIn("配置文件", d["output"])

    def test_workload_api(self):
        """工作节奏 API：近 7 天窗口 + 今日 + 健康提示（key 由前端翻译）。"""
        d = self._get("/api/workload?days=7")
        self.assertIn("today", d)
        self.assertEqual(len(d["window"]), 7)
        self.assertTrue(d["insights"])
        for i in d["insights"]:
            self.assertIn("key", i)
            self.assertTrue(i["key"].startswith("wk_"))
        self.assertEqual(len(d["window"][-1]["hourly"]), 24)

    def test_toggle_redline(self):
        d = self._post("/api/redlines",
                       {"pattern": "legacy", "statement": "旧红线", "action": "block"})
        d = self._post("/api/redlines/toggle", {"id": d["id"]})
        self.assertFalse(d["enabled"])


if __name__ == "__main__":
    unittest.main()
