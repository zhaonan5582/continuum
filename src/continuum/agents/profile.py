# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""AgentProfile —— 多宿主接入的注册表契约（docs/10 第一层：已知宿主精确适配）。

设计原则：
- **核心零宿主**：本模块只描述"如何探测/在哪里找会话文件"，不含任何宿主专有逻辑；
  实际解析函数在 importers 层（parser_key 间接引用，避免循环依赖）。
- **只读探测**：detect 只做路径存在性检查与文件计数，绝不写、绝不联网。
- **可扩展**：新增宿主 = 加一个 AgentProfile（或靠 docs/10 第二层嗅探）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentProfile:
    """一个宿主的接入画像（探测 + 各机制落点）。"""

    key: str                              # "workbuddy" / "codex" / "claude-code"
    display: str                          # 展示名（向导/agents 命令用）
    detect_paths: tuple[Path, ...]        # 探测路径（任一存在 → 认为已安装）
    session_roots: tuple[Path, ...]       # 喂食扫描根（机制 M2）
    parser_key: str                       # 解析策略标识（importers 层实现）
    format_kind: str = "unknown"          # jsonl / json / sqlite / zstd / markdown / vscdb
    support_level: str = "profile_only"   # parser_ready（有解析器）/ profile_only（仅探测）/ planned
    never_read: tuple[str, ...] = ()      # 安全红线：永不读取的文件名模式（凭据/密钥/配置）
    mcp_config: Path | None = None        # MCP 配置文件（机制 M1 自动写落点）
    mcp_config_format: str = "json"       # "json"（mcp.json）或 "toml"（config.toml）
    hooks_supported: bool = False         # 机制 M3
    prompt_file: Path | None = None       # 机制 M5（AGENTS.md / CLAUDE.md）
    note: str = ""                        # 用户需知的额外一步（如"面板点 Trust"）
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DetectedAgent:
    """探测结果（只读快照）。"""

    profile: AgentProfile
    found: bool
    session_count: int                    # 会话文件数（可用性指标；-1=未扫描）
    mechanisms: tuple[str, ...]           # 可用机制标识（mcp/session-file/hooks/prompt/proxy）

    @property
    def summary(self) -> str:
        if not self.found:
            return "未发现"
        how = " + ".join(self.mechanisms) or "（无可用机制）"
        return f"会话文件 {self.session_count} 个 | 机制: {how}"
