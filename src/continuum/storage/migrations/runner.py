# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""顺序迁移执行器——PRAGMA user_version 驱动，幂等，单迁移单事务。

规则（docs/01 §4：核心冻结、外围演进）：
- 迁移文件按文件名升序执行（0001_core.sql, 0002_*.sql, ...）；
- 已应用（user_version >= 序号）的跳过；
- 每个迁移原子应用：脚本被包进 BEGIN ... PRAGMA user_version=N ... COMMIT 一次执行，
  任一条失败整体回滚（连接须为 isolation_level=None 的显式事务控制模式）。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_FILE_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    pass


def migrate(conn: sqlite3.Connection, migrations_dir: str | Path) -> list[str]:
    """把 migrations_dir 下所有未应用迁移按序执行。返回本次实际应用的文件名列表。"""
    d = Path(migrations_dir)
    files: list[tuple[int, Path]] = []
    for p in sorted(d.glob("*.sql")):
        m = _FILE_RE.match(p.name)
        if not m:
            raise MigrationError(f"迁移文件名不合规（需 NNNN_name.sql）: {p.name}")
        files.append((int(m.group(1)), p))
    files.sort(key=lambda t: t[0])

    current = schema_version(conn)
    if files and current > files[-1][0]:
        # 库比代码新：迁移目录被回退/换了环境。静默跳过会掩盖危险状态，必须报错。
        raise MigrationError(
            f"数据库 schema 版本 {current} 高于代码内最高迁移 {files[-1][0]}——库比代码新，拒绝操作"
        )

    applied_now: list[str] = []
    for ver, path in files:
        current = schema_version(conn)
        if ver <= current:
            continue
        if ver != current + 1:
            raise MigrationError(
                f"迁移序号断裂：当前 user_version={current}，下一个文件是 {path.name}（期望 {current + 1:04d}_*）"
            )
        sql = path.read_text(encoding="utf-8")
        # 原子应用：user_version 与 DDL 同事务提交（PRAGMA user_version 是事务性的）
        script = f"BEGIN;\n{sql}\nPRAGMA user_version = {ver};\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error as e:
            conn.rollback()  # COMMIT 未到达时回滚显式事务，杜绝半应用状态
            raise MigrationError(f"迁移 {path.name} 失败（已回滚）: {e}") from e
        applied_now.append(path.name)
    return applied_now


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])
