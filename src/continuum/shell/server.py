# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Continuum 软件壳：本机回环图形界面（LOCAL-LOOPBACK-UI）。

- 仅绑定 127.0.0.1，永不暴露局域网/外网——不是外联通道，是本机 UI；
- 设置全部点选化：人格 / 红线（含正反用例）/ 记忆检索 / 沉淀 / 体检，
  不要求用户使用命令行（2026-10-06 楠哥：对 code 弱用户友好，降低全员学习成本）；
- 前端为内嵌静态单页（无构建工具、无 CDN、无外部资源——离线可用）。
"""

from __future__ import annotations

import json
import threading
import webbrowser
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path

from continuum.server import ContinuumServer

_STATIC_INDEX = Path(__file__).resolve().parent / "static" / "index.html"
# 与 backend 同款：HTTP 线程池跨线程访问 SQLite 连接，写路径串行化
_LOCK = threading.Lock()


class ShellHandler(BaseHTTPRequestHandler):
    """REST API + 静态页。srv/db_path 由 make_server 注入类属性。"""

    srv: ContinuumServer
    db_path: str

    # ---- 基础设施 ----

    def log_message(self, fmt, *args):  # noqa: N802 - 静默访问日志（stderr 干净）
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    MAX_BODY = 1_000_000   # 1MB 上限：正常 JSON 远小于此；防 Content-Length 失配/恶意死等

    def _body(self) -> dict:
        raw = self.headers.get("Content-Length")
        if not raw:
            return {}                       # 无长度（chunked 等）一律不读——宁可空不可卡
        try:
            length = int(raw)
        except ValueError:
            return {}
        if length <= 0 or length > self.MAX_BODY:
            return {}
        # 读体阶段临时 5s socket 超时：body 真未到达（代理/客户端失配）时快速失败
        # 绝不永久卡；正常场景 body 已被 BufferedReader 预读进缓冲，read 立即返回
        try:
            self.connection.settimeout(5.0)
        except (OSError, ValueError):
            pass
        try:
            data = self.rfile.read(length)
        except (TimeoutError, OSError):
            return {}
        finally:
            try:
                self.connection.settimeout(None)
            except (OSError, ValueError):
                pass
        if not data:
            return {}                       # 读尽/空体：视为无参数，绝不抛
        return json.loads(data.decode("utf-8"))

    # ---- 路由 ----

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = _STATIC_INDEX.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return
        if path == "/i18n.js":
            # 语言包（仅精确匹配此文件，不做任意路径服务——防目录穿越）
            body = (_STATIC_INDEX.parent / "i18n.js").read_bytes()
            self._send(200, body, "application/javascript; charset=utf-8")
            return
        try:
            with _LOCK:
                if path == "/api/overview":
                    self._json(self.api_overview())
                elif path == "/api/persona":
                    self._json(self.api_persona_get())
                elif path == "/api/redlines":
                    self._json(self.api_redlines())
                elif path == "/api/license":
                    self._json(self.api_license_get())
                elif path.startswith("/api/session/"):
                    # /api/session/{sid}/messages → split: ['', 'api', 'session', sid, 'messages']
                    sid = int(path.split("/")[3])
                    from urllib.parse import urlparse, parse_qs
                    qs = parse_qs(urlparse(self.path).query)
                    self._json(self.api_session_messages(
                        sid, int(qs.get("offset", ["0"])[0]),
                        int(qs.get("limit", ["50"])[0]), qs.get("q", [""])[0]))
                elif path == "/api/recall":
                    from urllib.parse import urlparse, parse_qs
                    qs = parse_qs(urlparse(self.path).query)
                    self._json(self.api_recall(qs.get("q", [""])[0],
                                               int(qs.get("limit", ["10"])[0])))
                else:
                    self._json({"error": f"未知路径 {path}"}, code=404)
        except Exception as e:  # noqa: BLE001 - API 层兜底，错误以 JSON 返回
            self._json({"error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            body = self._body()
            with _LOCK:
                if path == "/api/persona/version":
                    self._json(self.api_persona_version(body))
                elif path == "/api/persona/sample":
                    self._json(self.api_persona_sample(body))
                elif path == "/api/persona/activate":
                    self._json(self.api_persona_activate(body))
                elif path == "/api/redlines":
                    self._json(self.api_redline_add(body))
                elif path == "/api/redlines/toggle":
                    self._json(self.api_redline_toggle(body))
                elif path == "/api/redlines/test":
                    self._json(self.api_redline_test_add(body))
                elif path == "/api/guard-test":
                    self._json(self.api_guard_test())
                elif path == "/api/recall":
                    self._json(self.api_recall(body.get("q", ""),
                                               int(body.get("limit", 10))))
                elif path == "/api/extract":
                    self._json(self.api_extract(body))
                elif path == "/api/doctor":
                    # 复用 do_POST 开头已读好的 body（二次 _body() 会读尽返回空 →
                    # lang 参数丢失 → 英文界面出中文；2026-10-06 通读实测实锤）
                    self._json(self.api_doctor(body))
                elif path == "/api/license":
                    self._json(self.api_license_activate(body))
                else:
                    self._json({"error": f"未知路径 {path}"}, code=404)
        except Exception as e:  # noqa: BLE001
            self._json({"error": f"{type(e).__name__}: {e}"}, code=500)

    # ---- API 实现 ----

    def api_overview(self) -> dict:
        be = self.srv.be
        sessions = []
        for r in be.conn.execute(
            "SELECT id, external_id, title, project_id, started_at FROM sessions ORDER BY id"
        ).fetchall():
            n = be.conn.execute("SELECT COUNT(*) c FROM messages WHERE session_id=?",
                                (r["id"],)).fetchone()["c"]
            sessions.append({"id": r["id"], "external_id": r["external_id"],
                             "title": r["title"], "project_id": r["project_id"],
                             "messages": n})
        stats = {
            "sessions": len(sessions),
            "messages": be.conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"],
            "memories_active": be.conn.execute(
                "SELECT COUNT(*) c FROM memories WHERE status='active'").fetchone()["c"],
            "memories_pending": be.conn.execute(
                "SELECT COUNT(*) c FROM memories WHERE status='pending'").fetchone()["c"],
            "redlines": be.conn.execute(
                "SELECT COUNT(*) c FROM redlines WHERE enabled=1").fetchone()["c"],
            "persona_version": (be.persona_current() or {}).get("version"),
        }
        from continuum.license import features_unlocked, load_state
        st = load_state()
        return {"stats": stats, "sessions": sessions, "db_path": self.db_path,
                "plan": st.plan, "activated": st.activated,
                "features": list(features_unlocked())}

    def api_persona_get(self) -> dict:
        be = self.srv.be
        cur = be.persona_current()
        samples = be.persona_samples_for(cur["version"]) if cur else []
        cur_name = cur["name"] if cur else None
        versions = [dict(r) for r in be.conn.execute(
            "SELECT version, change_reason, created_at, active FROM persona_versions"
            " WHERE name=? ORDER BY version DESC LIMIT 20",
            (cur_name or "__none__",)).fetchall()] if cur else []
        return {"current": cur, "samples": samples, "versions": versions,
                "named": be.persona_list_named()}

    def api_persona_activate(self, body: dict) -> dict:
        name = (body.get("name") or "").strip()
        if not name:
            return {"error": "name 不能为空"}
        n = self.srv.be.persona_activate(name)
        return {"name": name, "switched": n}

    def api_persona_version(self, body: dict) -> dict:
        text = (body.get("text") or "").strip()
        if not text:
            return {"error": "text 不能为空"}
        # name 缺省 = 当前激活套（「修改当前人格」是用户直觉——
        # 修 A 套却落到 default 套 = 版本链断裂，2026-10-06 审查实锤）
        name = (body.get("name") or "").strip()
        if not name:
            cur = self.srv.be.persona_current()
            name = cur["name"] if cur else "default"
        vid = self.srv.be.persona_create(text, change_reason=body.get("reason") or "壳端录入",
                                         name=name)
        return {"version": vid, "name": name}

    def api_session_messages(self, session_id: int, offset: int = 0,
                             limit: int = 50, q: str = "") -> dict:
        """会话浏览器：完整消息内容，分页 + 关键词过滤。"""
        limit = max(1, min(limit, 200))
        total = self.srv.be.conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE session_id=?", (session_id,)
        ).fetchone()["c"]
        # LIKE 通配符转义：q 含 % / _ 时不得放大为全匹配（2026-10-06 通读实锤）
        q_esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.srv.be.conn.execute(
            "SELECT id, ts, role, content FROM messages WHERE session_id=?"
            " AND content LIKE ? ESCAPE '\\' ORDER BY seq LIMIT ? OFFSET ?",
            (session_id, f"%{q_esc}%" if q else "%", limit, offset)).fetchall()
        return {"total": total, "offset": offset, "limit": limit,
                "messages": [{"id": r["id"], "ts": r["ts"], "role": r["role"],
                              "content": r["content"]} for r in rows]}

    def api_persona_sample(self, body: dict) -> dict:
        cur = self.srv.be.persona_current()
        if cur is None:
            return {"error": "尚无人格版本——先创建版本"}
        if not (body.get("user") or "").strip() or not (body.get("agent") or "").strip():
            return {"error": "user/agent 都不能为空"}
        sid = self.srv.be.persona_add_sample(
            cur["version"], body["user"], body["agent"], tag=body.get("tag"))
        return {"sample_id": sid}

    def api_redlines(self) -> dict:
        rows = self.srv.be.conn.execute(
            "SELECT r.id, r.pattern, r.statement, r.action, r.scope, r.enabled,"
            " (SELECT COUNT(*) FROM redline_tests t WHERE t.redline_id=r.id) AS cases"
            " FROM redlines r ORDER BY r.id").fetchall()
        return {"redlines": [dict(r) for r in rows]}

    def api_redline_add(self, body: dict) -> dict:
        if not (body.get("pattern") or "").strip() or not (body.get("statement") or "").strip():
            return {"error": "pattern/statement 不能为空"}
        rid = self.srv.be.add_redline(
            pattern=body["pattern"], statement=body["statement"],
            action=body.get("action") or "block", scope=body.get("scope") or "global")
        return {"id": rid}

    def api_redline_toggle(self, body: dict) -> dict:
        cur = self.srv.be.conn.execute(
            "SELECT enabled FROM redlines WHERE id=?", (body.get("id"),)).fetchone()
        if cur is None:
            return {"error": "红线不存在"}
        new = 0 if cur["enabled"] else 1
        self.srv.be.conn.execute("UPDATE redlines SET enabled=? WHERE id=?",
                                 (new, body["id"]))
        return {"id": body["id"], "enabled": bool(new)}

    def api_redline_test_add(self, body: dict) -> dict:
        case = body.get("case")
        if case not in ("positive", "negative"):
            return {"error": "case 必须是 positive/negative"}
        expected = body.get("expected") or ("block" if case == "positive" else "allow")
        tid = self.srv.be.add_redline_test(body["redline_id"], case,
                                           body.get("sample") or "",
                                           expected_action=expected)
        return {"test_id": tid}

    def api_guard_test(self) -> dict:
        results = self.srv.run_redline_tests()
        passed = sum(1 for r in results if r["pass"])
        return {"results": results, "passed": passed, "total": len(results)}

    def api_recall(self, q: str, limit: int) -> dict:
        if not q.strip():
            return {"items": []}
        r = self.srv.memory_recall(q, limit=max(1, min(limit, 50)))
        return {"items": [i.__dict__ for i in r.items],
                "latency_ms": r.latency_ms, "stopped_by": r.stopped_by}

    def api_extract(self, body: dict) -> dict:
        from continuum.server.tools import ExtractScope
        r = self.srv.memory_extract(ExtractScope(session_id=body.get("session_id"),
                                                 confirm=bool(body.get("confirm"))))
        return {"active": r.produced_active, "pending": r.produced_pending,
                "scanned": r.scanned_messages}

    def api_doctor(self, body: dict | None = None) -> dict:
        import threading as _threading
        from continuum.doctor import run_doctor
        lang = (body or {}).get("lang") or "zh"
        # 体检在独立线程执行：Windows 实测「HTTP 线程读请求体后 spawn 子进程」会死锁
        # （线程卡死在 socket 读，连带 _LOCK 拖垮后续全部 API）。移线程=隔离该组合。
        result = {}

        def _work():
            buf = StringIO()
            with redirect_stdout(buf):
                code = run_doctor(lang=lang, spawn_check=False)
            result["output"] = buf.getvalue()
            result["code"] = code

        t = _threading.Thread(target=_work, daemon=True)
        t.start()
        t.join(timeout=45)
        if t.is_alive():
            return {"error": "体检超时（45s），请稍后重试"}
        if "output" not in result:
            return {"error": "体检执行失败"}
        return {"output": result["output"], "code": result["code"]}

    # ---- 授权（正式模块 continuum.license；真签名校验随商业化接入） ----

    def api_license_get(self) -> dict:
        from continuum.license import features_unlocked, load_state
        st = load_state()
        return {"activated": st.activated, "plan": st.plan,
                "key_masked": st.key_masked, "activated_at": st.activated_at,
                "features": list(features_unlocked())}

    def api_license_activate(self, body: dict) -> dict:
        from continuum.license import activate
        key = (body.get("key") or "").strip().upper().replace(" ", "")
        plan = body.get("plan") or "pro"
        if plan not in ("pro", "pro_cloud"):
            return {"error": "plan 必须是 pro / pro_cloud"}
        try:
            st = activate(key, plan)
        except ValueError as e:
            return {"error": str(e)}
        return {"activated": st.activated, "plan": st.plan}


def make_server(srv: ContinuumServer, db_path: str, host: str = "127.0.0.1",
                port: int = 8501) -> ThreadingHTTPServer:
    """装配壳服务（仅回环地址）。"""

    class _Handler(ShellHandler):
        pass

    _Handler.srv = srv
    _Handler.db_path = db_path
    return ThreadingHTTPServer((host, port), _Handler)


def run_shell(srv: ContinuumServer, db_path: str, port: int = 8501,
              open_browser: bool = True) -> None:
    httpd = make_server(srv, db_path, port=port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"Continuum 软件壳已启动：{url}  （Ctrl+C 退出；仅本机可访问）", flush=True)
    if open_browser:
        threading.Thread(target=lambda: (webbrowser.open(url)),
                         daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n软件壳已退出。")
