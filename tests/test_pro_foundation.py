# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Pro 地基测试：license 正式模块 + recall 宿主作用域。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from continuum.license import (  # noqa: E402
    PLAN_FEATURES,
    activate,
    features_unlocked,
    is_pro,
    load_state,
    validate_key,
)
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.server import ContinuumServer  # noqa: E402
from continuum.server.tools import AppendRequest  # noqa: E402
from continuum.udf import UDFMessage  # noqa: E402

MIGRATIONS = REPO / "src" / "continuum" / "storage" / "migrations" / "sql"


def _good_key(seed: int = 0) -> str:
    """构造校验和合法的 16 位 key（骨架算法：末位 = 前15位 ord 和 % 36）。"""
    base = "AAAA-BBBB-CCCC-DD"
    digits = base.replace("-", "") + "E"
    chk = (sum(ord(c) for c in digits[:15]) + seed) % 36
    last = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[chk]
    return "-".join([digits[0:4], digits[4:8], digits[8:12], digits[12:15] + last])


class TestLicense(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name) / "license.json"

    def tearDown(self):
        self.td.cleanup()

    def test_initial_state_free(self):
        st = load_state(self.path)
        self.assertFalse(st.activated)
        self.assertEqual(st.plan, "free")
        self.assertEqual(features_unlocked(self.path), ())

    def test_validate_format(self):
        ok, err = validate_key("bad")
        self.assertFalse(ok)
        self.assertIn("格式", err)

    def test_activate_pro_unlocks_cross_host(self):
        key = _good_key()
        st = activate(key, "pro", path=self.path)
        self.assertTrue(st.activated)
        self.assertEqual(st.plan, "pro")
        self.assertTrue(is_pro(self.path))
        self.assertIn("cross_host", features_unlocked(self.path))

    def test_bad_checksum_rejected(self):
        key = _good_key()
        bad = key[:-1] + ("0" if key[-1] != "0" else "1")
        with self.assertRaises(ValueError):
            activate(bad, "pro", path=self.path)
        st = load_state(self.path)
        self.assertFalse(st.activated)   # 拒绝后状态不变

    def test_plan_features_table(self):
        self.assertEqual(PLAN_FEATURES["free"], ())
        self.assertIn("cross_host", PLAN_FEATURES["pro"])
        self.assertIn("cloud_sync", PLAN_FEATURES["pro_cloud"])


class TestRecallHostScope(unittest.TestCase):
    """宿主作用域过滤：免费版单宿主隔离 / Pro 跨宿主的地基能力。"""

    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        for host, sid, content in [
            ("workbuddy", "wb-1", "记住：工作台的部署一律走通道1"),
            ("codex", "cx-1", "记住：codex 的代码审查用严格模式"),
        ]:
            self.srv.memory_append(AppendRequest(
                host_agent=host, external_session_id=sid,
                messages=(UDFMessage(ts="2026-01-01T00:00:00.000Z", role="user",
                                     host=host, session_id=sid, content=content),)))
        # 沉淀：原文 → memories（无 judge 中文启发式 + confirm 直接 active）
        from continuum.server.tools import ExtractScope
        self.srv.memory_extract(ExtractScope(confirm=True))

    def tearDown(self):
        self.be.close()

    def test_global_recall_sees_both(self):
        r_wb = self.srv.memory_recall("通道1", limit=10)
        r_cx = self.srv.memory_recall("严格模式", limit=10)
        self.assertTrue(any("工作台" in i.statement for i in r_wb.items), "workbuddy 记忆可召回")
        self.assertTrue(any("严格模式" in i.statement for i in r_cx.items), "codex 记忆可召回")

    def test_host_scope_filters(self):
        r_wb = self.srv.memory_recall("通道1", limit=10, host_scope="workbuddy")
        self.assertTrue(any("通道1" in i.statement for i in r_wb.items))
        r_cx = self.srv.memory_recall("严格模式", limit=10, host_scope="codex")
        self.assertTrue(any("严格模式" in i.statement for i in r_cx.items))
        # 作用域互斥：workbuddy 作用域查不到 codex 的内容
        r_cross = self.srv.memory_recall("严格模式", limit=10, host_scope="workbuddy")
        self.assertFalse(any("严格模式" in i.statement for i in r_cross.items))


if __name__ == "__main__":
    unittest.main()
