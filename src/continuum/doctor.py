# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""continuum doctor——一键体检：配置 → server 可启动 → 宿主加载状态 → 生效指引。

背景（2026-10-05 楠哥定调）：不能指望所有用户都冷加载（重启宿主）。
本命令把「配置写了 / JSON 合法 / 配置的命令真的能起 / 宿主连过没有」整条链
在 5 秒内跑完，并按宿主类型给出精确的生效指引。

多语言（2026-10-06）：输出文案走 S/RELOAD_HINTS 文案表（zh/en；其他语言回退 en），
壳传入当前界面语言——体检结果随界面语言切换（楠哥：英文界面不能出中文结果）。

doctor 只读不改任何配置——体检器，不是修理工。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
WORKBUDDY_MCP_JSON = HOME / ".workbuddy" / "mcp.json"
CODEX_CONFIG_TOML = HOME / ".codex" / "config.toml"
DEFAULT_DB = HOME / ".continuum" / "memory.continuum.db"

# 各宿主配置生效方式（产品契约：新宿主适配器必须补充此字段）
RELOAD_HINTS = {
    "zh": {
        "workbuddy": "冷加载型：重启 WorkBuddy 生效（运行中保存 mcp.json 不会热加载）。",
        "codex": "冷加载型：重启 codex 生效（MCP server 随启动拉起，起不来不阻塞 codex）。",
        "claude-code": "热加载型：hooks 每次触发都是新进程，改完配置即时生效，无需重启。",
    },
    "en": {
        "workbuddy": "Cold-load: takes effect after restarting WorkBuddy (saving mcp.json at runtime does not hot-reload).",
        "codex": "Cold-load: takes effect after restarting codex (MCP server spawns at startup; failure does not block codex).",
        "claude-code": "Hot-load: each hook fires a fresh process — config changes apply instantly, no restart needed.",
    },
}
RELOAD_PROXY = {
    "zh": "  - 代理模式: Continuum 独立 HTTP 进程（continuum proxy），与宿主生命周期解耦，宿主只需 base_url 指向它",
    "en": "  - Proxy mode: Continuum runs as an independent HTTP process (continuum proxy), decoupled from the host lifecycle - just point the host base_url at it",
}

S = {
    "zh": {
        "title": "continuum doctor — {now}",
        "sep": "：",
        "s1": "[1/3] 配置文件",
        "wb_ok": "WorkBuddy（mcp.json）：continuum 条目存在",
        "wb_missing": "WorkBuddy（mcp.json）：无 continuum 条目",
        "wb_broken": "WorkBuddy（mcp.json）：JSON 解析失败 {err}",
        "wb_absent": "WorkBuddy（mcp.json）：文件不存在",
        "cx_ok": "codex（config.toml）：[mcp_servers.continuum] 存在",
        "cx_missing": "codex（config.toml）：无 continuum 条目",
        "cx_broken": "codex（config.toml）：TOML 解析失败 {err}",
        "cx_absent": "codex（config.toml）：文件不存在（未接入 codex）",
        "s2": "[2/3] 记忆库",
        "db_missing": "不存在（首次会由 serve 自动创建）",
        "db_live": "有活动连接（宿主已加载）",
        "db_idle": "当前无活动连接",
        "s3": "[3/3] server 自检（按 WorkBuddy 配置的确切命令拉起）",
        "hs_ok": "server 握手（配置确切命令）：initialize OK，{ms}",
        "hs_fail": "server 握手失败（配置确切命令）：{err}",
        "spawn_fail": "server 启动失败（配置确切命令）：{err}",
        "no_host": "没有任何宿主配置了 continuum——先接入（README：方式 A / 方式 B）",
        "hint_title": "生效方式（按宿主）：",
    },
    "en": {
        "title": "continuum doctor — {now}",
        "sep": ": ",
        "s1": "[1/3] Config files",
        "wb_ok": "WorkBuddy (mcp.json): continuum entry present",
        "wb_missing": "WorkBuddy (mcp.json): no continuum entry",
        "wb_broken": "WorkBuddy (mcp.json): JSON parse failed {err}",
        "wb_absent": "WorkBuddy (mcp.json): file not found",
        "cx_ok": "codex (config.toml): [mcp_servers.continuum] present",
        "cx_missing": "codex (config.toml): no continuum entry",
        "cx_broken": "codex (config.toml): TOML parse failed {err}",
        "cx_absent": "codex (config.toml): file not found (codex not wired)",
        "s2": "[2/3] Memory store",
        "db_missing": "missing (auto-created by first serve)",
        "db_live": "active connection present (host loaded)",
        "db_idle": "no active connection right now",
        "s3": "[3/3] Server self-check (spawning with the exact command from WorkBuddy config)",
        "hs_ok": "server handshake (exact config command): initialize OK, {ms}",
        "hs_fail": "server handshake failed (exact config command): {err}",
        "spawn_fail": "server failed to start (exact config command): {err}",
        "no_host": "No host has continuum configured yet - wire it up first (README: Method A / B)",
        "hint_title": "How config changes take effect (per host):",
    },
}


