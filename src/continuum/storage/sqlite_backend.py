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
        accepted: list[int] = []
        skipped = 0
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM messages WHERE session_id=?", (session_id,)
        ).fetchone()
        seq = int(row["m"])
        for msg in messages:
            errs = msg.validate()
            if errs:
                raise ValueError(f"UDF 校验失败: {'; '.join(errs)}")
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

    def search_content(self, query: str, limit: int = 20) -> list[dict]:
        """FTS5 trigram 检索（中文兜底通道；结构化主力在 P1 entities/mentions）。"""
        rows = self.conn.execute(
            "SELECT m.id, m.session_id, m.role, m.ts, m.content,"
            " snippet(messages_fts, 0, '<<', '>>', '…', 24) AS snip"
            " FROM messages_fts f JOIN messages m ON m.id = f.rowid"
            " WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- audit ----
    def audit(self, actor: str, action: str, target: str | None, detail: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO audit_log(ts, actor, action, target, detail_json) VALUES(?,?,?,?,?)",
            (now_iso(), actor, action, target, json.dumps(detail or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def close(self) -> None:
        self.conn.close()
