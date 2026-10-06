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
- feed             喂食器：主动扫描会话文件增量入库（--once / --watch）
- agents           探测本机 agent 与可用接入机制（只读视图）
"""

from __future__ import annotations

import argparse
import json
import os
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
    name = getattr(args, "name", None) or "default"
    cur = be.persona_current()
    parent = cur["version"] if cur and cur["name"] == name else None
    ver = be.persona_create(snapshot_md=args.text, change_reason=args.reason,
                            parent_version=parent, name=name)
    state = "已激活" if be.persona_current() and be.persona_current()["version"] == ver else "未激活"
    print(f"人格 v{ver}（{name}）已创建（parent={f'v{parent}' if parent else '无'}，reason={args.reason}，{state}）")
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
    try:
        rep = import_workbuddy_session(args.jsonl, be, project_id=args.project, title=args.title)
    except FileNotFoundError as e:
        print(f"导入失败：文件不存在——{e}\n请检查路径后重试。")
        be.close()
        return 1
    except Exception as e:  # noqa: BLE001 - 用户侧错误一律人类可读，不留裸 traceback
        print(f"导入失败：文件无法解析（{type(e).__name__}: {e}）\n"
              f"请确认是 WorkBuddy 会话 jsonl 文件（每行一个 JSON 对象）。")
        be.close()
        return 1
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
    try:                                # 第 0 扳机接线：常驻 MCP 模式顺带增量喂食（多宿主）
        from continuum.importers.feed_base import SessionFeeder
        feed = SessionFeeder.build_default(srv.be)
    except Exception:                   # pragma: no cover - 喂食器失败不影响 serve
        feed = None
    if feed is not None:
        feed.start_background(120.0)     # 真喂食：常驻期间每 2 分钟主动扫描（宪法 10）
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


def cmd_shell(args) -> int:
    """软件壳：本机回环图形界面（127.0.0.1，非外联）——设置全部点选化。
    --install-autostart / --uninstall-autostart：注册/移除登录自启（后台无窗口常驻，
    用户不再需要开着命令行窗口——成熟产品形态）；--daemon：本次运行后台化（日志落盘）。"""
    from http.server import ThreadingHTTPServer  # noqa: F401 - 预检导入失败尽早暴露

    if getattr(args, "install_autostart", False):
        return _shell_autostart_install(args)
    if getattr(args, "uninstall_autostart", False):
        return _shell_autostart_uninstall()

    if getattr(args, "daemon", False):
        _redirect_to_log()               # 无控制台场景：stdout/stderr 落盘 ~/.continuum/shell.log

    srv = _open_server(args.db)
    try:                                 # 壳常驻期间也真喂食（与 serve 同款后台线程，多宿主）
        from continuum.importers.feed_base import SessionFeeder
        feed = SessionFeeder.build_default(srv.be)
        if feed is not None:
            feed.start_background(120.0)
    except Exception:                    # pragma: no cover - 喂食失败不影响壳
        pass
    from continuum.shell.server import run_shell

    run_shell(srv, args.db, port=args.port, open_browser=not args.no_browser)
    return 0


TASK_NAME = "ContinuumShell"        # 计划任务名（安装/卸载共用一个）


def _redirect_to_log() -> None:
    """把 stdout/stderr 重定向到 ~/.continuum/shell.log（无控制台运行场景）。"""
    import sys as _sys
    log_path = Path.home() / ".continuum" / "shell.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(log_path, "a", encoding="utf-8", buffering=1)
    _sys.stdout = f
    _sys.stderr = f


def _shell_autostart_install(args) -> int:
    """注册登录自启的计划任务（无窗口后台跑壳）——解决"必须开着命令行"。
    实现：schtasks ONLOGON + pythonw（venv Scripts 下自带的无控制台解释器）。"""
    import subprocess as _sp
    import sys as _sys

    pyw = Path(_sys.executable).with_name("pythonw.exe")
    if not pyw.exists():
        pyw = Path(_sys.executable)      # 退化：用带控制台的解释器（仍可工作）
    db = os.path.abspath(os.path.expanduser(args.db))
    tr = f'"{pyw}" -m continuum.cli --db "{db}" shell --no-browser --daemon'
    r = _sp.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
                 "/SC", "ONLOGON", "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"注册失败：{(r.stderr or r.stdout or '').strip()[:300]}")
        print("（如提示权限不足，用管理员权限的终端重跑本命令）")
        return 1
    print(f"[OK] 已注册登录自启：{TASK_NAME}")
    print(f"  命令: {tr}")
    print("  壳将在下次登录时自动后台运行（无窗口），日志见 ~/.continuum/shell.log")
    print(f"  立即启动（无需等下次登录）：schtasks /Run /TN {TASK_NAME}")
    print("  取消自启：continuum shell --uninstall-autostart")
    return 0


def _shell_autostart_uninstall() -> int:
    import subprocess as _sp

    r = _sp.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(f"移除失败（可能未注册）：{(r.stderr or r.stdout or '').strip()[:200]}")
        return 1
    print(f"[OK] 已移除自启任务：{TASK_NAME}（若壳正在运行，可手动结束对应 pythonw 进程）")
    return 0


def cmd_agents(args) -> int:
    """探测本机已安装的 agent 与可用接入机制（只读）——`continuum setup` 的前置视图。

    docs/10：普适性 = 注册表（已知）+ 嗅探（未知）+ 手动（兜底）；
    本命令展示第一层（注册表探测）结果。"""
    from continuum.agents import detect_all

    rows = detect_all()
    print("探测本机 agent（只读）：")
    found_any = False
    for d in rows:
        mark = "[+]" if d.found else "[ ]"
        if d.found:
            found_any = True
        print(f"  {mark} {d.profile.display:<14} {d.summary}")
        if d.found and d.profile.note:
            print(f"      note: {d.profile.note}")
    if not found_any:
        print("  （未发现已知宿主——可用 continuum import-workbuddy 手动导入会话文件）")
    print()
    print("接入机制说明（docs/10）：mcp=MCP 直连 / session-file=会话文件喂食 /")
    print("                      hooks=宿主钩子 / prompt=提示词片段 / proxy=LLM 代理")
    print("下一步：continuum setup 一键接入（自动配置全部发现的宿主）")
    return 0


def cmd_feed(args) -> int:
    """喂食器 CLI（真喂食的第三种触达形态）：
    --once（默认）：扫一次退出——供系统计划任务/手动调用（无常驻进程）。
    --watch：常驻循环（Ctrl+C 退出）。
    serve/shell 常驻时已自带后台喂食线程，本命令供二者都不开时的兜底。"""
    import time as _time
    from continuum.importers.feed_base import SessionFeeder

    be = _open_backend(args.db)
    feed = SessionFeeder.build_default(be)
    if feed is None:
        # 无喂食对象不是失败：计划任务场景返回 0，避免被系统误标为失败任务
        print("未发现任何已知宿主的会话目录——无喂食对象（正常退出）；"
              "可先运行 continuum agents 查看探测结果")
        be.close()
        return 0
    if args.watch:
        feed.start_background(args.interval)
        try:
            while True:
                _time.sleep(3600)
        except KeyboardInterrupt:
            feed.stop_background()
            print("\n喂食器已停止。")
    else:
        r = feed.sweep_now()
        by = " ".join(f"{k}+{v}" for k, v in r.by_host.items()) or "-"
        print(f"扫描完成: 新入库 {r.new_messages} 条 [{by}] / 文件 {r.files_scanned} 个 / "
              f"老会话标记 {r.old_files_marked} / 幂等跳过 {r.skipped_dedup}")
    be.close()
    return 0


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
    pv.add_argument("--name", default="default",
                    help="人格套名称（同名=新版本；新名称=新建一套并自动激活）")
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

    sh = sub.add_parser("shell", help="启动软件壳（本机回环图形界面，浏览器打开）")
    sh.add_argument("--db", default=argparse.SUPPRESS,
                    help="记忆库路径（也可放子命令前：continuum --db X shell）")
    sh.add_argument("--port", type=int, default=8501, help="监听端口（默认 8501，仅 127.0.0.1）")
    sh.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    sh.add_argument("--daemon", action="store_true",
                    help="后台化运行（日志落盘 ~/.continuum/shell.log）")
    sh.add_argument("--install-autostart", action="store_true",
                    help="注册登录自启（后台常驻，无需再开命令行窗口）")
    sh.add_argument("--uninstall-autostart", action="store_true", help="移除登录自启")
    sh.set_defaults(func=cmd_shell)

    ag = sub.add_parser("agents", help="探测本机 agent 与可用接入机制（只读）")
    ag.set_defaults(func=cmd_agents)

    fd = sub.add_parser("feed", help="喂食器：主动扫描宿主会话文件增量入库（真喂食）")
    fd.add_argument("--db", default=argparse.SUPPRESS,
                    help="记忆库路径（也可放子命令前：continuum --db X feed）")
    fd.add_argument("--once", action="store_true", help="扫一次退出（默认；计划任务用）")
    fd.add_argument("--watch", action="store_true", help="常驻循环扫描（Ctrl+C 退出）")
    fd.add_argument("--interval", type=float, default=120.0, help="--watch 间隔秒（默认 120）")
    fd.set_defaults(func=cmd_feed)

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
    # Windows GBK/cp936 终端下输出 ✓/⚠ 等字符会 UnicodeEncodeError——
    # 跟随终端编码但无法编码的字符降级为 ?，绝不因输出崩溃（商业化排查 2026-10-06）
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    args = build_parser().parse_args(argv)
    db = getattr(args, "db", None)
    if db:
        args.db = os.path.expanduser(db)   # 支持 ~/memory.continuum.db 形态
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
