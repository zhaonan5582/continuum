# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""L0/L1 物化视图（docs/01 §4：收敛扇——目录只放指针，每层硬预算）。

- L0（全局，≤2K token）：人格占位 + 项目清单 + 红线总数；
- L1（每项目，≤3K token）：红线 > 决策 > 约定 > 排除 > 事实，同类按时间降序，
  预算截断（被移走的信息仍可经 recall 从 L3 寻回——寻回原则）。
- token 估算：CJK ≈1.5 token/字，ASCII ≈0.25/字符（粗估，够预算管理用）。
"""

from __future__ import annotations

from continuum.storage.backend import StorageBackend

_KIND_PRIORITY = ("redline", "decision", "convention", "exclusion", "preference", "fact")
_KIND_TITLE = {
    "redline": "红线", "decision": "决策", "convention": "约定",
    "exclusion": "排除清单（试过不行）", "preference": "偏好", "fact": "事实",
}


def estimate_tokens(text: str) -> int:
    """粗估 token 数：CJK 按 1.5/字，其余按 0.25/字符。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    return int(cjk * 1.5 + (len(text) - cjk) * 0.25)


def materialize_l1(backend: StorageBackend, project_id: str | None = None,
                   budget_tokens: int = 3_000) -> str:
    """L1 项目状态：active 记忆按类型分组、优先级排序、硬预算截断。"""
    conds = ["status = 'active'"]
    params: list = []
    if project_id:
        # project 通过 session 关联（P1 简化：session.project_id 匹配）
        conds.append(
            "session_id IN (SELECT id FROM sessions WHERE project_id = ?)"
        )
        params.append(project_id)
    rows = backend.conn.execute(
        "SELECT kind, statement, evidence_level, created_at FROM memories WHERE "
        + " AND ".join(conds)
        + " ORDER BY created_at DESC",
        params,
    ).fetchall()

    by_kind: dict[str, list] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)

    lines: list[str] = [f"# L1 项目状态{('（' + project_id + '）') if project_id else ''}"]
    used = estimate_tokens(lines[0])
    truncated: list[str] = []
    for kind in _KIND_PRIORITY:
        items = by_kind.get(kind)
        if not items:
            continue
        section = [f"## {_KIND_TITLE[kind]}"]
        section_used = estimate_tokens(section[0])
        for r in items:
            line = f"- {r['statement']}"
            t = estimate_tokens(line)
            if used + section_used + t > budget_tokens:
                truncated.append(f"{kind}:{r['statement'][:30]}")
                continue
            section.append(line)
            section_used += t
        if len(section) > 1:
            lines.extend(section)
            used += section_used
    if len(lines) == 1:
        # 空态占位：不留悬空指针（L0 红线概况写着"全文见 L1"，L1 不能是裸标题）
        lines.append("（暂无 active 记忆——等待落库 / 沉淀或显式导入）")
    if truncated:
        lines.append(f"（{len(truncated)} 条因预算截断，可经 recall 寻回）")
    return "\n".join(lines)


def materialize_l0(backend: StorageBackend, budget_tokens: int = 2_000) -> str:
    """L0 人格与默契：P2 阶段 persona 为占位（引擎 P3 上线），含项目清单与红线概况。"""
    projects = backend.conn.execute(
        "SELECT DISTINCT project_id FROM sessions WHERE project_id IS NOT NULL LIMIT 20"
    ).fetchall()
    redline_count = backend.conn.execute(
        "SELECT COUNT(*) c FROM memories WHERE kind='redline' AND status='active'"
    ).fetchone()["c"]
    lines = [
        "# L0 全局状态",
        "## 人格与默契",
        "（人格引擎于 P3 上线；当前阶段协作规则由装配方注入）",
        "## 活跃项目",
    ]
    for r in projects:
        if r["project_id"]:
            lines.append(f"- {r['project_id']}")
    if not any(r["project_id"] for r in projects):
        lines.append("- （暂无——等待首次落库或显式导入）")
    lines.append(f"## 红线概况：当前生效红线 {redline_count} 条（全文见 L1）")
    text = "\n".join(lines)
    while estimate_tokens(text) > budget_tokens and len(lines) > 3:
        lines.pop()                       # 预算裁剪：从尾部丢（项目清单可丢，头部结构保留）
        text = "\n".join(lines)
    return text
