# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Continuum CLI（零依赖，argparse）。

子命令：
- serve            启动 MCP server（P1+）
- proxy            启动 LLM API 透明代理（P2-c：OpenAI 兼容/Anthropic/Gemini）
- doctor           一键体检：配置/server/宿主加载状态 + 各宿主生效指引
- persona add      录入 few-shot 样本（人工挑选，docs/01 §5.4）
- persona list     列样本
- persona version  创建人格新版本
- persona current  查看当前人格
- import workbuddy 从 WorkBuddy jsonl 导入会话（P1 验收通道）
- backup           备份记忆库
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from continuum.version import VERSION


def _open_backend(db_path: str):
    from continuum.storage import SQLiteBackend

    migrations = Path(__file__).resolve().parent / "storage" / "migrations" / "sql"
    return SQLiteBackend(db_path, migrations)


def _open_server(db_path: str):
    from continuum.server import ContinuumServer

    return ContinuumServer(_open_backend(db_path))


# ---------- persona 子命令 ----------

def cmd_persona_add(args) -> int:
    be = _open_backend(args.db)
    cur = be.persona_current()
    if cur is None:
        print("尚无人格版本——先用 `persona version --text <状态块内容>` 创建")
        return 1
    sample_id = be.persona_add_sample(
        persona_version=cur["version"],
        user_utterance=args.user,
        agent_response=args.agent,
        tag=args.tag,
    )
    print(f"样本 #{sample_id} 已挂到 persona v{cur['version']}")
    be.close()
    return 0


def cmd_persona_list(args) -> int:
    be = _open_backend(args.db)
    cur = be.persona_current()
    version = cur["version"] if cur else None
    if args.all:
        rows = be.persona_samples(None, limit=1000)
    else:
        rows = be.persona_samples(version, limit=1000)
    print(f"人格版本: v{version}（{'最新' if cur else '未创建'}）样本 {len(rows)} 条")
    for r in rows:
        tag = f" [{r['tag']}]" if r["tag"] else ""
        print(f"  #{r['id']}{tag} 用户: {r['user_utterance'][:60]}")
        print(f"      agent: {r['agent_response'][:60]}")
    be.close()
    return 0


def cmd_persona_version(args) -> int:
    be = _open_backend(args.db)
    cur = be.persona_current()
    parent = cur["version"] if cur else None
    ver = be.persona_create(snapshot_md=args.text, change_reason=args.reason, parent_version=parent)
    print(f"人格 v{ver} 已创建（parent=v{parent}，reason={args.reason}）")
    be.close()
    return 0


def cmd_persona_current(args) -> int:
    be = _open_backend(args.db)
    try:
        cur = be.persona_current()
        if cur is None:
            print("（无人格版本）")
            return 1
        print(f"== persona v{cur['version']}（{cur['created_at']}，{cur['change_reason']}）==")
        print(cur["snapshot_md"])
        return 0
    finally:
        be.close()


# ---------- import / backup ----------

def cmd_import_workbuddy(args) -> int:
    from continuum.importers import import_workbuddy_session

    be = _open_backend(args.db)
    rep = import_workbuddy_session(args.jsonl, be, project_id=args.project, title=args.title)
    print(f"导入完成: 总行 {rep.total_lines} / 导入 {rep.imported_messages} / "
          f"非正文 {rep.skipped_non_message} / 空内容 {rep.skipped_empty_content} / "
          f"超大跳过 {rep.skipped_oversized}")
    print(f"session_id = {rep.session_id}（extract/recall 用）")
    be.close()
    return 0


def cmd_backup(args) -> int:
    be = _open_backend(args.db)
    pages = be.backup_to(args.out)
    print(f"备份完成: {args.out}（{pages} 页）")
    be.close()
    return 0


# ---------- serve / 钩子执行（档位 B 核心接线） ----------

def cmd_serve(args) -> int:
    """--on 缺省：启动 MCP server（stdio）。
    --on session-end / prompt / guard：Claude Code 钩子执行模式（档位 B）——
    从 stdin 读 Claude Code hook JSON，执行对应动作后按其协议输出。"""
    if getattr(args, "on", None):
        return _run_hook(args.on, args.db, stdin_text=getattr(args, "stdin_text", None))
    try:
        from continuum.server.mcp import MCPNotInstalled, build_mcp_server
    except ImportError:
        print("MCP 支持未安装：pip install 'mcp>=1.0'（可选依赖，核心零依赖不受影响）")
        return 1
    srv = _open_server(args.db)
    feed = None
    try:                                # 第 0 扳机接线：常驻 MCP 模式顺带增量喂食
        from continuum.importers.workbuddy_feed import WorkbuddyFeed
        feed = WorkbuddyFeed.build_default(srv.be)
    except Exception:                   # pragma: no cover - 喂食器失败不影响 serve
        feed = None
    try:
        mcp = build_mcp_server(srv, feed=feed)
    except MCPNotInstalled as e:
        # build 时才延迟导入 mcp 包；未安装时给人类可读输出而非裸 traceback
        print(f"MCP 支持未安装：{e}")
        return 1
    mcp.run()          # stdio
    return 0


