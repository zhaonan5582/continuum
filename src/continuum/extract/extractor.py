# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""轨道 B 抽取器：把会话消息沉淀为结构化记忆候选（无 judge 模式）。

P1 边界决策（诚实记录）：
- 一律 evidence_level="inferred" + status="pending"（无 judge 不冒充高置信度，宪法 2）；
- stated_by 按消息角色：user→user、assistant→agent（写入投毒分权，docs/06）；
- 实体**只挂不建**：statement 中出现已存在实体（canonical/aliases/name 匹配）才记
  mem_mentions；新建实体延后到 P2（有 judge 后，避免无判据的实体增殖）；
- 每条候选四强制字段齐全（backend.add_memory 集中强制）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from continuum.extract.heuristics import extract_candidates_from_text
from continuum.storage.backend import StorageBackend
from continuum.udf import UDFMessage

_STATED_BY = {"user": "user", "assistant": "agent", "tool": "agent"}


@dataclass(frozen=True)
class Candidate:
    kind: str
    statement: str
    stated_by: str
    source_message_id: int


def candidates_from_messages(
    messages: list[tuple[int, UDFMessage]],
) -> list[Candidate]:
    """从 (message_id, UDFMessage) 列表抽取候选。"""
    out: list[Candidate] = []
    for mid, msg in messages:
        stated_by = _STATED_BY.get(msg.role, "agent")
        for kind, statement in extract_candidates_from_text(msg.content):
            out.append(Candidate(kind=kind, statement=statement,
                                 stated_by=stated_by, source_message_id=mid))
    return out


def _match_existing_entities(backend: StorageBackend, statement: str) -> list[int]:
    """statement 中出现已存在实体（canonical/name/alias）→ 返回实体 id 列表。"""
    rows = backend.conn.execute("SELECT id, canonical_name, name, aliases_json FROM entities").fetchall()
    hits: list[int] = []
    for r in rows:
        names = {r["canonical_name"], r["name"], *json.loads(r["aliases_json"])}
        if any(n and n in statement for n in names):
            hits.append(int(r["id"]))
    return hits


def run_extraction(
    backend: StorageBackend,
    session_id: int,
    messages: list[tuple[int, UDFMessage]],
) -> tuple[int, int, int]:
    """执行一次结构化预沉淀。返回 (produced_pending, skipped_empty, scanned_messages)。

    - 每条候选写入 memories（inferred + pending，四强制字段齐全）；
    - 同 (kind, statement, source_message_id) 幂等（应用层查重）；
    - statement 中出现的已有实体 → 记 mem_mentions。
    """
    produced = 0
    scanned = len(messages)
    candidates = candidates_from_messages(messages)
    for cand in candidates:
        # 幂等：同来源同语句不重复写入
        dup = backend.conn.execute(
            "SELECT id FROM memories WHERE kind=? AND statement=? AND source_message_id=?",
            (cand.kind, cand.statement, cand.source_message_id),
        ).fetchone()
        if dup:
            continue
        mid = backend.add_memory(
            kind=cand.kind,
            statement=cand.statement,
            stated_by=cand.stated_by,
            source_message_id=cand.source_message_id,
            session_id=session_id,
            evidence_level="inferred",
            status="pending",
        )
        produced += 1
        for eid in _match_existing_entities(backend, cand.statement):
            backend.record_mention(cand.source_message_id, eid)
    backend.audit(
        "core", "extract", f"sessions/{session_id}",
        {"scanned": scanned, "produced_pending": produced,
         "mode": "heuristic-no-judge"},
    )
    return produced, scanned - len(candidates), scanned
