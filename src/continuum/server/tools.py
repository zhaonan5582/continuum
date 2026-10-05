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

    # ---- P1 · 契约 stub ----
    def memory_extract(self, scope: ExtractScope) -> ExtractResult:
        raise FeatureNotAvailable("memory_extract", self.PHASES["memory_extract"])

    def memory_recall(self, query: str, time_hint: str | None = None, limit: int = 20) -> RecallResult:
        raise FeatureNotAvailable("memory_recall", self.PHASES["memory_recall"])

    def memory_audit(self, query: AuditQuery) -> AuditResult:
        raise FeatureNotAvailable("memory_audit", self.PHASES["memory_audit"])

    # ---- P2 · 契约 stub ----
    def memory_compact(self, rng: CompactRange) -> CompactPlan:
        raise FeatureNotAvailable("memory_compact", self.PHASES["memory_compact"])

    def memory_assemble(self, project_id: str | None = None) -> AssemblePackage:
        raise FeatureNotAvailable("memory_assemble", self.PHASES["memory_assemble"])

    # ---- P3 · 契约 stub ----
    def memory_guard(self, operation: Operation) -> GuardVerdict:
        raise FeatureNotAvailable("memory_guard", self.PHASES["memory_guard"])
