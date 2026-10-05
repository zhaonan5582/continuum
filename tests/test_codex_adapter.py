# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""codex 适配器测试（用真实 codex 会话文件实测，非合成）。"""
from __future__ import annotations

import glob
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json  # noqa: E402

from continuum.adapters import CodexSessionAdapter  # noqa: E402

_real = sorted(glob.glob(str(Path.home() / ".codex" / "sessions" / "**" / "rollout-*.jsonl"),
                         recursive=True))


class TestCodexAdapter(unittest.TestCase):
    def setUp(self):
        self.sa = CodexSessionAdapter()

    @unittest.skipIf(not _real, "本机无 codex 会话文件")
    def test_real_codex_session(self):
        """用本机真实 codex 会话文件实测解析。"""
        lines = Path(_real[0]).read_text(encoding="utf-8").splitlines()
        out = self.sa.to_udf(lines)
        # developer 注入必须被滤掉
        self.assertTrue(all(m.host == "codex" for m in out))
        self.assertTrue(all(m.role in ("user", "assistant") for m in out))
        self.assertTrue(all(m.content.strip() for m in out))

    def test_synthetic_developer_filtered(self):
        lines = [
            json.dumps({"ordinal": "0", "timestamp": "2026-08-17T04:45:26.057Z",
                        "type": "session_meta", "payload": {}}),
            json.dumps({"ordinal": "1", "timestamp": "2026-08-17T04:45:27.000Z",
                        "type": "response_item", "payload": {
                            "type": "message", "role": "developer",
                            "content": [{"type": "input_text", "text": "<skills>系统注入</skills>"}]}}),
            json.dumps({"ordinal": "2", "timestamp": "2026-08-17T04:45:28.000Z",
                        "type": "response_item", "payload": {
                            "type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": "帮我写个脚本"}]}}),
            json.dumps({"ordinal": "3", "timestamp": "2026-08-17T04:45:30.000Z",
                        "type": "response_item", "payload": {
                            "type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": "好的。"}]}}),
        ]
        out = self.sa.to_udf(lines)
        self.assertEqual(len(out), 2)   # developer 被滤
        self.assertEqual(out[0].content, "帮我写个脚本")




if __name__ == "__main__":
    unittest.main()
