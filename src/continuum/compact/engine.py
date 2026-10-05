# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""按条判决压缩引擎（docs/01 §5.2，宪法 5：禁止摘要式压缩）。

三态判决（逐条，不无差别摘要）：
- full     完整保留
- truncate 保留调用/要点 + 正文替换为指针
- drop     删除（**仅当该消息已沉淀出记忆/有指针时才允许**——无损层承诺）

判决来源两级：
- judge 注入时：judge.judge_message_retention 逐条判；
- 无 judge 时：启发式兜底——资产类内容（红线/决策/约定/排除相关或 user 消息）full，
  拿不准一律 full（宁可胖不可丢）。

红线/决策/排除类内容 **drop 前必须验证已入 L1**（§5.2 铁律）——当前实现为
"此类内容启发式强制 full"，从根上避免违规 drop。
产物是 CompactPlan（每条去留+理由+指针），**不是摘要文本**；宿主如需摘要基于清单生成。
"""

from __future__ import annotations

import re

from continuum.judges.base import Judge
from continuum.storage.backend import StorageBackend

_ASSET_PATTERN = re.compile(
    r"(决定|拍板|敲定|约定|说好|不许|不准|禁止|红线|严禁|试过|走不通|排除|放弃|偏好|我习惯)"
)

# 最早/最新的消息保留（首尾不动，防止上下文断裂）
_KEEP_HEAD = 2
_KEEP_TAIL = 4


def compact_session(
    backend: StorageBackend,
    session_id: int,
    from_seq: int,
    to_seq: int,
    judge: Judge | None = None,
) -> tuple[list[dict], int, int]:
    """对 [from_seq, to_seq] 区间逐条判决。返回 (entries, chars_before, chars_after)。

    entries 元素：{message_id, seq, verdict, reason, pointer}
    """
    rows = backend.conn.execute(
        "SELECT id, seq, role, content, ts FROM messages"
        " WHERE session_id=? AND seq BETWEEN ? AND ? ORDER BY seq",
        (session_id, from_seq, to_seq),
    ).fetchall()

    # 有沉淀指针的消息集合（该消息已产生记忆条目 → 有指针可指向）
    ids = [str(r["id"]) for r in rows]
    pointered: set[int] = set()
    if ids:
        ph = ",".join("?" * len(ids))
        for r in backend.conn.execute(
            f"SELECT DISTINCT source_message_id FROM memories WHERE source_message_id IN ({ph})",
            ids,
        ):
            pointered.add(int(r["source_message_id"]))

    entries: list[dict] = []
    chars_before = 0
    chars_after = 0
    total = len(rows)
    for idx, r in enumerate(rows):
        mid = int(r["id"])
        content = r["content"]
        chars_before += len(content)
        entry = {"message_id": mid, "seq": int(r["seq"]), "verdict": "full", "reason": "", "pointer": None}

        # 首尾保护（上下文断裂防线）
        if idx < _KEEP_HEAD or idx >= total - _KEEP_TAIL:
            entry["reason"] = "首尾保护：保留上下文完整"
            entries.append(entry)
            chars_after += len(content)
            continue

        if judge is not None:
            verdict, reason = judge.judge_message_retention(
                r["role"], content, has_downstream_memory=mid in pointered
            )
            # 引擎级强制（不信任 judge 自觉）：无沉淀指针的消息禁止 drop——
            # 无损层承诺由机制保证，而不是由 judge 的"自律"保证
            if verdict == "drop" and mid not in pointered:
                verdict, reason = "truncate", f"judge 建议 drop 但无沉淀指针，引擎降级保留（{reason}）"
        else:
            # 无 judge 启发式（§5.2 兜底：拿不准一律 full）
            has_pointer = mid in pointered
            if _ASSET_PATTERN.search(content):
                verdict, reason = "full", "资产类内容（决策/约定/红线/排除/偏好），启发式强制保留"
            elif has_pointer:
                verdict, reason = ("truncate", "内容已沉淀为记忆，正文留指针（无 judge 保守不 drop）")
            elif r["role"] == "tool":
                verdict, reason = ("truncate", "工具结果：保留调用语义，正文可截断（无 judge 保守不 drop）")
            else:
                verdict, reason = "full", "无 judge：拿不准一律保留（宁可胖不可丢）"

        entry["verdict"] = verdict
        entry["reason"] = reason
        if verdict == "truncate":
            entry["pointer"] = f"messages/{mid}"
            chars_after += min(len(content), 200)      # 截断保留前 200 字符为要点
        elif verdict == "drop":
            entry["pointer"] = f"messages/{mid}"
            # drop 不计入 after（正文移除，指针极小）
        else:
            chars_after += len(content)
        entries.append(entry)

    return entries, chars_before, chars_after
