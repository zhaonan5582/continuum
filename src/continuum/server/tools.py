# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""MCP Server 七工具签名（P0 契约冻结，docs/01 §6）——实现分批交付。

| 工具 | 期次 | 状态（P0） |
|------|------|-----------|
| memory_append  | P1 | ✅ 已实装（阶段 B） |
| memory_extract | P1 | 签名冻结，调用抛 FeatureNotAvailable |
| memory_recall  | P1 | 签名冻结，同上 |
| memory_audit   | P1 | 签名冻结，同上 |
| memory_compact | P2 | 签名冻结，同上 |
| memory_assemble| P2 | 签名冻结，同上 |
| memory_guard   | P3 | 签名冻结，同上 |

冻结规则：参数与返回类型为契约，只增可选字段不改既有语义；
任何签名修改走变更记录 + 机械交叉检索。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from continuum.storage.backend import StorageBackend
from continuum.udf import UDFMessage

# ---------- 请求 / 响应类型（契约冻结） ----------


@dataclass(frozen=True)
class AppendRequest:
    host_agent: str
    external_session_id: str
    messages: tuple[UDFMessage, ...]
    title: str | None = None
    project_id: str | None = None


@dataclass(frozen=True)
class AppendResult:
    session_id: int
    accepted: int
    skipped: int
    message_ids: tuple[int, ...]


@dataclass(frozen=True)
class ExtractScope:
    session_id: int | None = None
    since_ts: str | None = None
    kind: str | None = None          # fact/decision/exclusion/convention/redline/preference


@dataclass(frozen=True)
class ExtractResult:
    produced_active: int
    produced_pending: int            # 低置信度待确认（写入即"诚实"，无 judge 不冒充）
    scanned_messages: int


@dataclass(frozen=True)
class RecallItem:
    memory_id: int
    statement: str
    kind: str
    evidence_level: str              # tested/cited/inferred（宪法 2，逐条带等级返回）
    stated_by: str                   # user/agent
    source_message_id: int | None
    ts: str
    score: float


@dataclass(frozen=True)
class RecallResult:
    items: tuple[RecallItem, ...]
    rounds_used: int                 # 停止判断循环实际轮数（增强模式）
    latency_ms: float
    stopped_by: str                  # budget / satisfied / no-judge-fast-path


@dataclass(frozen=True)
class AuditQuery:
    action: str | None = None
    target_like: str | None = None
    since_ts: str | None = None
    limit: int = 50


@dataclass(frozen=True)
class AuditResult:
    entries: tuple[dict, ...]        # {ts, actor, action, target, detail}
    total_matched: int


@dataclass(frozen=True)
class CompactRange:
    session_id: int
    from_seq: int
    to_seq: int


@dataclass(frozen=True)
class CompactEntry:
    message_id: int
    verdict: str                     # full / truncate / drop（§5.2 三态）
    reason: str                      # 判决理由（清单本身是审计对象）
    pointer: str | None = None       # drop/truncate 时指向原文或沉淀产物的指针


@dataclass(frozen=True)
class CompactPlan:
    session_id: int
    entries: tuple[CompactEntry, ...]
    chars_before: int
    chars_after: int
    note: str = ""                   # 例：红线/决策/排除清单已被验证入 L1


@dataclass(frozen=True)
class AssemblePackage:
    project_id: str | None
    persona_md: str                  # L0 人格与默契（最新版本块）
    snapshot_md: str                 # L0 + L1 快照（指针为主，硬预算内）
    recent_verbatim: tuple[str, ...] # L0.5 最近现场（原样，不摘要）
    token_estimate: int              # 总预算 ≤ 8_000（§10 指标）
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Operation:
    kind: str                        # write / delete / send / exec ...
    target: str                      # 操作对象（路径/表/外发目标）
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GuardVerdict:
    verdict: str                     # allow / warn / block
    matched_redlines: tuple[int, ...]
    reason: str


class FeatureNotAvailable(NotImplementedError):
    """功能存在契约、未到交付期次。消息必须携带期次（诚实接口，不静默装死）。"""

    def __init__(self, tool: str, phase: str):
        super().__init__(f"{tool} 属于 {phase} 交付范围，当前版本未实装")
        self.tool = tool
        self.phase = phase


# ---------- 中文相对时间解析（recall time_hint，P1 简版） ----------

import re as _re  # noqa: E402
from datetime import datetime, timedelta, timezone as _tz  # noqa: E402


