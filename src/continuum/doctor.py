# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""continuum doctor——一键体检：配置 → server 可启动 → 宿主加载状态 → 生效指引。

背景（2026-10-05 楠哥定调）：不能指望所有用户都冷加载（重启宿主）。
本命令把「配置写了 / JSON 合法 / 配置的命令真的能起 / 宿主连过没有」整条链
在 5 秒内跑完，并按宿主类型给出精确的生效指引：

- MCP 冷加载型（WorkBuddy / codex）：改配置后需重启宿主一次，此后常驻。
- hooks 型（Claude Code 档位 B）：每次钩子触发都是新进程，改完即时生效（天然热加载）。
- 代理型（方式 B）：Continuum 独立 HTTP 进程，与宿主生命周期解耦。

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
    "workbuddy": "冷加载型：重启 WorkBuddy 生效（运行中保存 mcp.json 不会热加载）。",
    "codex": "冷加载型：重启 codex 生效（MCP server 随启动拉起，起不来不阻塞 codex）。",
    "claude-code": "热加载型：hooks 每次触发都是新进程，改完配置即时生效，无需重启。",
}

_OK = "  ✓ "
_WARN = "  ⚠ "
_BAD = "  ✗ "


def _check_entry(name: str, ok: bool, detail: str = "") -> bool:
    print((_OK if ok else _BAD) + name + (f"：{detail}" if detail else ""))
    return ok


def check_config_files() -> dict:
    """检查各宿主配置文件里的 continuum 条目。返回 {host: command_list}。"""
    found: dict[str, list[str]] = {}
    print("[1/3] 配置文件")

    if WORKBUDDY_MCP_JSON.exists():
        try:
            data = json.loads(WORKBUDDY_MCP_JSON.read_text(encoding="utf-8"))
            entry = (data.get("mcpServers") or {}).get("continuum")
            if entry and entry.get("command"):
                found["workbuddy"] = [entry["command"], *entry.get("args", [])]
                _check_entry(f"WorkBuddy ({WORKBUDDY_MCP_JSON.name})", True, "continuum 条目存在")
            else:
                _check_entry(f"WorkBuddy ({WORKBUDDY_MCP_JSON.name})", False, "无 continuum 条目")
        except (json.JSONDecodeError, OSError) as e:
            _check_entry(f"WorkBuddy ({WORKBUDDY_MCP_JSON.name})", False, f"JSON 解析失败 {e}")
    else:
        _check_entry(f"WorkBuddy ({WORKBUDDY_MCP_JSON.name})", False, "文件不存在")

    if CODEX_CONFIG_TOML.exists():
        try:
            import tomllib
            with CODEX_CONFIG_TOML.open("rb") as f:
                data = tomllib.load(f)
            entry = (data.get("mcp_servers") or {}).get("continuum")
            if entry and entry.get("command"):
                found["codex"] = [entry["command"], *entry.get("args", [])]
                _check_entry(f"codex ({CODEX_CONFIG_TOML.name})", True, "[mcp_servers.continuum] 存在")
            else:
                _check_entry(f"codex ({CODEX_CONFIG_TOML.name})", False, "无 continuum 条目")
        except (tomllib.TOMLDecodeError, OSError) as e:
            _check_entry(f"codex ({CODEX_CONFIG_TOML.name})", False, f"TOML 解析失败 {e}")
    else:
        _check_entry(f"codex ({CODEX_CONFIG_TOML.name})", False, "文件不存在（未接入 codex）")
    return found


def check_db(db_path: Path = DEFAULT_DB) -> bool:
    print("[2/3] 记忆库")
    if not db_path.exists():
        return _check_entry(str(db_path), False, "不存在（首次会由 serve 自动创建）")
    wal = db_path.with_name(db_path.name + "-wal")
    shm = db_path.with_name(db_path.name + "-shm")
    live = wal.exists() or shm.exists()
    detail = "有活动连接（宿主已加载）" if live else "当前无活动连接"
    return _check_entry(str(db_path), True, detail)


def check_server_live(command: list[str], env_extra: dict[str, str] | None = None,
                      timeout: float = 30.0) -> bool:
    """用配置里的确切命令拉起 server，做一次 initialize 握手（模拟宿主真实行为）。"""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env_extra or {})}
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, text=True, encoding="utf-8")
    except OSError as e:
        return _check_entry("server 启动（配置确切命令）", False, str(e))
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
        return _check_entry("server 握手（配置确切命令）", name == "continuum",
                            f"initialize OK，{dt:.0f}ms")
    except Exception as e:  # noqa: BLE001 - 体检器要兜住一切失败
        return _check_entry("server 握手（配置确切命令）", False, f"{type(e).__name__}: {e}")
    finally:
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


def run_doctor(db_path: Path = DEFAULT_DB) -> int:
    print(f"continuum doctor — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    configs = check_config_files()
    check_db(db_path)
    print("[3/3] server 自检（按 WorkBuddy 配置的确切命令拉起）")
    ok = True
    if "workbuddy" in configs:
        entry_env: dict[str, str] = {}
        try:
            data = json.loads(WORKBUDDY_MCP_JSON.read_text(encoding="utf-8"))
            entry_env = (data.get("mcpServers") or {}).get("continuum", {}).get("env") or {}
        except (json.JSONDecodeError, OSError):
            pass
        ok = check_server_live(configs["workbuddy"], entry_env)
    elif "codex" in configs:
        ok = check_server_live(configs["codex"])
    else:
        print(_WARN + "没有任何宿主配置了 continuum——先接入（README：方式 A / 方式 B）")

    print("\n生效方式（按宿主）：")
    for host in ("workbuddy", "codex", "claude-code"):
        print(f"  - {host}: {RELOAD_HINTS[host]}")
    print("  - 代理模式: Continuum 独立 HTTP 进程（continuum proxy），与宿主生命周期解耦，"
          "宿主只需 base_url 指向它")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run_doctor())
