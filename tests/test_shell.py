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

    def test_toggle_redline(self):
        d = self._post("/api/redlines",
                       {"pattern": "legacy", "statement": "旧红线", "action": "block"})
        d = self._post("/api/redlines/toggle", {"id": d["id"]})
        self.assertFalse(d["enabled"])


if __name__ == "__main__":
    unittest.main()
