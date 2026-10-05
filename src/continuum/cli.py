# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Continuum CLI（零依赖，argparse）。

子命令：
- serve            启动 MCP server（P1+）
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


# ---------- serve ----------

def cmd_serve(args) -> int:
    """启动 MCP server（stdio）。mcp 未安装时给出清晰指引。"""
    try:
        from continuum.server.mcp import build_mcp_server
    except ImportError:
        print("MCP 支持未安装：pip install 'mcp>=1.0'（可选依赖，核心零依赖不受影响）")
        return 1
    srv = _open_server(args.db)
    mcp = build_mcp_server(srv)
    mcp.run()          # stdio
    return 0


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

    sv = sub.add_parser("serve", help="启动 MCP server（stdio）")
    sv.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
