#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""隐私承诺检查：核心包源码禁止出现任何网络库引用（默认零外联，宪法 8）。

用法：python tests/check_no_network.py   （CI 与本地复用；任何命中退出码 1）
与 tests/test_commercial.py::TestZeroNetwork 同一逻辑——脚本供 CI 独立步骤使用。
"""
from __future__ import annotations

import sys
from pathlib import Path

BAD_KEYWORDS = ("urllib", "requests", "httpx", "socket", "http.client", "urllib3", "websocket")


def main() -> int:
    src = Path(__file__).resolve().parents[1] / "src" / "continuum"
    offenders: list[str] = []
    for p in sorted(src.rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        for kw in BAD_KEYWORDS:
            if kw in text:
                offenders.append(f"{p.relative_to(src.parents[2])} contains '{kw}'")
    if offenders:
        print("PRIVACY FAILED: remove network usage from core (BYOK only at adapter layer):", file=sys.stderr)
        for o in offenders:
            print(f"  VIOLATION: {o}", file=sys.stderr)
        return 1
    print("PRIVACY OK: core has zero network-library references.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
