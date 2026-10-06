# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""`continuum setup` —— 一键安装（docs/10 §4 目标形态）。

默认**全自动、零交互**：探测 → 自动接入全部发现的宿主 → 报告 → 验证提示。
用户只需：`pip install continuum-core && continuum setup`。

安全约束（代码强制）：
- **只增不改**：只写自己的 `continuum` 条目，绝不改动用户既有配置；
- **写前备份**（<file>.bak-continuum-<时间戳>）；
- **幂等**：已存在条目 → 跳过并报告；
- **单点失败降级**：某宿主写失败不影响其他（逐宿主 try）。
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SERVER_KEY = "continuum"


@dataclass
class SetupAction:
    host: str
    config_path: Path | None
    status: str          # created / updated / exists / skipped / error / manual
    detail: str = ""


def build_server_entry(db_path: str, python_exe: str | None = None) -> dict:
    """标准 MCP server 条目（各宿主格式相近；env 注入 PYTHONPATH 以支持源码运行）。"""
    exe = python_exe or sys.executable
    src_root = str(Path(__file__).resolve().parent.parent)   # .../src
    return {
        "command": exe,
        "args": ["-m", "continuum.cli", "serve", "--db", db_path],
        "env": {"PYTHONPATH": src_root},
    }


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    bak = path.with_name(path.name + ".bak-continuum-" + datetime.now().strftime("%Y%m%d%H%M%S"))
    try:
        shutil.copy2(path, bak)
        return bak
    except OSError:
        return None


def write_json_mcp(path: Path, entry: dict, host: str) -> SetupAction:
    """写 JSON 形态的 MCP 配置（mcp.json / .claude.json）。host 由调用方显式传入
    （从路径猜会得到 ".workbuddy" 这类目录名，2026-10-07 测试实锤）。"""
    try:
        data: dict = {}
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                return SetupAction(host=host, config_path=path, status="error",
                                   detail="配置顶层不是 JSON object，跳过（请人工检查）")
        servers = data.get("mcpServers")
        # 兼容 mcpServers 被写成空数组的情况（实测见 CC 的 .claude.json）
        if isinstance(servers, list):
            servers = {} if not servers else None
            if servers is None:
                return SetupAction(host=host, config_path=path, status="error",
                                   detail="mcpServers 为非空数组，格式不明，跳过")
        if not isinstance(servers, dict):
            servers = {}
        if SERVER_KEY in servers:
            return SetupAction(host=host, config_path=path, status="exists",
                               detail="已有 continuum 条目（未改动）")
        servers[SERVER_KEY] = entry
        data["mcpServers"] = servers
        bak = _backup(path) if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return SetupAction(host=host, config_path=path,
                           status="updated" if bak else "created",
                           detail=f"已写入 mcpServers.continuum"
                                  + (f"（备份 {bak.name}）" if bak else ""))
    except (OSError, ValueError) as e:
        return SetupAction(host=host, config_path=path, status="error", detail=f"{type(e).__name__}: {e}")


def write_toml_mcp(path: Path, entry: dict, host: str) -> SetupAction:
    """写 TOML 形态（codex config.toml）——追加段落，不动既有内容。"""
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if f"[mcp_servers.{SERVER_KEY}]" in text:
            return SetupAction(host=host, config_path=path, status="exists",
                               detail="已有 [mcp_servers.continuum] 段（未改动）")
        exe = entry["command"].replace("\\", "\\\\")
        db = entry["args"][entry["args"].index("--db") + 1].replace("\\", "\\\\")
        py_path = entry["env"].get("PYTHONPATH", "").replace("\\", "\\\\")
        block = (
            f"\n# Continuum：跨 agent 持久记忆层（{datetime.now():%Y-%m-%d %H:%M} 由 continuum setup 写入；"
            f"删除本段即回滚）\n"
            f"[mcp_servers.{SERVER_KEY}]\n"
            f"command = '{exe}'\n"
            f"args = [\"-m\", \"continuum.cli\", \"serve\", \"--db\", '{db}']\n"
            f"env_vars = []\n"
            f"startup_timeout_sec = 60\n"
        )
        bak = _backup(path) if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
        return SetupAction(host=host, config_path=path, status="updated",
                           detail="已追加 [mcp_servers.continuum]"
                                  + (f"（备份 {bak.name}）" if bak else "")
                                  + (f"；PYTHONPATH={py_path}" if py_path else ""))
    except (OSError, ValueError) as e:
        return SetupAction(host=host, config_path=path, status="error", detail=f"{type(e).__name__}: {e}")


def run_setup(db_path: str, *, python_exe: str | None = None,
              dry_run: bool = False, home: Path | None = None
              ) -> tuple[list[SetupAction], list[str]]:
    """执行一键安装。返回 (逐宿主动作, 用户需知提示)。home 可覆盖（测试隔离用）。"""
    from continuum.agents import detect_all

    entry = build_server_entry(db_path, python_exe)
    actions: list[SetupAction] = []
    notes: list[str] = []

    for d in detect_all(home):
        prof = d.profile
        if not d.found:
            continue
        if prof.mcp_config is None:
            actions.append(SetupAction(host=prof.key, config_path=None, status="manual",
                                       detail="无标准 MCP 配置位置——请参考 README 手动接入"))
            continue
        if not prof.mcp_config.parent.exists() and not prof.mcp_config.exists():
            actions.append(SetupAction(host=prof.key, config_path=prof.mcp_config,
                                       status="skipped", detail="配置目录不存在（宿主可能未初始化）"))
            continue
        if dry_run:
            actions.append(SetupAction(host=prof.key, config_path=prof.mcp_config,
                                       status="manual", detail="[dry-run] 将写入 continuum 条目"))
            continue
        if prof.mcp_config_format == "toml":
            actions.append(write_toml_mcp(prof.mcp_config, entry, prof.key))
        else:
            actions.append(write_json_mcp(prof.mcp_config, entry, prof.key))
        if prof.note:
            notes.append(f"{prof.display}: {prof.note}")

    notes.append("喂食：serve/shell 常驻时自动采集各宿主会话（也可计划任务跑 continuum feed --once）")
    return actions, notes
