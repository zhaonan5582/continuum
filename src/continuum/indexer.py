# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""召回索引管理（lite / fast 双模式，docs/16 实测数据支撑）。

| 模式 | 机制 | 实测（6 万条） | 存储 |
|------|------|---------------|------|
| lite | 全表 LIKE + 归一（现状） | 291ms | 基线 |
| fast | content_norm 列 + FTS5 trigram | **1-2ms（100+ 倍）** | **+78%** |

**两种模式都支持、可随时切换**（楠哥 2026-10-07：选择权留给用户）。
- 切 fast：回填 content_norm + 建 FTS 索引（实测 1.2 万条：回填 0.3s、建索引 1.0s）；
- 切 lite：DROP FTS + content_norm 置 NULL（**原文一行不动**，完全可逆）。

**归一化规格（必须两侧一致，否则召回失真）**：删除所有空白（含 NBSP）、零宽字符
（U+200B–U+200F）、行/段分隔（U+2028/2029）、BOM（U+FEFF）、软连字符（U+00AD），
并把反斜杠折叠为单（路径转义差异）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

NORM_RE = re.compile(r"[\s\u200b-\u200f\u2028\u2029\ufeff\u00ad]+")

FTS_TABLE = "messages_norm_fts"
# 触发器名（外部内容表模式必须同步索引，否则新消息检索不到）
TRIGGERS = ("messages_norm_ai", "messages_norm_au", "messages_norm_ad")


def normalize(s: str) -> str:
    """归一化：两侧（入库与查询）必须用同一函数——否则召回失真（实测教训）。"""
    if not s:
        return ""
    return NORM_RE.sub("", s.replace("\\\\", "\\"))


def fts_ready(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (FTS_TABLE,)).fetchone()
    return row is not None


def fts_count(conn: sqlite3.Connection) -> int:
    """FTS 索引行数（用于一致性核对：应与已填 content_norm 的行数一致）。"""
    if not fts_ready(conn):
        return -1
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0])
    except sqlite3.Error:
        return -1


def needs_repair(conn: sqlite3.Connection) -> bool:
    """fast 模式下是否需要重建索引（触发器缺失/行数不符 → 需修复）。"""
    if not fts_ready(conn):
        return True
    try:
        filled = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content_norm IS NOT NULL").fetchone()[0]
    except sqlite3.OperationalError:
        return True
    trig = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
        " AND name IN (?,?,?)", TRIGGERS).fetchone()[0]
    if trig < 3:
        return True                            # 触发器不全（新消息会漏索引）
    return fts_count(conn) < filled


def index_status(conn: sqlite3.Connection, mode: str | None) -> dict:
    n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    filled = 0
    try:
        filled = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE content_norm IS NOT NULL").fetchone()[0]
    except sqlite3.OperationalError:
        filled = -1                                    # 列不存在（未迁移）
    return {"mode": mode, "chosen": mode is not None, "messages": n,
            "norm_filled": filled, "fts_ready": fts_ready(conn),
            "fts_rows": fts_count(conn),
            "consistent": (mode != "fast") or (not needs_repair(conn))}


def build_fast_index(conn: sqlite3.Connection, batch: int = 500) -> dict:
    """切换到 fast 模式：回填 content_norm + 建 FTS5 trigram 索引（幂等）。"""
    t_fill_start = __import__("time").perf_counter()
    # 加列（若迁移未跑）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
    if "content_norm" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN content_norm TEXT")
        conn.commit()
    # 回填（只填 NULL——幂等）
    todo = conn.execute(
        "SELECT id, content FROM messages WHERE content_norm IS NULL").fetchall()
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        conn.executemany("UPDATE messages SET content_norm=? WHERE id=?",
                         [(normalize(c), mid) for mid, c in chunk])
        conn.commit()
    fill_sec = __import__("time").perf_counter() - t_fill_start

    # 建 FTS（外部内容表模式；trigram 分词支持中文与短语查询）
    t_fts_start = __import__("time").perf_counter()
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
        "content_norm, content='messages', content_rowid='id', tokenize='trigram')")
    conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")
    # ★ 触发器：外部内容表模式下，**后续写入/更新/删除必须同步索引**——
    #   否则 rebuild 之后新增的消息永远检索不到（隐性 bug，数天后才暴露）。
    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS messages_norm_ai AFTER INSERT ON messages BEGIN"
        f"  INSERT INTO {FTS_TABLE}(rowid, content_norm)"
        f"  VALUES (new.id, COALESCE(new.content_norm, ''));"
        f" END")
    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS messages_norm_au AFTER UPDATE OF content_norm ON messages BEGIN"
        f"  INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, content_norm)"
        f"  VALUES ('delete', old.id, COALESCE(old.content_norm, ''));"
        f"  INSERT INTO {FTS_TABLE}(rowid, content_norm)"
        f"  VALUES (new.id, COALESCE(new.content_norm, ''));"
        f" END")
    conn.execute(
        f"CREATE TRIGGER IF NOT EXISTS messages_norm_ad AFTER DELETE ON messages BEGIN"
        f"  INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, content_norm)"
        f"  VALUES ('delete', old.id, COALESCE(old.content_norm, ''));"
        f" END")
    conn.commit()
    fts_sec = __import__("time").perf_counter() - t_fts_start
    return {"filled": len(todo), "fill_sec": round(fill_sec, 2), "fts_sec": round(fts_sec, 2),
            "triggers": len(TRIGGERS)}


def drop_fast_index(conn: sqlite3.Connection) -> dict:
    """切回 lite：删 FTS + 清空归一列（**原文不动**，完全可逆）。"""
    dropped = False
    for t in TRIGGERS:                       # 先删触发器（否则 DROP 表会报错/残留）
        conn.execute(f"DROP TRIGGER IF EXISTS {t}")
    if fts_ready(conn):
        conn.execute(f"DROP TABLE {FTS_TABLE}")
        dropped = True
    cleared = 0
    try:
        cur = conn.execute("UPDATE messages SET content_norm = NULL"
                           " WHERE content_norm IS NOT NULL")
        cleared = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
    except sqlite3.OperationalError:
        pass
    return {"fts_dropped": dropped, "norm_cleared": cleared}
