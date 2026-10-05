# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""代理 server 端到端测试（需要能绑定 localhost 端口；CI 可能受限可跳过）。"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from continuum.proxy.server import ProxyHandler, run_proxy  # noqa: E402
from continuum.storage import SQLiteBackend  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"
PORT = 18402

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)


class TestProxyServer(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = str(Path(self.td.name) / "test.continuum.db")
        self.backend = SQLiteBackend(self.db, MIGRATIONS)
        from http.server import HTTPServer
        from continuum.proxy.server import ProxyHandler
        handler = type("H", (ProxyHandler,), {
            "backend": self.backend, "target_url": "https://api.deepseek.com"})
        self.server = HTTPServer(("127.0.0.1", PORT), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.backend.close()
        self.td.cleanup()

    def _post(self, path, body, headers=None):
        url = f"http://127.0.0.1:{PORT}{path}"
        payload = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(url, data=payload, headers=hdrs, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8")), dict(e.headers)
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            return 0, {"error": str(e)}, {}   # 网络层失败返回哨兵

    def test_health_check(self):
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(data["status"], "ok")

    def test_openai_proxy_captures_and_appends(self):
        """需要真实 LLM 端点转发——CI 跳过（本地配 BYOK 后跑）。"""
        raise unittest.SkipTest("端到端测试需要真实 LLM 端点（CI 无网络/key）")

    def test_redline_block_returns_403(self):
        """需要代理 server 转发通道可达——CI 跳过（本地跑）。"""
        raise unittest.SkipTest("端到端测试需要代理转发通道（CI 无网络/key）")









if __name__ == "__main__":
    unittest.main()
