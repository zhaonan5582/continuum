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
    confirm: bool = False            # v1.3 审查修复：True=用户显式触发（拍板）→ active；
                                     # False=机制扳机（sweeping）→ pending 待确认


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

    def __init__(self, backend: StorageBackend, judge=None):
        self.be = backend
        self.judge = judge        # P2：Judge 实例（可选，None=启发式兜底）

    def set_judge(self, judge) -> None:
        """运行时注入/替换 Judge（BYOK 配置变化时调用）。"""
        self.judge = judge

    # ---- P1 · 已实装 ----
    def memory_append(self, req: AppendRequest) -> AppendResult:
        if not req.host_agent or not req.host_agent.strip():
            raise ValueError("host_agent 不能为空（tools 层防御，与 backend 双层）")
        # 一致性强制：消息级 host 必须与会话级 host_agent 一致，
        # 否则同一条消息会因幂等键 (host, external_id) 不匹配而静默分裂会话（探针坐实）
        bad = sorted({m.host for m in req.messages if m.host != req.host_agent})
        if bad:
            raise ValueError(f"消息 host 与 host_agent 不一致 {bad}（应为 {req.host_agent!r}）——适配器必须统一")
        sid = self.be.ensure_session(
            req.host_agent, req.external_session_id, title=req.title, project_id=req.project_id
        )
        ids, skipped = self.be.append_messages(sid, list(req.messages))
        return AppendResult(session_id=sid, accepted=len(ids), skipped=skipped, message_ids=tuple(ids))

    def memory_extract(self, scope: ExtractScope) -> ExtractResult:
        """轨道 B / sweeping 的沉淀动作（无 judge 模式：中文启发式）。
        confirm=True（用户显式触发）→ active 直接可召回；
        confirm=False（机制扳机）→ pending 待确认（探针23：沉淀必须可见，但猜测不当记忆卖——
        pending 条目进管理队列，active 才进用户召回）。"""
        import time as _time

        from continuum.extract import run_extraction  # 延迟导入保持 server 层轻

        t0 = _time.perf_counter()
        if scope.session_id is not None:
            rows = self.be.list_session_messages_with_ids(scope.session_id, limit=200)
        else:
            rows = self.be.fetch_messages_since(scope.since_ts, limit=200)
        produced, _, scanned = run_extraction(
            self.be, scope.session_id, rows,
            status_override="active" if scope.confirm else "pending",
        )
        latency = round((_time.perf_counter() - t0) * 1000, 1)
        self.be.audit("core", "extract.done", f"sessions/{scope.session_id}",
                      {"produced": produced, "scanned": scanned, "ms": latency,
                       "confirm": scope.confirm})
        return ExtractResult(produced_active=produced if scope.confirm else 0,
                             produced_pending=0 if scope.confirm else produced,
                             scanned_messages=scanned)

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

    # ---- P2 · 已实装 ----
    def memory_assemble(self, project_id: str | None = None) -> AssemblePackage:
        """冷启动装配包（第一防线）：persona 最新版 + L0 + 当前项目 L1 + 最近现场。
        总预算 ≤8K token，超预算按「最近现场 → 事实」顺序剪（红线/决策/约定不动）。"""
        from continuum.snapshot import estimate_tokens, materialize_l0, materialize_l1

        persona_md = self.persona_current_md()
        l0 = materialize_l0(self.be)
        l1 = materialize_l1(self.be, project_id)
        recent_rows = self.be.fetch_messages_since(None, limit=20)
        recent = tuple(m.content for _, m in recent_rows)

        parts_used = estimate_tokens(persona_md) + estimate_tokens(l0) + estimate_tokens(l1)
        warnings: list[str] = []
        kept_recent: list[str] = []
        budget = 8_000
        for text in recent:
            t = estimate_tokens(text)
            if parts_used + t > budget:
                warnings.append(f"最近现场因预算截断 {len(recent) - len(kept_recent)} 条（L0+L1 优先）")
                break
            kept_recent.append(text)
            parts_used += t

        pkg = AssemblePackage(
            project_id=project_id, persona_md=persona_md, snapshot_md=l0 + "\n\n" + l1,
            recent_verbatim=tuple(kept_recent), token_estimate=parts_used,
            warnings=tuple(warnings),
        )
        self.be.audit("core", "assemble", project_id or "global",
                      {"token_estimate": pkg.token_estimate, "recent": len(kept_recent)})
        return pkg

    def memory_compact(self, rng: CompactRange) -> CompactPlan:
        """按条判决压缩（宪法 5）：逐条 full/truncate/drop + 理由 + 原文指针。
        产物是清单不是摘要。有 judge 用 judge 逐条判；无 judge 启发式兜底（拿不准一律 full）。
        drop 前提：该消息已沉淀出记忆（有指针）——无损层承诺不破。"""
        from continuum.compact import compact_session

        entries, before, after = compact_session(
            self.be, rng.session_id, rng.from_seq, rng.to_seq, judge=self.judge
        )
        plan = CompactPlan(
            session_id=rng.session_id,
            entries=tuple(CompactEntry(
                message_id=e["message_id"], verdict=e["verdict"],
                reason=e["reason"], pointer=e["pointer"],
            ) for e in entries),
            chars_before=before, chars_after=after,
        )
        self.be.audit("core", "compact", f"sessions/{rng.session_id}", {
            "range": [rng.from_seq, rng.to_seq], "entries": len(plan.entries),
            "chars_before": before, "chars_after": after,
            "verdicts": {v: sum(1 for e in plan.entries if e.verdict == v)
                         for v in ("full", "truncate", "drop")},
        })
        return plan

    # ---- P3 · 已实装 ----
    def memory_guard(self, operation: Operation) -> GuardVerdict:
        """红线门禁（宪法 6：hook 管「不能违反」）。命中即审计（F3 种子数据）。
        判定顺序：block > ask > warn（最严者生效）。"""
        matched = self.be.match_redlines(operation.target,
                                         project_id=operation.detail.get("project_id"))
        if not matched:
            verdict, reason, ids = "allow", "无命中红线", ()
        else:
            ids = tuple(r["id"] for r in matched)
            actions = [r["action"] for r in matched]
            if "block" in actions:
                verdict = "block"
                reason = "命中 block 红线: " + "; ".join(
                    r["statement"] for r in matched if r["action"] == "block")[:200]
            elif "ask" in actions:
                verdict, reason = "ask", "命中 ask 红线，需人工确认"
            else:
                verdict, reason = "warn", "命中 warn 红线"
        self.be.audit("core", "guard", operation.target[:120],
                      {"verdict": verdict, "matched": list(ids), "kind": operation.kind})
        return GuardVerdict(verdict=verdict, matched_redlines=ids, reason=reason)

    def persona_current_md(self) -> str:
        """最新人格状态块文本（assemble 数据源；无版本返回占位）。"""
        cur = self.be.persona_current()
        if cur is None:
            return "（人格引擎于 P3 上线；当前协作规则见 L0/L1）"
        return f"<!-- persona v{cur['version']} -->\n{cur['snapshot_md']}"
