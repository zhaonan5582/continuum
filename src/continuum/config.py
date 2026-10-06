# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""用户配置（~/.continuum/config.json）—— 集中管理可调项。

当前项：
- `recall_index_mode`：**未设置 | "lite" | "fast"** —— 召回索引模式。
  - 未设置：**不预设**（用户主权）——此时按"现状行为"运行（等于 lite 的机制，
    但**语义上是"用户尚未选择"，不是我替他选的默认值**）；
  - lite：全表 LIKE + 归一 —— 存储小，检索较慢，随库增长线性劣化；
  - fast：归一列 + FTS5 trigram 索引 —— 检索快 100 倍且不随库增长劣化，存储 +78%。
  **楠哥 2026-10-07 拍板：两种都保留，选择权留给用户**（不替用户做选择）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(os.environ.get(
    "CONTINUUM_CONFIG") or (Path.home() / ".continuum" / "config.json"))

MODES = ("lite", "fast")
_CACHE: dict = {}          # {(path, mtime_ns, size) → mode}；见 get_mode 的缓存说明
# ★ 不预设 recall_index_mode：未配置即"未选择"（用户主权，2026-10-07 修正）
DEFAULTS: dict = {}


def _resolve(path: Path | None) -> Path:
    """运行时解析路径（避免 Python 默认参数在**定义时**绑定的陷阱——
    2026-10-07 实测事故：测试改模块常量无效，导致测试写脏生产配置）。
    优先级：显式参数 > 环境变量 CONTINUUM_CONFIG > ~/.continuum/config.json。
    """
    if path is not None:
        return Path(path)
    env = os.environ.get("CONTINUUM_CONFIG")
    return Path(env) if env else (Path.home() / ".continuum" / "config.json")


def load(path: Path | None = None) -> dict:
    path = _resolve(path)
    cfg = dict(DEFAULTS)
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(d, dict):
            cfg.update(d)
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return cfg


def save(cfg: dict, path: Path | None = None) -> None:
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def get_mode(path: Path | None = None) -> str | None:
    """读取召回索引模式（带 mtime 缓存）。

    **返回 None = 用户尚未选择**（不是"默认 lite"）——调用方按"现状行为"处理，
    但**不得对外宣称这是默认值**（用户主权，2026-10-07 修正）。

    缓存必要性（实测）：不加缓存时每次检索都要读配置文件（~1ms IO），
    100 次检索凭空多 100ms —— 会把 fast 模式的速度优势吃光。
    mtime 变化即失效 → **用户在使用中切换模式能被立即感知**（无需重启）。
    """
    p = _resolve(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    if _CACHE.get("key") == key:
        return _CACHE.get("mode")
    raw = load(p).get("recall_index_mode")
    mode = str(raw).lower() if raw else None
    mode = mode if mode in MODES else None
    _CACHE["key"] = key
    _CACHE["mode"] = mode
    return mode


def is_chosen(path: Path | None = None) -> bool:
    """用户是否已显式选择过索引模式（供引导语使用）。"""
    return get_mode(path) is not None


def set_mode(mode: str, path: Path | None = None) -> None:
    mode = (mode or "").lower()
    if mode not in MODES:
        raise ValueError(f"模式必须是 {MODES} 之一，得到 {mode!r}")
    cfg = load(path)
    cfg["recall_index_mode"] = mode
    save(cfg, path)