def cmd_proxy(args) -> int:
    """启动 LLM API 透明代理（方式 B：零宿主配合，阻塞直到 Ctrl+C）。

    请求/响应原样转发给 --target；同时透明抽取对话入库、注入记忆、计量 token。
    与方式 A（serve：MCP 直连）互为冗余防线，可单独使用也可叠加。"""
    from continuum.proxy.server import run_proxy

    migrations = str(Path(__file__).resolve().parent / "storage" / "migrations" / "sql")
    run_proxy(host=args.host, port=args.port, db_path=args.db,
              target_url=args.target, migrations_dir=migrations)
    return 0


def cmd_doctor(args) -> int:
    """一键体检：配置文件 → 记忆库 → 按配置确切命令拉起 server 握手 → 生效指引。"""
    from continuum.doctor import run_doctor

    return run_doctor()


def cmd_redline_add(args) -> int:
    be = _open_backend(args.db)
    rid = be.add_redline(pattern=args.pattern, statement=args.statement,
                         action=args.action, scope=args.scope)
    print(f"红线 #{rid} 已录入（action={args.action}, scope={args.scope}）——"
          f"用 `redline test --redline-id {rid}` 补正反用例防误伤")
    be.close()
    return 0


def cmd_redline_list(args) -> int:
    import sqlite3 as _sq
    be = _open_backend(args.db)
    rows = be.conn.execute(
        "SELECT r.id, r.pattern, r.statement, r.action, r.scope, r.enabled,"
        " (SELECT COUNT(*) FROM redline_tests t WHERE t.redline_id=r.id) AS cases"
        " FROM redlines r ORDER BY r.id").fetchall()
    if not rows:
        print("（无红线——用 `redline add` 录入）")
    for r in rows:
        mark = "✓" if r["enabled"] else "✗"
        print(f"#{r['id']} [{mark}] {r['action']:<5} {r['scope']:<12} 用例{r['cases']}  {r['statement']}")
        print(f"      pattern: {r['pattern']}")
    be.close()
    return 0


def cmd_redline_test_add(args) -> int:
    expected = args.expected or ("block" if args.case == "positive" else "allow")
    be = _open_backend(args.db)
    tid = be.add_redline_test(args.redline_id, args.case, args.sample,
                              expected_action=expected)
    print(f"用例 #{tid} 已录（{args.case}, expected={expected}）——`guard-test` 执行")
    be.close()
    return 0


def _read_hook_stdin(injected: str | None = None) -> dict:
    """读 Claude Code hook 的 stdin JSON（解析失败返回空 dict，不挂死）。
    injected 非空时直接使用（测试/编程调用），不读 stdin。"""
    if injected is not None:
        raw = injected
    else:
        raw = sys.stdin.read()
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def _run_hook(on: str, db_path: str, stdin_text: str | None = None) -> int:
    from continuum.server import ContinuumServer, ExtractScope

    be = _open_backend(db_path)
    try:
        srv = ContinuumServer(be)      # 复用同一连接（杜绝双连接句柄残留）
        if on == "session-end":
            payload = _read_hook_stdin(stdin_text)
            r = srv.memory_extract(ExtractScope(session_id=None, since_ts=None))
            print(json.dumps({"continuum": "session-end 沉淀完成",
                              "produced_pending": r.produced_pending,
                              "scanned": r.scanned_messages}, ensure_ascii=False))
            return 0
        if on == "prompt":
            # UserPromptSubmit：stdout 会作为上下文注入——输出装配摘要 + 提醒
            pkg = srv.memory_assemble(project_id=None)
            print(pkg.snapshot_md[:4000])
            if pkg.recent_verbatim:
                print("\n[最近现场]\n" + "\n".join(pkg.recent_verbatim[-5:]))
            print("\n（以上为 Continuum 记忆装配。需要更多历史时调用 memory_recall；"
                  "记忆中有红线的内容严禁触碰。）")
            return 0
        if on == "guard":
            payload = _read_hook_stdin(stdin_text)
            tool_name = payload.get("tool_name", "")
            tool_input = payload.get("tool_input") or {}
            target = json.dumps(tool_input, ensure_ascii=False)[:500]
            v = srv.memory_guard(
                __import__("continuum.server", fromlist=["Operation"]).Operation(
                    kind=tool_name, target=target))
            if v.verdict == "block":
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"[Continuum 红线] {v.reason}"}}, ensure_ascii=False))
            elif v.verdict == "ask":
                print(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "[Continuum] 该操作命中需人工确认的红线"}}, ensure_ascii=False))
            # warn/allow：无输出 + exit 0（放行）
            return 0
        print(f"未知 --on 目标: {on}", file=sys.stderr)
        return 2
    finally:
        be.close()




# ---------- guard 测试集执行器 ----------

