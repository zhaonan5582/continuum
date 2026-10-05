# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""UDF v1 冻结契约测试：合法/非法/未知字段/版本号。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.udf import UDFError, UDFMessage, loads_udf, parse_udf  # noqa: E402

VALID = {
    "udf_version": 1,
    "ts": "2026-10-05T12:00:00.000Z",
    "role": "user",
    "host": "workbuddy",
    "session_id": "sess-001",
    "content": "把收敛扇的驱逐公式改一下",
    "meta": {"tokens": 42},
}


class TestUDF(unittest.TestCase):
    def test_valid_roundtrip(self):
        msg = parse_udf(dict(VALID))
        self.assertEqual(msg.role, "user")
        self.assertEqual(msg.meta.tokens, 42)
        d = msg.to_udf_dict()
        self.assertEqual(d["udf_version"], 1)
        again = parse_udf(d)
        self.assertEqual(again, msg)

    def test_bad_role_rejected(self):
        bad = dict(VALID, role="system")  # v1 冻结三种角色，无 system
        with self.assertRaises(UDFError):
            parse_udf(bad)

    def test_unknown_field_rejected(self):
        bad = dict(VALID, mood="happy")  # 冻结契约：禁止私有扩展
        with self.assertRaises(UDFError):
            parse_udf(bad)

    def test_wrong_version_rejected(self):
        bad = dict(VALID, udf_version=2)
        with self.assertRaises(UDFError):
            parse_udf(bad)

    def test_bad_ts_rejected(self):
        bad = dict(VALID, ts="2026/10/05 12:00")
        with self.assertRaises(UDFError):
            parse_udf(bad)

    def test_empty_content_allowed(self):
        msg = parse_udf(dict(VALID, content=""))
        self.assertEqual(msg.content, "")

    def test_loads_from_jsonl_line(self):
        import json
        msg = loads_udf(json.dumps(VALID, ensure_ascii=False))
        self.assertIsInstance(msg, UDFMessage)


if __name__ == "__main__":
    unittest.main()
