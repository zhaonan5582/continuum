# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""多宿主接入层（docs/10）：注册表 + 只读探测 + 未来的 setup 一键安装。"""

from continuum.agents.profile import AgentProfile, DetectedAgent
from continuum.agents.registry import GLOBAL_NEVER_READ, build_registry, detect_all, is_never_read

__all__ = ["GLOBAL_NEVER_READ", "AgentProfile", "DetectedAgent", "build_registry",
           "detect_all", "is_never_read"]
