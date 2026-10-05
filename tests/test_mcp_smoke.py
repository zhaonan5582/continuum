# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""档位 B 真机冒烟：MCP stdio 握手 + 七工具列表 + append→recall 闭环。

需要可选依赖 mcp（pip install 'mcp>=1.0'）；CI 零依赖环境自动跳过。
真机语义：以真实子进程方式启动 `continuum serve`（stdio JSON-RPC），
走完整 MCP 握手，而不是绕过协议直接调函数。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import queue
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import mcp  # noqa: F401
    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False

EXPECTED_TOOLS = {
    "memory_append", "memory_extract", "memory_recall", "memory_audit",
    "memory_compact", "memory_assemble", "memory_guard",
}


@unittest.skipUnless(_HAS_MCP, "需要 mcp 可选依赖（pip install 'mcp>=1.0'）")
class TestMCPStdioSmoke(unittest.TestCase):
    """真机：子进程起 continuum serve，JSON-RPC 全握手 + 一次写读闭环。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.db = str(Path(self.td.name) / "smoke.continuum.db")
        env = {**os.environ, "PYTHONPATH": str(REPO / "src"),
               "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "continuum.cli", "serve", "--db", self.db],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=str(REPO), text=True, encoding="utf-8", bufsize=1,
        )
        self._q: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def tearDown(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        err = self._stderr.read() if self._stderr else ""
        self.td.cleanup()
        if err.strip() and "Traceback" in err:
            self.fail(f"server stderr 有 traceback：\n{err[:2000]}")

    def _pump(self):
        self._stderr = self.proc.stderr
        for line in self.proc.stdout:
            self._q.put(line)

    def _rpc(self, payload: dict, timeout: float = 30.0) -> dict:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        line = self._q.get(timeout=timeout)
        return json.loads(line)

    def _initialize(self) -> dict:
        resp = self._rpc({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "continuum-smoke", "version": "0.0.1"}},
        })
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()
        return resp

    def test_handshake_tools_and_roundtrip(self):
        # 1) 握手
        init = self._initialize()
        self.assertEqual(init.get("jsonrpc"), "2.0")
        self.assertIn("serverInfo", init.get("result", {}))

        # 2) 七工具齐
        tools = self._rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in tools["result"]["tools"]}
        self.assertEqual(EXPECTED_TOOLS, names)

        # 3) 写→读闭环（append 一条带唯一标记的消息，recall 应命中）
        marker = f"smoke-{os.getpid()}-秦直道九原至云阳"
        append = self._rpc({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "memory_append", "arguments": {
                "host_agent": "smoke-host", "external_session_id": "s-smoke-1",
                "messages": [{"ts": "2026-10-05T10:00:00.000Z", "role": "user",
                              "content": f"{marker} 蒙恬主持修建"}],
            }},
        })
        self.assertFalse(append["result"].get("isError", False),
                         f"append 失败：{append}")
        recall = self._rpc({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "memory_recall", "arguments": {
                "query": "秦直道", "limit": 5}},
        })
        text = json.dumps(recall, ensure_ascii=False)
        self.assertIn("秦直道", text, f"recall 未命中标记：{text[:1000]}")


if __name__ == "__main__":
    unittest.main()
