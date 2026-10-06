# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""License 管理（本地骨架）。

状态（2026-10-06 楠哥拍板的定价结构）：
- 免费版：完整核心 + 单宿主隔离 + BYOK——永不联网
- 本地 Pro（买断）：跨宿主汇聚解锁
- Pro + 云（订阅）：本地 Pro + 托管判决网关额度 + 云同步

诚实边界：
- 当前校验 = 本地格式 + 校验位骨架（防手滑输错），**真签名校验随商业化接入**；
- license.json 存于 ~/.continuum/license.json（本机文件，随库同目录）；
- 「Pro 能力检测」走 buildflags 契约（pro 构建功能缺失而非上锁）——license 状态
  是**商业化运营层**的数据（谁买了什么），与构建裁剪是两件事，二者不混用。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LICENSE_PATH = Path(os.environ.get(
    "CONTINUUM_LICENSE_PATH") or (Path.home() / ".continuum" / "license.json"))

KEY_RE = re.compile(r"^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$")

# plan → 解锁的功能集（运营层语义；构建层能力见 buildflags）
PLAN_FEATURES: dict[str, tuple[str, ...]] = {
    "free": (),
    "pro": ("cross_host",),                      # 本地 Pro：跨宿主汇聚
    "pro_cloud": ("cross_host", "cloud_judge", "cloud_sync"),  # Pro + 云
}


@dataclass(frozen=True)
class LicenseState:
    activated: bool
    plan: str                    # free / pro / pro_cloud
    key_masked: str
    activated_at: str


def load_state(path: Path = DEFAULT_LICENSE_PATH) -> LicenseState:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return LicenseState(activated=False, plan="free", key_masked="", activated_at="")
    key = d.get("key") or ""
    masked = "-".join(key[i:i + 4] for i in range(0, len(key), 4)) if key else ""
    return LicenseState(
        activated=bool(d.get("activated")),
        plan=d.get("plan") or "free",
        key_masked=masked,
        activated_at=d.get("activated_at") or "",
    )


def validate_key(key: str) -> tuple[bool, str]:
    """骨架校验：格式 4×4 + 末位校验和（前 15 位 ord 和 % 36，base36）。
    真签名校验随商业化接入——替换此函数即可，调用方不变。"""
    key = key.strip().upper().replace(" ", "")
    if not KEY_RE.fullmatch(key):
        return False, "注册码格式应为 XXXX-XXXX-XXXX-XXXX"
    digits = key.replace("-", "")
    if sum(ord(c) for c in digits[:15]) % 36 != int(digits[15], 36):
        return False, "注册码校验失败（校验位不符）"
    return True, ""


def activate(key: str, plan: str, path: Path = DEFAULT_LICENSE_PATH) -> LicenseState:
    ok, err = validate_key(key)
    if not ok:
        raise ValueError(err)
    now = datetime.now(timezone.utc).isoformat()
    state = {"activated": True, "key": key.strip().upper(), "plan": plan,
             "activated_at": now}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return load_state(path)


def is_pro(path: Path = DEFAULT_LICENSE_PATH) -> bool:
    st = load_state(path)
    return st.activated and st.plan in ("pro", "pro_cloud")


def features_unlocked(path: Path = DEFAULT_LICENSE_PATH) -> tuple[str, ...]:
    st = load_state(path)
    return PLAN_FEATURES.get(st.plan, ()) if st.activated else ()
