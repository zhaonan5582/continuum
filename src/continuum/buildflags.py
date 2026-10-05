# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""构建裁剪标记（设计宪法 7：核心零宿主；docs/03：双构建）。

边界决策（v0.99b 定）：P0 只提供「构建裁剪能力」，不预设切什么。
- free 构建 = 本仓库构建（不含任何 pro 代码）；
- pro 构建 = 本仓库 + 私有 pro 仓叠加（pro 代码以 `continuum.pro` 包注入）。
运行时检测遵循「功能缺失」而非「功能上锁」——不存在可被改一行绕过的开关。
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class BuildInfo:
    edition: str          # "free" | "pro"
    pro_available: bool   # continuum.pro 包是否存在


def detect_build() -> BuildInfo:
    """检测当前构建形态。规则：
    - `CONTINUUM_EDITION` 环境变量显式指定（"free"/"pro"），用于打包期固定；
    - 否则按 continuum.pro 包是否存在自动判定。
    """
    explicit = os.environ.get("CONTINUUM_EDITION", "").strip().lower()
    pro_spec = importlib.util.find_spec("continuum.pro")
    pro_available = pro_spec is not None
    edition = explicit if explicit in ("free", "pro") else ("pro" if pro_available else "free")
    return BuildInfo(edition=edition, pro_available=pro_available)


def is_feature_available(feature: str) -> bool:
    """功能可用性查询。命名约定：
    - "core.*"        —— 永远可用（宪法：免费层是完整个人核心）
    - "pro.*"         —— 仅 pro 构建可用（模块缺失 = 功能不存在，非上锁）
    未识别的功能名一律 False（fail-closed）。
    """
    if feature.startswith("core."):
        return True
    if feature.startswith("pro."):
        return detect_build().pro_available
    return False
