# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""代理本体：本地 HTTP server，接收宿主请求 → 注入 → 转发 LLM → 返回。

零第三方依赖（http.server + urllib.request）。
宿主把 LLM base_url 指向 http://127.0.0.1:{port}/v1 即完成接入。

BYOK-USER-INITIATED-NETWORK：本模块的全部外联都发生在用户显式启动
`continuum proxy --target <上游>` 之后，转发目标完全由用户指定；核心存储层零外联。
"""

from __future__ import annotations

import json
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from continuum.storage.backend import StorageBackend
from continuum.storage.sqlite_backend import SQLiteBackend
from continuum.proxy import process_request, process_response

_TARGET_KEY = "continuum_target_url"   # 真实 LLM endpoint（env 或启动参数）


class ProxyHandler(BaseHTTPRequestHandler):
    """每个请求：格式检测 → 对话提取 → 红线检查 → 注入装配包 → 转发 LLM → 返回。"""

    backend: StorageBackend = None       # 由 factory 注入
    target_url: str = ""                 # 真实 LLM base URL
    assembly_text: str | None = None     # 装配包文本（每请求或缓存）
    judge = None                         # 可选 Judge

    def do_POST(self):
        t0 = time.perf_counter()
        body = self._read_body()
        if body is None:
            self._respond(400, {"error": "非法 JSON"})
            return

        headers = {k.lower(): v for k, v in self.headers.items()}
        path = self.path

        # 红线检查 + 注入
        fmt_name, modified_body, extra_headers = process_request(
            path, headers, body,
            assembly_text=self.assembly_text,
            judge=self.judge,
        )

        # 红线 block → 直接拒绝
        if "X-Continuum-Blocked" in extra_headers:
            self._respond(403, {
                "error": extra_headers["X-Continuum-Blocked"],
                "continuum": "blocked_by_redline",
            }, extra_headers)
            return

        # 转发到真实 LLM
        target = self.target_url.rstrip("/") + path
        resp_body, resp_status, resp_headers_fwd = self._forward(
            target, headers, modified_body or body
        )

        # token 计量
        if fmt_name and resp_body:
            token_headers = process_response(fmt_name, resp_body)
            extra_headers.update(token_headers)

        extra_headers["X-Continuum-Latency-Ms"] = str(round((time.perf_counter() - t0) * 1000))
        self._respond(resp_status, resp_body, {**extra_headers, **resp_headers_fwd})

    def do_GET(self):
        """健康检查 / 状态查询。"""
        if self.path in ("/health", "/healthz"):
            self._respond(200, {"status": "ok", "version": "p2-c"})
        else:
            self._respond(404, {"error": "not found"})

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _forward(self, target: str, orig_headers: dict, body: dict) -> tuple[dict | None, int, dict]:
        """转发到真实 LLM。返回 (response_body, status, response_headers)。"""
        # 过滤 hop-by-hop headers
        fwd_headers = {
            k: v for k, v in orig_headers.items()
            if k not in ("host", "content-length", "transfer-encoding",
                         "connection", "keep-alive")
        }
        # LLM 认证：优先用宿主传来的 Authorization（BYOK），否则用环境变量
        import os
        api_key = os.environ.get("CONTINUUM_LLM_API_KEY", "")
        if api_key and "authorization" not in fwd_headers:
            fwd_headers["authorization"] = f"Bearer {api_key}"

        payload = json.dumps(body).encode("utf-8")
        fwd_headers["content-type"] = "application/json"
        fwd_headers["content-length"] = str(len(payload))

        req = urllib.request.Request(target, data=payload, headers=fwd_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_body = json.loads(resp.read().decode("utf-8"))
                return resp_body, resp.status, dict(resp.headers)
        except urllib.error.HTTPError as e:
            resp_body = json.loads(e.read().decode("utf-8"))
            return resp_body, e.code, dict(e.headers)
        except Exception as e:
            return {"error": f"proxy forward failed: {type(e).__name__}"}, 502, {}

    def _respond(self, status: int, body: dict, extra_headers: dict | None = None):
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        """静默——不往 stdout 吐请求日志（保持 agent 终端干净）。"""


def run_proxy(host: str = "127.0.0.1", port: int = 8402,
              db_path: str = "continuum.continuum.db",
              target_url: str = "",
              migrations_dir: str | None = None) -> HTTPServer:
    """启动代理 server（阻塞）。返回 HTTPServer 实例（可调 .shutdown() 停止）。"""
    from continuum.server import ContinuumServer

    backend = SQLiteBackend(db_path, migrations_dir or "")
    handler = type("ProxyHandler", (ProxyHandler,), {
        "backend": backend,
        "target_url": target_url,
        "assembly_text": None,
    })
    server = HTTPServer((host, port), handler)
    print(f"Continuum proxy listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        backend.close()
    return server
