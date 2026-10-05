# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""七工具契约测试：签名存在、期次 stub 诚实报错、append 真实现、buildflags 双形态。"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.buildflags import detect_build, is_feature_available  # noqa: E402
from continuum.server import (  # noqa: E402
    AppendRequest,
    AuditQuery,
    CompactRange,
    ContinuumServer,
    ExtractScope,
    FeatureNotAvailable,
    Operation,
)
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage, UDFMeta  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


def _msg(content="c", role="user"):
    return UDFMessage(ts="2026-10-05T10:00:00.000Z", role=role, host="w", session_id="s", content=content)


class TestSevenToolsContract(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)

    def tearDown(self):
        self.be.close()

    def test_all_seven_mounted(self):
        for name in ContinuumServer.PHASES:
            self.assertTrue(
                callable(getattr(self.srv, name)),
                f"{name} 未挂载",
            )

    def test_append_real_implementation(self):
        r = self.srv.memory_append(
            AppendRequest(host_agent="w", external_session_id="s1",
                          messages=(_msg("第一条"), _msg("第二条", "assistant")))
        )
        self.assertEqual((r.accepted, r.skipped), (2, 0))
        # 幂等重推
        r2 = self.srv.memory_append(
            AppendRequest(host_agent="w", external_session_id="s1",
                          messages=(_msg("第一条"),))
        )
        self.assertEqual((r2.accepted, r2.skipped), (0, 1))
        self.assertEqual(r2.session_id, r.session_id)

    def test_phase_stubs_are_honest(self):
        """未到期的工具必须诚实报错且携带期次——不静默装死。
        （v1.5 更新：memory_assemble 已随 P2-b 实装，stub 清单只剩 memory_guard。）"""
        with self.assertRaises(FeatureNotAvailable) as cm:
            self.srv.memory_guard(Operation(kind="write", target="x"))
        self.assertIn("P3", str(cm.exception))

    def test_p1p2_tools_no_longer_stub(self):
        """已实装工具的回退防护：防止未来误回退成 stub。"""
        for name in ("memory_append", "memory_extract", "memory_recall",
                     "memory_audit", "memory_compact", "memory_assemble"):
            fn = getattr(self.srv, name)
            try:
                if name == "memory_append":
                    fn(AppendRequest(host_agent="w", external_session_id="s", messages=(_msg(),)))
                elif name == "memory_extract":
                    fn(ExtractScope())
                elif name == "memory_recall":
                    fn("测试")
                elif name == "memory_compact":
                    fn(CompactRange(session_id=1, from_seq=0, to_seq=1))
                else:
                    fn(AuditQuery())
            except FeatureNotAvailable:
                self.fail(f"{name} 被回退成了未实装 stub！")
            except Exception:
                pass  # 其他错误（如参数问题）不算回退


class TestBuildFlags(unittest.TestCase):
    def test_free_build_by_default(self):
        info = detect_build()
        # 本仓库无 continuum.pro 包 → free
        self.assertFalse(info.pro_available)
        self.assertEqual(info.edition, "free")
        self.assertTrue(is_feature_available("core.append"))
        self.assertFalse(is_feature_available("pro.cross_host_merge"))

    def test_unknown_feature_fails_closed(self):
        self.assertFalse(is_feature_available("not.a.feature"))

    def test_pro_injection_detected(self):
        """双构建验证：mock pro 包存在 → detect_build 判定 pro；不存在 → free。
        （v1.1 修正：原断言 `spec is None or spec is not None` 恒真，是假测试。）"""
        from unittest.mock import patch
        from continuum import buildflags

        # 分支 1：pro 包可被找到
        fake_spec = types.SimpleNamespace(name="continuum.pro")
        with patch.object(buildflags.importlib.util, "find_spec", return_value=fake_spec):
            info = buildflags.detect_build()
            self.assertTrue(info.pro_available)
            self.assertEqual(info.edition, "pro")
            self.assertTrue(buildflags.is_feature_available("pro.cross_host_merge"))

        # 分支 2：pro 包不存在
        with patch.object(buildflags.importlib.util, "find_spec", return_value=None):
            info = buildflags.detect_build()
            self.assertFalse(info.pro_available)
            self.assertEqual(info.edition, "free")
            self.assertFalse(buildflags.is_feature_available("pro.cross_host_merge"))


if __name__ == "__main__":
    unittest.main()
