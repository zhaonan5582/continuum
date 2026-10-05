# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""SQLite 存储后端——默认实现（单文件、WAL、单写者常驻 server 多 client）。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterator

from continuum.storage.backend import StorageBackend
from continuum.storage.migrations import runner
from continuum.udf import UDFMessage, now_iso
from continuum.version import DESIGN_CONSTANTS


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class SQLiteBackend(StorageBackend):
    def __init__(self, path: str | Path, migrations_dir: str | Path):
        self.path = str(path)
        self._migrations_dir = str(migrations_dir)
        # isolation_level=None：显式事务控制（迁移原子性依赖此模式）
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    # ---- migration ----
    def migrate(self) -> list[str]:
        return runner.migrate(self.conn, self._migrations_dir)

    # ---- sessions ----
    def ensure_session(
        self,
        host_agent: str,
        external_id: str,
        title: str | None = None,
        project_id: str | None = None,
    ) -> int:
        if not host_agent or not host_agent.strip():
            raise ValueError("host_agent 不能为空（商业化审查 v1.2：拒绝脏元数据）")
        if len(host_agent) > 128 or len(external_id) > 256:
            raise ValueError("host_agent/external_id 超长")
        row = self.conn.execute(
            "SELECT id FROM sessions WHERE host_agent=? AND external_id=?",
            (host_agent, external_id),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO sessions(host_agent, external_id, title, project_id, started_at)"
            " VALUES(?,?,?,?,?)",
            (host_agent, external_id, title, project_id, now_iso()),
        )
        sid = int(cur.lastrowid)
        self.audit("core", "session.ensure", f"sessions/{sid}", {"host": host_agent, "external_id": external_id})
        return sid

    # ---- messages ----
    def append_messages(self, session_id: int, messages: list[UDFMessage]) -> tuple[list[int], int]:
        """原文落库，语义 = 全或无：
        - 任何一条 UDF 非法 → 整批拒绝（一条不落），适配器修正后重推整批；
        - INSERT 阶段在显式事务内，杜绝半批落库；
        - 幂等键 (session_id, ts, role, content_hash)：同一消息重推不重复落库。
          已知取舍：秒级 ts 下「同秒同角色同内容」的两条真实发言会被视为重推——
          适配器必须提供毫秒级精度 ts（UDF schema 已注明），现实中概率≈0。
        """
        # 校验前置：任何一条非法即整批拒绝（fail-fast，不落任何一行）
        for i, msg in enumerate(messages):
            errs = msg.validate()
            if errs:
                raise ValueError(f"第 {i} 条 UDF 校验失败，整批拒绝: {'; '.join(errs)}")

        accepted: list[int] = []
        skipped = 0
        try:
            self.conn.execute("BEGIN")
            row = self.conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS m FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()
            seq = int(row["m"])
            for msg in messages:
                seq += 1
                chash = _sha256(f"{msg.role}|{msg.ts}|{msg.content}")
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO messages"
                    " (session_id, seq, role, host, content, content_hash, ts, token_count)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (
                        session_id,
                        seq,
                        msg.role,
                        msg.host,
                        msg.content,
                        chash,
                        msg.ts,
                        msg.meta.tokens,
                    ),
                )
                if cur.rowcount == 0:  # 幂等命中：同一消息重复推送
                    skipped += 1
                    continue
                accepted.append(int(cur.lastrowid))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.rollback()
            raise
        if accepted:
            self.audit(
                "core",
                "append",
                f"sessions/{session_id}",
                {"accepted": len(accepted), "skipped": skipped},
            )
        return accepted, skipped

    def iter_session_messages(self, session_id: int, limit: int | None = None) -> Iterator[UDFMessage]:
        sql = "SELECT * FROM messages WHERE session_id=? ORDER BY seq"
        params: tuple = (session_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (session_id, limit)
        for r in self.conn.execute(sql, params):
            yield UDFMessage(
                ts=r["ts"],
                role=r["role"],
                host=r["host"],
                session_id=str(session_id),
                content=r["content"],
            )

    def list_session_messages_with_ids(self, session_id: int, limit: int = 200) -> list[tuple[int, UDFMessage]]:
        rows = self.conn.execute(
            "SELECT id, ts, role, host, session_id, content FROM messages"
            " WHERE session_id=? ORDER BY seq LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            (
                int(r["id"]),
                UDFMessage(ts=r["ts"], role=r["role"], host=r["host"],
                           session_id=str(session_id), content=r["content"]),
            )
            for r in rows
        ]

    def fetch_messages_since(self, since_ts: str | None = None, limit: int = 200) -> list[tuple[int, UDFMessage]]:
        if since_ts:
            rows = self.conn.execute(
                "SELECT id, ts, role, host, session_id, content FROM messages"
                " WHERE ts >= ? ORDER BY ts LIMIT ?",
                (since_ts, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT id, ts, role, host, session_id, content FROM messages"
                " ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            rows = list(reversed(rows))
        return [
            (
                int(r["id"]),
                UDFMessage(ts=r["ts"], role=r["role"], host=r["host"],
                           session_id=str(r["session_id"]), content=r["content"]),
            )
            for r in rows
        ]

    def search_content(self, query: str, limit: int = 20) -> list[dict]:
        """FTS5 trigram 检索（原文寻回兜底通道）。

        v1.5 修正：**引号短语查询**——`"片段"` 对 trigram 索引即连续子串精确匹配
        （滑窗 OR 会把查询弱化到 3 字粒度，40MB 语料中判别力崩塌，实测 7%）。
        ≤2 字符显式空结果（trigram 语义下无意义）。"""
        q = query.strip()
        if len(q) < 3:
            return []
        # 超长串（>40 字）降级为头部短语（FTS 查询串过长性能退化）
        phrase = q[:40] if len(q) > 40 else q
        match_expr = f'"{phrase}"'
        try:
            rows = self.conn.execute(
                "SELECT m.id, m.session_id, m.role, m.ts, m.content,"
                " snippet(messages_fts, 0, '<<', '>>', '…', 64) AS snip"
                " FROM messages_fts f JOIN messages m ON m.id = f.rowid"
                " WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
                (match_expr, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []          # FTS 语法边缘（引号/特殊序列），兜底空结果
        return [dict(r) for r in rows]

    # ---- audit ----
    def audit(self, actor: str, action: str, target: str | None, detail: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO audit_log(ts, actor, action, target, detail_json) VALUES(?,?,?,?,?)",
            (now_iso(), actor, action, target, json.dumps(detail or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    # ---- memories / entities / mentions（P1 轨道 B 写入路径）----

    def find_or_create_entity(
        self,
        kind: str,
        name: str,
        canonical_name: str | None = None,
        aliases: list[str] | None = None,
    ) -> tuple[int, bool]:
        """实体消歧（docs/01 §5.1：先查相似→挂 alias，无命中才新建）。
        匹配顺序：exact(kind+canonical) → exact(kind+name) → alias 命中。
        返回 (entity_id, created)。"""
        canon = canonical_name or name
        row = self.conn.execute(
            "SELECT id FROM entities WHERE kind=? AND canonical_name=?", (kind, canon)
        ).fetchone()
        if row:
            eid = int(row["id"])
            if aliases:
                self._merge_aliases(eid, aliases)
            return eid, False
        row = self.conn.execute(
            "SELECT id FROM entities WHERE kind=? AND name=?", (kind, name)
        ).fetchone()
        if row:
            eid = int(row["id"])
            if aliases:
                self._merge_aliases(eid, aliases)
            return eid, False
        alias_json = json.dumps(sorted(set(aliases or []) | {name}), ensure_ascii=False)
        cur = self.conn.execute(
            "INSERT INTO entities(kind, name, canonical_name, aliases_json, created_at)"
            " VALUES(?,?,?,?,?)",
            (kind, name, canon, alias_json, now_iso()),
        )
        return int(cur.lastrowid), True

    def _merge_aliases(self, entity_id: int, aliases: list[str]) -> None:
        row = self.conn.execute("SELECT aliases_json FROM entities WHERE id=?", (entity_id,)).fetchone()
        current = set(json.loads(row["aliases_json"]))
        merged = json.dumps(sorted(current | set(aliases)), ensure_ascii=False)
        self.conn.execute("UPDATE entities SET aliases_json=? WHERE id=?", (merged, entity_id))

    def add_memory(
        self,
        *,
        kind: str,
        statement: str,
        stated_by: str,
        source_message_id: int | None,
        session_id: int | None,
        evidence_level: str = "inferred",
        status: str = "pending",
        valid_until: str | None = None,
    ) -> int:
        """写入记忆条目。四强制字段在此集中强制（宪法 2）：
        source_message_id / created_at / evidence_level / valid_from——缺一在此抛错。
        statement 为空串同样拒绝（空记忆比没有记忆更坏）。"""
        if not statement or not statement.strip():
            raise ValueError("statement 不能为空（空记忆拒绝写入）")
        if evidence_level not in DESIGN_CONSTANTS["EVIDENCE_LEVELS"]:
            raise ValueError(f"evidence_level 非法: {evidence_level}")
        if kind not in DESIGN_CONSTANTS["MEMORY_KINDS"]:
            raise ValueError(f"kind 非法: {kind}")
        if stated_by not in ("user", "agent"):
            raise ValueError(f"stated_by 非法: {stated_by}")
        if source_message_id is None:
            raise ValueError("source_message_id 缺失——无来源指针的记忆不许写入（宪法 2）")
        ts = now_iso()
        cur = self.conn.execute(
            "INSERT INTO memories"
            " (kind, statement, source_message_id, session_id, stated_by, evidence_level,"
            "  created_at, valid_from, valid_until, status)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (kind, statement.strip(), source_message_id, session_id, stated_by,
             evidence_level, ts, ts, valid_until, status),
        )
        return int(cur.lastrowid)

    def record_mention(self, message_id: int, entity_id: int,
                       span_start: int | None = None, span_end: int | None = None) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO mem_mentions(message_id, entity_id, span_start, span_end)"
            " VALUES(?,?,?,?)",
            (message_id, entity_id, span_start, span_end),
        )

    def search_memories(
        self,
        terms: list[str],
        kind: str | None = None,
        time_from: str | None = None,
        time_to: str | None = None,
        limit: int = 20,
        include_pending: bool = False,
    ) -> list[sqlite3.Row]:
        """结构化过滤主力（§5.3）：statement 逐词 LIKE + 类型/时间过滤 + user-stated 优先。

        status 语义（v1.2 审查修正）：默认只返回 active——pending 是未经确认的
        低置信度猜测，直接进用户召回 = 把猜测当记忆卖（探针20 坐实）。
        include_pending=True 仅供管理/调试路径使用。

        LIKE 通配符（%/_）做转义（ESCAPE '\\'）——分词层虽已天然滤掉大部分，
        此处按设计安全兜底（下划线在 \\w 分词中可存活）。
        空词列表返回空（避免全表扫描）。"""
        terms = [t for t in terms if t]
        if not terms:
            return []

        def _escape(s: str) -> str:
            return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        conds = ["status = 'active'" if not include_pending else "status IN ('active','pending')"]
        conds += ["statement LIKE ? ESCAPE '\\'" for _ in terms]
        params: list = [f"%{_escape(t)}%" for t in terms]
        if kind:
            conds.append("kind = ?")
            params.append(kind)
        if time_from:
            conds.append("created_at >= ?")
            params.append(time_from)
        if time_to:
            conds.append("created_at <= ?")
            params.append(time_to)
        params.append(limit)
        return self.conn.execute(
            "SELECT * FROM memories WHERE " + " AND ".join(conds) +
            " ORDER BY CASE stated_by WHEN 'user' THEN 0 ELSE 1 END, created_at DESC LIMIT ?",
            params,
        ).fetchall()

    # ---- audit 查询（memory_audit 数据源）----
    def audit_query(self, action: str | None = None, target_like: str | None = None,
                    since_ts: str | None = None, limit: int = 50) -> tuple[list[dict], int]:
        conds, params = [], []
        if action:
            conds.append("action = ?")
            params.append(action)
        if target_like:
            conds.append("target LIKE ?")
            params.append(f"%{target_like}%")
        if since_ts:
            conds.append("ts >= ?")
            params.append(since_ts)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        total = self.conn.execute(f"SELECT COUNT(*) c FROM audit_log {where}", params).fetchone()["c"]
        rows = self.conn.execute(
            f"SELECT ts, actor, action, target, detail_json FROM audit_log {where}"
            " ORDER BY ts DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows], total

    # ---- persona（版本化状态块，P3）----

    def persona_current(self) -> dict | None:
        """最新人格状态块（无版本时返回 None——assemble 用占位文本）。"""
        r = self.conn.execute(
            "SELECT id, version, snapshot_md, created_at, change_reason"
            " FROM persona_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return dict(r) if r else None

    def persona_create(self, snapshot_md: str, change_reason: str,
                       parent_version: int | None = None) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM persona_versions").fetchone()
        ver = int(row["v"])
        cur = self.conn.execute(
            "INSERT INTO persona_versions(version, snapshot_md, parent_version, change_reason, created_at)"
            " VALUES(?,?,?,?,?)",
            (ver, snapshot_md, parent_version, change_reason, now_iso()),
        )
        ver_id = int(cur.lastrowid)
        self.audit("core", "persona.create", f"persona/v{ver}", {"reason": change_reason})
        return ver_id

    def persona_add_sample(self, persona_version: int, user_utterance: str,
                           agent_response: str, tag: str | None = None,
                           source_message_id: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO persona_samples(persona_version, user_utterance, agent_response,"
            " source_message_id, tag) VALUES(?,?,?,?,?)",
            (persona_version, user_utterance, agent_response, source_message_id, tag),
        )
        return int(cur.lastrowid)

    def persona_samples(self, persona_version: int | None = None, limit: int = 100) -> list[dict]:
        where, params = "", []
        if persona_version is not None:
            where = "WHERE persona_version = ?"
            params.append(persona_version)
        rows = self.conn.execute(
            f"SELECT * FROM persona_samples {where} ORDER BY id DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- redlines（红线表：逐字存储 + 正反测试集）----

    def add_redline(self, pattern: str, statement: str, action: str = "block",
                    scope: str = "global", source_memory_id: int | None = None) -> int:
        if action not in ("block", "warn", "ask"):
            raise ValueError(f"action 非法: {action}")
        cur = self.conn.execute(
            "INSERT INTO redlines(pattern, scope, action, statement, source_memory_id, enabled)"
            " VALUES(?,?,?,?,?,1)",
            (pattern, scope, action, statement, source_memory_id),
        )
        rid = int(cur.lastrowid)
        self.audit("core", "redline.add", f"redlines/{rid}", {"pattern": pattern, "action": action})
        return rid

    def add_redline_test(self, redline_id: int, case_type: str, sample_target: str,
                         expected_action: str, sample_detail: str | None = None) -> int:
        if case_type not in ("positive", "negative"):
            raise ValueError(f"case_type 非法: {case_type}")
        cur = self.conn.execute(
            "INSERT INTO redline_tests(redline_id, case_type, sample_target, sample_detail, expected_action)"
            " VALUES(?,?,?,?,?)",
            (redline_id, case_type, sample_target, sample_detail, expected_action),
        )
        return int(cur.lastrowid)

    def match_redlines(self, target: str, project_id: str | None = None) -> list[sqlite3.Row]:
        """guard 数据查询：enabled 红线中匹配 target 的（pattern 子串 + scope 兼容）。"""
        rows = self.conn.execute("SELECT * FROM redlines WHERE enabled=1").fetchall()
        out = []
        for r in rows:
            scope = r["scope"]
            if scope.startswith("project:") and scope.split(":", 1)[1] != (project_id or ""):
                continue
            if r["pattern"] in target:
                out.append(r)
        return out

    # ---- backup（商业化：用户记忆是唯一副本，必须有官方备份通道）----
    def backup_to(self, dest_path: str | Path) -> int:
        """在线一致备份到目标文件（SQLite backup API，期间可继续写入）。返回目标页数。"""
        dest = sqlite3.connect(str(dest_path))
        try:
            with dest:
                self.conn.backup(dest)
            return int(dest.execute("PRAGMA page_count").fetchone()[0])
        finally:
            dest.close()

    def close(self) -> None:
        # 干净关闭：TRUNCATE checkpoint 合并 WAL（Windows 下释放 -wal/-shm 句柄，防临时目录清理失败）
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        self.conn.close()