def cmd_guard_test(args) -> int:
    """执行 redline_tests 正反测试集——防误伤机制的自动化回归。"""
    from continuum.server import ContinuumServer

    srv = _open_server(args.db)
    results = srv.run_redline_tests()
    total = len(results)
    failed = [r for r in results if not r["pass"]]
    for r in results:
        mark = "PASS" if r["pass"] else "FAIL"
        print(f"[{mark}] 红线#{r['redline_id']} {r['case_type']} target={r['sample_target'][:60]}"
              f" 期望={r['expected_action']} 实际={r['actual']}  {r['note'][:40]}")
    be_closed = None
    print(f"\n执行 {total} 项，通过 {total - len(failed)}，失败 {len(failed)}")
    srv.be.close()
    return 0 if not failed else 1


# ---------- 解析器 ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="continuum", description="Continuum——跨 agent 持久记忆层")
    p.add_argument("--db", default="continuum.continuum.db", help="记忆库路径")
    p.add_argument("--version", action="version", version=f"continuum {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("persona", help="人格状态块与样本管理")
    psub = sp.add_subparsers(dest="persona_cmd", required=True)
    pa = psub.add_parser("add", help="录入 few-shot 样本")
    pa.add_argument("--user", required=True)
    pa.add_argument("--agent", required=True)
    pa.add_argument("--tag", default=None)
    pa.set_defaults(func=cmd_persona_add)
    pl = psub.add_parser("list", help="列样本")
    pl.add_argument("--all", action="store_true", help="含历史版本样本")
    pl.set_defaults(func=cmd_persona_list)
    pv = psub.add_parser("version", help="创建人格新版本")
    pv.add_argument("--text", required=True)
    pv.add_argument("--reason", required=True)
    pv.set_defaults(func=cmd_persona_version)
    pc = psub.add_parser("current", help="查看当前人格")
    pc.set_defaults(func=cmd_persona_current)

    imp = sub.add_parser("import-workbuddy", help="导入 WorkBuddy 会话 jsonl")
    imp.add_argument("jsonl")
    imp.add_argument("--project", default=None)
    imp.add_argument("--title", default=None)
    imp.set_defaults(func=cmd_import_workbuddy)

    bk = sub.add_parser("backup", help="备份记忆库")
    bk.add_argument("out")
    bk.set_defaults(func=cmd_backup)

    sv = sub.add_parser("serve", help="启动 MCP server（stdio）；--on 进入钩子执行模式")
    sv.add_argument("--db", default=argparse.SUPPRESS,
                    help="记忆库路径（也可放子命令前：continuum --db X serve）")
    sv.add_argument("--on", choices=["session-end", "prompt", "guard"], default=None,
                    help="档位 B 钩子执行模式（由 hooks 配置模板自动生成）")
    sv.set_defaults(func=cmd_serve)

    px = sub.add_parser("proxy", help="启动 LLM API 透明代理（零宿主配合，方式 B）")
    px.add_argument("--db", default=argparse.SUPPRESS,
                    help="记忆库路径（也可放子命令前：continuum --db X proxy）")
    px.add_argument("--target", required=True,
                    help="上游 LLM API base URL（如 https://api.deepseek.com）")
    px.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    px.add_argument("--port", type=int, default=8402, help="监听端口（默认 8402）")
    px.set_defaults(func=cmd_proxy)

    dt = sub.add_parser("doctor", help="一键体检：配置/server 自检/宿主加载状态 + 生效指引")
    dt.set_defaults(func=cmd_doctor)

    gt = sub.add_parser("guard-test", help="执行红线正反测试集（防误伤回归）")
    gt.set_defaults(func=cmd_guard_test)

    rl = sub.add_parser("redline", help="红线与正反测试集管理")
    rlsub = rl.add_subparsers(dest="redline_cmd", required=True)
    rl_add = rlsub.add_parser("add", help="录入红线（memory_guard 据此拦截）")
    rl_add.add_argument("--pattern", required=True, help="匹配模式（对 target/detail 做包含匹配）")
    rl_add.add_argument("--statement", required=True, help="红线陈述（逐字存储，禁止改写）")
    rl_add.add_argument("--action", choices=["block", "warn", "ask"], default="block")
    rl_add.add_argument("--scope", default="global", help="global 或 project:<id>")
    rl_add.set_defaults(func=cmd_redline_add)
    rl_list = rlsub.add_parser("list", help="列红线（含用例计数）")
    rl_list.set_defaults(func=cmd_redline_list)
    rl_t = rlsub.add_parser("test", help="录正反用例（positive=该拦 / negative=不该拦）")
    rl_t.add_argument("--redline-id", type=int, required=True)
    rl_t.add_argument("--case", choices=["positive", "negative"], required=True)
    rl_t.add_argument("--sample", required=True, help="样例 target")
    rl_t.add_argument("--expected", choices=["block", "warn", "allow", "ask"], default=None,
                      help="缺省：positive=block / negative=allow")
    rl_t.set_defaults(func=cmd_redline_test_add)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
