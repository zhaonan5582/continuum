# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""宿主注册表与只读探测（docs/10 三层普适性的第一层）。

覆盖范围：35+ 主流 AI coding agent（含国外主力：Claude Code / Codex CLI / Cursor /
Copilot / Cline / Roo / Kilo / Continue / Aider / Gemini CLI / OpenCode / Goose /
Crush / Windsurf / Zed / Amp / OpenHands / Trae / Qwen Code / Kiro / Grok / Kimi …）。

数据来源（2026-10-06 网络调研，docs/10 附录有完整对照表与出处）：
- deja-vu registry（35 agent 位置与格式）
- claude-code-history-viewer ×2（31 agent）
- agentscrub（凭据安全边界清单）

**support_level 语义**（诚实标注，不假装全能）：
- parser_ready  : 本仓库已有解析器，喂食可直接用
- profile_only  : 仅探测（能发现、能告知用户），解析器待补
- planned       : 已知位置与格式，排在路线图（如 zstd 需压缩库）

**安全红线（永不读取）**：各宿主的凭据/密钥/配置文件——只读会话记录，
且 `never_read` 列出的模式在任何路径下都跳过（隐私白皮书承诺的可验证实现）。
"""

from __future__ import annotations

import glob
from pathlib import Path

from continuum.agents.profile import AgentProfile, DetectedAgent

# 任何 profile 在任何情况下都不得读取的文件（凭据/密钥/配置）——安全红线
GLOBAL_NEVER_READ: tuple[str, ...] = (
    "auth.json", ".credentials.json", "credentials.json", "secrets.json",
    ".env", "settings.json", "settings.local.json", "mcp.json",
    "mcp-config.json", "mcp_config.json", "mcp-auth.json", "oauth_creds.json",
    "mcp-oauth-tokens.json", "google_accounts.json", "trustedFolders.json",
    "installation_id", "user_id", "config.toml", "config.yaml", "config.yml",
    "config.json", "config.ts", "cline_mcp_settings.json", "globalState.json",
    "trustedFolders.json", "cap_sid", "*.key", "*.pem", "*_token*",
)


def _p(base: Path, *parts: str) -> Path:
    return base.joinpath(*parts)


def build_registry(home: Path | None = None) -> tuple[AgentProfile, ...]:
    """全量注册表（新增宿主在此追加）。home 可覆盖（测试用），默认 ~。"""
    h = home if home is not None else Path.home()
    p = lambda *a: _p(h, *a)  # noqa: E731

    def A(**kw) -> AgentProfile:
        kw.setdefault("never_read", GLOBAL_NEVER_READ)
        return AgentProfile(**kw)

    return (
        # ---------- Tier 1：本仓库已实测 / 已有解析器 ----------
        A(key="claude-code", display="Claude Code",
          detect_paths=(p(".claude"),),
          session_roots=(p(".claude", "projects"),),
          parser_key="cc_message", format_kind="jsonl", support_level="parser_ready",
          mcp_config=p(".claude.json"), mcp_config_format="json",
          hooks_supported=True, prompt_file=p(".claude", "CLAUDE.md")),
        A(key="codex", display="codex CLI",
          detect_paths=(p(".codex"),),
          session_roots=(p(".codex", "sessions"), p(".codex", "archived_sessions")),
          parser_key="codex_rollout", format_kind="jsonl", support_level="parser_ready",
          mcp_config=p(".codex", "config.toml"), mcp_config_format="toml",
          prompt_file=p(".codex", "AGENTS.md"),
          note="MCP 配置写入 config.toml 后需重启 codex 生效"),
        A(key="workbuddy", display="WorkBuddy",
          detect_paths=(p(".workbuddy"),),
          session_roots=(p(".workbuddy", "projects"),),
          parser_key="workbuddy_dialog", format_kind="jsonl", support_level="parser_ready",
          mcp_config=p(".workbuddy", "mcp.json"), mcp_config_format="json",
          prompt_file=p(".workbuddy", "AGENTS.md"),
          note="需在 WorkBuddy 设置面板点一次 Trust 激活 MCP"),

        # ---------- Tier 1：JSONL 族（解析器同族，可快速点亮） ----------
        A(key="gemini-cli", display="Gemini CLI",
          detect_paths=(p(".gemini"),),
          session_roots=(p(".gemini", "tmp"), p(".gemini", "history")),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only"),
        A(key="qwen-code", display="Qwen Code",
          detect_paths=(p(".qwen"),),
          session_roots=(p(".qwen", "projects"),),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only"),
        A(key="copilot-cli", display="GitHub Copilot CLI",
          detect_paths=(p(".copilot"),),
          session_roots=(p(".copilot", "session-state"),),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only"),
        A(key="grok-cli", display="Grok CLI",
          detect_paths=(p(".grok"),),
          session_roots=(p(".grok", "sessions"),),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only"),
        A(key="kimi", display="Kimi CLI",
          detect_paths=(p(".kimi"),),
          session_roots=(p(".kimi", "sessions"),),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only"),
        A(key="kiro", display="Kiro",
          detect_paths=(p(".kiro"),),
          session_roots=(p(".kiro", "sessions"),),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only"),
        A(key="codebuddy-code", display="CodeBuddy Code",
          detect_paths=(p(".codebuddy"),),
          session_roots=(p(".codebuddy", "projects"),),
          parser_key="cc_message", format_kind="jsonl", support_level="profile_only",
          note="Claude Code fork 格式"),
        A(key="open-interpreter", display="Open Interpreter",
          detect_paths=(p(".openinterpreter"),),
          session_roots=(p(".openinterpreter", "sessions"),),
          parser_key="codex_rollout", format_kind="jsonl", support_level="profile_only",
          note="复用 Codex rollout 解析器"),
        A(key="pi", display="Pi / oh-my-pi",
          detect_paths=(p(".pi"), p(".omp")),
          session_roots=(p(".pi", "agent", "sessions"), p(".omp", "agent", "sessions")),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only"),
        A(key="antigravity", display="Google Antigravity",
          detect_paths=(p(".gemini", "antigravity"), p(".antigravity-server")),
          session_roots=(p(".gemini"),),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only"),

        # ---------- Tier 1：JSON 族（每会话一文件） ----------
        A(key="continue", display="Continue.dev",
          detect_paths=(p(".continue"),),
          session_roots=(p(".continue", "sessions"),),
          parser_key="json_session", format_kind="json", support_level="profile_only"),
        A(key="cline", display="Cline",
          detect_paths=(p(".cline"),),
          session_roots=(p(".cline", "data", "sessions"),),
          parser_key="json_session", format_kind="json", support_level="profile_only"),
        A(key="roo-code", display="Roo Code",
          detect_paths=(p(".roo"),),
          session_roots=(),
          parser_key="json_session", format_kind="json", support_level="profile_only",
          note="历史在 VS Code globalStorage（rooveterinaryinc.roo-cline/tasks）——需指定路径"),
        A(key="kilo-code", display="Kilo Code",
          detect_paths=(p(".kilo"), p(".local", "share", "kilo")),
          session_roots=(p(".local", "share", "kilo"),),
          parser_key="json_session", format_kind="json", support_level="profile_only"),
        A(key="amp", display="Amp (Sourcegraph)",
          detect_paths=(p(".local", "share", "amp"),),
          session_roots=(p(".local", "share", "amp", "threads"),),
          parser_key="json_session", format_kind="json", support_level="profile_only"),
        A(key="openhands", display="OpenHands",
          detect_paths=(p(".openhands"),),
          session_roots=(p(".openhands", "sessions"),),
          parser_key="json_session", format_kind="json", support_level="profile_only"),
        A(key="mistral-vibe", display="Mistral Vibe",
          detect_paths=(p(".vibe"),),
          session_roots=(p(".vibe", "logs", "session"),),
          parser_key="json_session", format_kind="json", support_level="profile_only"),
        A(key="aider", display="Aider",
          detect_paths=(p(".aider"), p(".aider.conf.yml")),
          session_roots=(),      # 记录在项目目录（.aider.chat.history.md），需用户指定
          parser_key="markdown_log", format_kind="markdown", support_level="profile_only",
          note="历史文件在项目目录（<repo>/.aider.chat.history.md）——需指定仓库路径"),

        # ---------- Tier 1：SQLite 族（需 sqlite 读取器，核心已具备） ----------
        A(key="hermes", display="hermes",
          detect_paths=(p(".hermes"),),
          session_roots=(p(".hermes"),),
          parser_key="sqlite_sessions", format_kind="sqlite", support_level="profile_only",
          note="实测为 SQLite（~/.hermes/state.db）"),
        A(key="goose", display="Goose (Block)",
          detect_paths=(p(".local", "share", "goose"),),
          session_roots=(p(".local", "share", "goose", "sessions"),),
          parser_key="sqlite_sessions", format_kind="sqlite", support_level="profile_only"),
        A(key="opencode", display="OpenCode",
          detect_paths=(p(".local", "share", "opencode"), p(".config", "opencode")),
          session_roots=(p(".local", "share", "opencode"),),
          parser_key="sqlite_sessions", format_kind="sqlite", support_level="profile_only"),
        A(key="crush", display="Crush (Charm)",
          detect_paths=(p(".local", "share", "crush"), p(".config", "crush")),
          session_roots=(p(".local", "share", "crush"),),
          parser_key="sqlite_sessions", format_kind="sqlite", support_level="profile_only",
          note="历史为每项目 .crush/crush.db——需指定仓库路径"),
        A(key="zed", display="Zed",
          detect_paths=(p(".local", "share", "zed"),),
          session_roots=(p(".local", "share", "zed"),),
          parser_key="sqlite_sessions", format_kind="sqlite", support_level="profile_only"),
        A(key="forgecode", display="ForgeCode",
          detect_paths=(p(".forge"),),
          session_roots=(p(".forge"),),
          parser_key="sqlite_sessions", format_kind="sqlite", support_level="profile_only"),
        A(key="z-code", display="Z Code (Z.ai)",
          detect_paths=(p(".zcode"),),
          session_roots=(p(".zcode",),),
          parser_key="sqlite_sessions", format_kind="sqlite", support_level="profile_only"),
        A(key="llm", display="llm (datasette)",
          detect_paths=(p(".config", "io.datasette.llm"),),
          session_roots=(p(".config", "io.datasette.llm"),),
          parser_key="sqlite_sessions", format_kind="sqlite", support_level="profile_only"),

        # ---------- IDE 系（历史在 VS Code / 应用存储，路径因平台而异） ----------
        A(key="cursor", display="Cursor",
          detect_paths=(p(".cursor"),),
          session_roots=(p(".cursor", "projects"),),
          parser_key="jsonl_generic", format_kind="jsonl", support_level="profile_only",
          note="CLI 版为 ~/.cursor/projects/**/agent-transcripts/*.jsonl；IDE 版在 state.vscdb（SQLite）"),
        A(key="windsurf", display="Windsurf",
          detect_paths=(p(".codeium", "windsurf"), p(".windsurf")),
          session_roots=(p(".codeium", "windsurf"),),
          parser_key="vscdb", format_kind="sqlite", support_level="planned",
          note="Cascade 历史在 VS Code workspaceStorage 的 state.vscdb"),
        A(key="trae", display="Trae",
          detect_paths=(p(".trae"),),
          session_roots=(),
          parser_key="vscdb", format_kind="sqlite", support_level="planned",
          note="历史在应用 User/workspaceStorage（启发式，未官方公开）"),
        A(key="vscode-copilot", display="VS Code Copilot Chat",
          detect_paths=(p(".vscode"),),
          session_roots=(),
          parser_key="vscdb", format_kind="sqlite", support_level="planned",
          note="需指定 workspaceStorage 路径（跨平台位置不同）"),

        # ---------- 压缩格式（需 zstd 支持，标准库 3.14+ 或有第三方） ----------
        A(key="deepseek-harness", display="DeepSeek Harness",
          detect_paths=(p(".dsh"),),
          session_roots=(p(".dsh", "sessions"),),
          parser_key="zstd_jsonl", format_kind="zstd", support_level="planned",
          note="zstd 压缩 JSONL（需压缩库支持）"),
        A(key="reasonix", display="Reasonix",
          detect_paths=(p(".reasonix"),),
          session_roots=(p(".reasonix"),),
          parser_key="zstd_jsonl", format_kind="zstd", support_level="planned"),
    )


_EXT_BY_FORMAT = {
    "jsonl": ("**/*.jsonl",),
    "json": ("**/*.json", "*.json"),
    "sqlite": ("**/*.db", "*.db", "**/*.sqlite", "*.sqlite"),
    "vscdb": ("**/state.vscdb",),
    "zstd": ("**/*.zstd", "**/*.jsonl.zstd"),
    "markdown": ("**/*.md",),
}


def _count_sessions(roots: tuple[Path, ...], format_kind: str = "jsonl",
                    cap: int = 100_000) -> int:
    """只读统计会话文件数（按格式只数对应扩展名，避免把配置/缓存算进来）。"""
    patterns = _EXT_BY_FORMAT.get(format_kind, ("**/*.jsonl", "**/*.json"))
    n = 0
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in patterns:
            for _ in glob.iglob(str(root / pattern), recursive=True):
                n += 1
                if n >= cap:
                    return n
    return n


def _mechanisms(a: AgentProfile, session_count: int) -> tuple[str, ...]:
    out: list[str] = []
    if a.mcp_config is not None:
        out.append("mcp")
    if session_count > 0:
        out.append("session-file")
    if a.hooks_supported:
        out.append("hooks")
    if a.prompt_file is not None:
        out.append("prompt")
    out.append("proxy")                # 代理与宿主无关（任何走 HTTP API 的宿主都可用）
    return tuple(dict.fromkeys(out))


def detect_all(home: Path | None = None) -> list[DetectedAgent]:
    """探测本机全部注册宿主（只读）。"""
    out: list[DetectedAgent] = []
    for a in build_registry(home):
        found = any(x.exists() for x in a.detect_paths)
        n = _count_sessions(a.session_roots, a.format_kind) if found else -1
        out.append(DetectedAgent(profile=a, found=found, session_count=n,
                                 mechanisms=_mechanisms(a, max(n, 0))))
    return out


def is_never_read(path: Path | str) -> bool:
    """安全红线：该路径是否属于'永不读取'（凭据/密钥/配置）——喂食器必须调用。"""
    import fnmatch
    name = Path(path).name.lower()
    for pat in GLOBAL_NEVER_READ:
        if fnmatch.fnmatch(name, pat.lower()):
            return True
    return False