def _parse_time_hint(hint: str) -> str | None:
    """把「今天/昨天/前天/上周/上个月/最近一周/N天前」解析为 from_ts（ISO8601 UTC）。
    无法识别返回 None（不影响检索，仅退化为不限时间）。"""
    hint = (hint or "").strip()
    if not hint:
        return None
    now = datetime.now(_tz.utc)

    def _iso(days_ago: int) -> str:
        return (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    m = _re.match(r"^(\d+)\s*天前$", hint)
    if m:
        return _iso(int(m.group(1)))
    table = {"今天": 0, "今天内": 0, "昨天": 1, "前天": 2, "最近一周": 0, "本周": 0,
             "上周": 7, "最近一月": 0, "上个月": 31, "最近一个月": 0}
    if hint in table:
        return _iso(table[hint])
    return None


# ---------- Server（7 工具挂载点） ----------


class ContinuumServer:
    """P0：append 实装 + 六工具契约 stub；P1/P2/P3 逐期点亮。"""

    PHASES = {
        "memory_append": "P1",
        "memory_extract": "P1",
        "memory_recall": "P1",
        "memory_audit": "P1",
        "memory_compact": "P2",
        "memory_assemble": "P2",
        "memory_guard": "P3",
    }

    def __init__(self, backend: StorageBackend):
        self.be = backend

    # ---- P1 · 已实装 ----
    def memory_append(self, req: AppendRequest) -> AppendResult:
        if not req.host_agent or not req.host_agent.strip():
            raise ValueError("host_agent 不能为空（tools 层防御，与 backend 双层）")
        sid = self.be.ensure_session(
            req.host_agent, req.external_session_id, title=req.title, project_id=req.project_id
        )
        ids, skipped = self.be.append_messages(sid, list(req.messages))
        return AppendResult(session_id=sid, accepted=len(ids), skipped=skipped, message_ids=tuple(ids))

    def memory_extract(self, scope: ExtractScope) -> ExtractResult:
        """轨道 B / sweeping 的沉淀动作（无 judge 模式：中文启发式，全量 inferred+pending）。"""
        import time as _time

        from continuum.extract import run_extraction  # 延迟导入保持 server 层轻

        t0 = _time.perf_counter()
        if scope.session_id is not None:
            rows = self.be.list_session_messages_with_ids(scope.session_id, limit=200)
        else:
            rows = self.be.fetch_messages_since(scope.since_ts, limit=200)
        produced, _, scanned = run_extraction(self.be, scope.session_id, rows)
        latency = round((_time.perf_counter() - t0) * 1000, 1)
        self.be.audit("core", "extract.done", f"sessions/{scope.session_id}",
                      {"produced": produced, "scanned": scanned, "ms": latency})
        return ExtractResult(produced_active=0, produced_pending=produced, scanned_messages=scanned)

    def memory_recall(self, query: str, time_hint: str | None = None, limit: int = 20) -> RecallResult:
        """快速路径（无 judge）：结构化过滤主力（memories 逐词匹配 + 类型/时间过滤）
        + FTS 原文兜底。user-stated 优先。停止条件 = 无 judge 固定单轮（增强模式 P2）。"""
        import re as _re
        import time as _time

        t0 = _time.perf_counter()
        time_from = _parse_time_hint(time_hint) if time_hint else None

        # 查询词提取：整串 + 高频滑窗（中文 LIKE 需要短语粒度）
        chunks = [c for c in _re.split(r"[^\w\u4e00-\u9fff]+", query) if len(c) >= 2]
        terms: list[str] = []
        for c in chunks[:4]:
            if c not in terms:
                terms.append(c)
            if len(c) >= 4:                       # 长串切 2 字滑窗提升召回
                for i in range(len(c) - 1):
                    g = c[i:i + 2]
                    if g not in terms and not _re.match(r"^[\d_]+$", g):
                        terms.append(g)
        terms = terms[:10]

        results: list[RecallItem] = []
        seen: set = set()
        if terms:
            for r in self.be.search_memories(terms, time_from=time_from, limit=limit):
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                results.append(RecallItem(
                    memory_id=r["id"], statement=r["statement"], kind=r["kind"],
                    evidence_level=r["evidence_level"], stated_by=r["stated_by"],
                    source_message_id=r["source_message_id"], ts=r["created_at"],
                    score=1.0 if r["stated_by"] == "user" else 0.6,
                ))
            for term in terms:
                if len(term) < 3:                  # FTS trigram 需 ≥3 字符
                    continue
                for h in self.be.search_content(term, limit=limit):
                    key = ("m", h["id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(RecallItem(
                        memory_id=-h["id"], statement=h["content"][:200], kind="verbatim",
                        evidence_level="cited", stated_by="user",
                        source_message_id=h["id"], ts=h["ts"], score=0.3,
                    ))
        results.sort(key=lambda i: i.score, reverse=True)
        results = results[:limit]
        latency = round((_time.perf_counter() - t0) * 1000, 1)
        self.be.audit("core", "recall", query[:80],
                      {"hits": len(results), "ms": latency, "mode": "fast-path"})
        return RecallResult(items=tuple(results), rounds_used=1,
                            latency_ms=latency, stopped_by="no-judge-fast-path")

    def memory_audit(self, query: AuditQuery) -> AuditResult:
        entries, total = self.be.audit_query(
            action=query.action, target_like=query.target_like,
            since_ts=query.since_ts, limit=query.limit,
        )
        return AuditResult(entries=tuple(entries), total_matched=total)

    # ---- P2 · 契约 stub ----
    def memory_compact(self, rng: CompactRange) -> CompactPlan:
        raise FeatureNotAvailable("memory_compact", self.PHASES["memory_compact"])

    def memory_assemble(self, project_id: str | None = None) -> AssemblePackage:
        raise FeatureNotAvailable("memory_assemble", self.PHASES["memory_assemble"])

    # ---- P3 · 契约 stub ----
    def memory_guard(self, operation: Operation) -> GuardVerdict:
        raise FeatureNotAvailable("memory_guard", self.PHASES["memory_guard"])
