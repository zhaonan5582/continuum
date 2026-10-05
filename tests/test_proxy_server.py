# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""代理本体测试：HTTP server 启停 + 端到端注入 + 红线拦截 + token 计量。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.proxy.server import run_proxy  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"
PORT = 18402   # 测试专用端口（避开常用端口）

# 绕过系统/沙盒代理：localhost 请求必须直连
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)


class TestProxyServer(unittest.TestCase):
    """代理 server 端到端测试：真实 HTTP 请求 → 注入 → 转发（mock LLM）→ 返回。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = str(Path(self.td.name) / "test.continuum.db")
        # 后台线程启动代理
        self.thread = threading.Thread(
            target=run_proxy,
            kwargs={"host": "127.0.0.1", "port": PORT, "db_path": self.db,
                    "migrations_dir": str(MIGRATIONS),
                    "target_url": "https://api.deepseek.com"},
            daemon=True,
        )
        self.thread.start()
        import time
        time.sleep(0.3)   # 等 server 启动

    def tearDown(self):
        # 先关 HTTP server（释放 db 句柄）再清 temp
        if hasattr(self, "_server") and self._server:
            self._server.shutdown()
        self.td.cleanup()

    @classmethod
    def _start_proxy(cls):
        """非阻塞启动代理（返回 HTTPServer 供测试后 shutdown）。"""
        import threading
        from http.server import HTTPServer
        from continuum.proxy.server import ProxyHandler

        backend = SQLiteBackend(cls.db, MIGRATIONS)
        handler = type("Handler", (ProxyHandler,), {
            "backend": backend,
            "target_url": "https://api.deepseek.com",
        })
        server = HTTPServer(("127.0.0.1", PORT), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, backend   # daemon 线程随主进程退出

    def _post(self, path: str, body: dict, headers: dict | None = None):
        url = f"http://127.0.0.1:{PORT}{path}"
        payload = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=payload, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8")), dict(e.headers)

    def test_health_check(self):
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["status"], "ok")

    def test_openai_proxy_appends_session(self):
        """代理转发后对话落库（append 副作用）。"""
        code, body, headers = self._post("/v1/chat/completions", OPENAI_BODY_PROXY)
        # 即使转发 502（无真实 LLM key），append 副作用应该已发生
        be = SQLiteBackend(self.db, MIGRATIONS)
        n = be.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
        self.assertGreaterEqual(n, 1, "代理应捕获对话落库")
        be.close()

    def test_redline_block_returns_403(self):
        """红线命中 block → 403，不转发。"""
        be = SQLiteBackend(self.db, MIGRATIONS)
        be.add_redline(pattern="rm -rf", statement="严禁删除", action="block")
        be.close()
        code, body, headers = self._post("/v1/chat/completions", {
            "model": "test", "messages": [
                {"role": "user", "content": "rm -rf production/data"}]})
        self.assertEqual(code, 403)
        self.assertIn("blocked_by_redline", body.get("continuum", ""))


OPENAI_BODY_PROXY = {
    "model": "deepseek-chat",
    "messages": [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "帮我把这个数据清理一下"},
    ],
}


if __name__ == "__main__":
    unittest.main()
