# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""P0 冒烟：建库 → 迁移 → 表齐全 → 幂等重跑。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.storage.migrations import runner  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"

EXPECTED_TABLES = {
    "sessions",
    "messages",
    "messages_fts",
    "entities",
    "memories",
    "edges",
    "mem_mentions",
    "audit_log",
}


class TestSchemaMigration(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_migrate_creates_all_core_tables(self):
        applied = runner.migrate(self.conn, MIGRATIONS)
        self.assertEqual(applied, ["0001_core.sql"])
        self.assertEqual(runner.schema_version(self.conn), 1)
        tables = {
            r["name"]
            for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
        }
        missing = EXPECTED_TABLES - tables
        self.assertFalse(missing, f"缺表: {missing}")

    def test_migrate_is_idempotent(self):
        runner.migrate(self.conn, MIGRATIONS)
        applied_again = runner.migrate(self.conn, MIGRATIONS)
        self.assertEqual(applied_again, [], "重复迁移应跳过已应用项")

    def test_migration_gap_rejected(self):
        # 直接把 user_version 拉到 2，0001 就成了"断裂"——必须拒绝而非静默跳过
        self.conn.executescript(
            "CREATE TABLE fake(x); PRAGMA user_version = 2;"
        )
        with self.assertRaises(runner.MigrationError):
            runner.migrate(self.conn, MIGRATIONS)


if __name__ == "__main__":
    unittest.main()