def _S(lang: str) -> dict:
    return S.get(lang) or S["en"]


def _hint(lang: str) -> dict:
    return RELOAD_HINTS.get(lang) or RELOAD_HINTS["en"]


def _check_entry(name: str, ok: bool, detail: str = "", lang: str = "zh") -> bool:
    mark = "  ✓ " if ok else "  ✗ "
    sep = "：" if lang == "zh" else ": "
    print(mark + name + (sep + detail if detail else ""))
    return ok


def check_config_files(lang: str = "zh") -> dict:
    """检查各宿主配置文件里的 continuum 条目。返回 {host: command_list}。"""
    T = _S(lang)
    found: dict[str, list[str]] = {}
    print(T["s1"])

    if WORKBUDDY_MCP_JSON.exists():
        try:
            data = json.loads(WORKBUDDY_MCP_JSON.read_text(encoding="utf-8"))
            entry = (data.get("mcpServers") or {}).get("continuum")
            if entry and entry.get("command"):
                found["workbuddy"] = [entry["command"], *entry.get("args", [])]
                _check_entry(T["wb_ok"], True, lang=lang)
            else:
                _check_entry(T["wb_missing"], False, lang=lang)
        except (json.JSONDecodeError, OSError) as e:
            _check_entry(T["wb_broken"].format(err=e), False, lang=lang)
    else:
        _check_entry(T["wb_absent"], False, lang=lang)

    if CODEX_CONFIG_TOML.exists():
        try:
            import tomllib
            with CODEX_CONFIG_TOML.open("rb") as f:
                data = tomllib.load(f)
            entry = (data.get("mcp_servers") or {}).get("continuum")
            if entry and entry.get("command"):
                found["codex"] = [entry["command"], *entry.get("args", [])]
                _check_entry(T["cx_ok"], True, lang=lang)
            else:
                _check_entry(T["cx_missing"], False, lang=lang)
        except (tomllib.TOMLDecodeError, OSError) as e:
            _check_entry(T["cx_broken"].format(err=e), False, lang=lang)
    else:
        _check_entry(T["cx_absent"], False, lang=lang)
    return found


def check_db(db_path: Path = DEFAULT_DB, lang: str = "zh") -> bool:
    T = _S(lang)
    print(T["s2"])
    if not db_path.exists():
        return _check_entry(str(db_path), False, T["db_missing"], lang=lang)
    wal = db_path.with_name(db_path.name + "-wal")
    shm = db_path.with_name(db_path.name + "-shm")
    live = wal.exists() or shm.exists()
    detail = T["db_live"] if live else T["db_idle"]
    return _check_entry(str(db_path), True, detail, lang=lang)


def check_server_live(command: list[str], env_extra: dict[str, str] | None = None,
                      timeout: float = 30.0, lang: str = "zh") -> bool:
    """用配置里的确切命令拉起 server，做一次 initialize 握手（模拟宿主真实行为）。"""
    T = _S(lang)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env_extra or {})}
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, text=True, encoding="utf-8")
    except OSError as e:
        return _check_entry(T["spawn_fail"].format(err=str(e)), False, lang=lang)
    try:
        assert proc.stdin and proc.stdout
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "continuum-doctor", "version": "0.0.1"}},
        }) + "\n")
        proc.stdin.flush()
        t0 = time.monotonic()
        line = proc.stdout.readline()
        dt = (time.monotonic() - t0) * 1000
        resp = json.loads(line)
        name = resp["result"]["serverInfo"]["name"]
        return _check_entry(T["hs_ok"].format(ms=f"{dt:.0f}"), name == "continuum", lang=lang)
    except Exception as e:  # noqa: BLE001 - 体检器要兜住一切失败
        return _check_entry(T["hs_fail"].format(err=f"{type(e).__name__}: {e}"), False, lang=lang)
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


def run_doctor(db_path: Path = DEFAULT_DB, lang: str = "zh") -> int:
    T = _S(lang)
    print(T["title"].format(now=time.strftime("%Y-%m-%d %H:%M:%S")))
    configs = check_config_files(lang)
    check_db(db_path, lang=lang)
    print(T["s3"])
    ok = True
    if "workbuddy" in configs:
        entry_env: dict[str, str] = {}
        try:
            data = json.loads(WORKBUDDY_MCP_JSON.read_text(encoding="utf-8"))
            entry_env = (data.get("mcpServers") or {}).get("continuum", {}).get("env") or {}
        except (json.JSONDecodeError, OSError):
            pass
        ok = check_server_live(configs["workbuddy"], entry_env, lang=lang)
    elif "codex" in configs:
        ok = check_server_live(configs["codex"], lang=lang)
    else:
        print("  ⚠ " + T["no_host"])

    hints = _hint(lang)
    print("\n" + T["hint_title"])
    for host in ("workbuddy", "codex", "claude-code"):
        print(f"  - {host}: {hints[host]}")
    print(RELOAD_PROXY.get(lang) or RELOAD_PROXY["en"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run_doctor())
