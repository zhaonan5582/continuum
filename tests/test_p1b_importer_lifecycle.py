# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""WorkBuddy 导入器测试（合成 jsonl 样本，按实测格式构造）+ P1-B 生命周期扳机装配。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.importers import import_workbuddy_session  # noqa: E402
from continuum.server import (  # noqa: E402
    AppendRequest,
    ContinuumServer,
    ExtractScope,
)
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.sweeper import SweeperScheduler  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


def _jsonl(tmp: str) -> Path:
    """按实测 WorkBuddy 格式构造合成会话文件。"""
    p = Path(tmp) / "9cf27da4-test.jsonl"
    lines = [
        {"type": "file-history-snapshot", "id": "snap1", "timestamp": 1786606666212},
        {"type": "message", "id": "m1", "role": "user", "sessionId": "9cf27da4-test",
         "timestamp": 1786606666212, "cwd": "x",
         "content": [{"type": "input_text", "text": "我们决定采用方案A，性能优先。"}]},
        {"type": "reasoning", "id": "r1", "timestamp": 1786606667000, "content": [{"type": "text", "text": "思考中"}]},
        {"type": "message", "id": "m2", "role": "assistant", "sessionId": "9cf27da4-test",
         "timestamp": 1786606670000, "cwd": "x",
         "content": [{"type": "output_text", "text": "好的。"}, {"type": "output_text", "text": "已记录红线：不许动生产库。"}]},
        {"type": "message", "id": "m3", "role": "user", "sessionId": "9cf27da4-test",
         "timestamp": 1786606675000, "cwd": "x", "content": []},   # 空 content → 跳过
        {"type": "ai-title", "id": "t1", "title": "标题"},
    ]
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
    return p


class TestWorkbuddyImporter(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        self.jsonl = _jsonl(self.td.name)

    def tearDown(self):
        self.be.close()
        self.td.cleanup()

    def test_import_filters_and_converts(self):
        rep = import_workbuddy_session(self.jsonl, self.be, title="测试会话")
        # 6 行：1 snapshot 跳 + 3 message 导入（m3 空 content 跳）+ 1 reasoning 跳 + 1 ai-title 跳
        self.assertEqual(rep.total_lines, 6)
        self.assertEqual(rep.imported_messages, 2)
        self.assertEqual(rep.skipped_non_message, 3)   # snapshot + reasoning + ai-title
        self.assertEqual(rep.skipped_empty_content, 1)  # m3 空 content（计 empty 不计 non_message？）
        # 注：m3 有 type=message 但 content=[] → skipped_empty

        # 回读验证：正文拼接（两块 output_text 合并）与 ts 转换
        msgs = list(self.be.iter_session_messages(rep.session_id))
        by_content = {m.content for m in msgs}
        self.assertIn("我们决定采用方案A，性能优先。", [c[:20] + "…" if False else c for c in
                                                    [m.content for m in msgs]] or by_content)
        joined = "\n".join(m.content for m in msgs)
        self.assertIn("好的。", joined)
        self.assertIn("不许动生产库", joined)          # 多块拼接
        self.assertIn("2026-08-13T", msgs[0].ts)      # 毫秒(1786606666212) → ISO8601

    def test_import_idempotent(self):
        rep1 = import_workbuddy_session(self.jsonl, self.be)
        rep2 = import_workbuddy_session(self.jsonl, self.be)   # 重导：全部去重
        self.assertEqual(rep2.imported_messages, 0)


class TestLifecycleWiring(unittest.TestCase):
    """P1-B：生命周期扳机装配——sweep_fn 默认接到 extract；会话结束/定时均触发沉淀。"""

    def test_sweeper_wired_to_extract(self):
        be = SQLiteBackend(":memory:", MIGRATIONS)
        srv = ContinuumServer(be)
        # 装配：sweep_fn 接 extract（P1-B 提供的默认接线）
        sch = SweeperScheduler(backend=be, every_n_rounds=3, every_hours=999)
        sch.sweep_fn = lambda sid: srv.memory_extract(
            ExtractScope(session_id=sid, since_ts=None))

        # 落库 3 轮 → 第 3 轮触发 sweeping → 产生 pending 沉淀
        srv.memory_append(AppendRequest(host_agent="w", external_session_id="s1", messages=(
            __import__("continuum.udf", fromlist=["UDFMessage"]).UDFMessage(
                ts="2026-10-05T10:00:00.000Z", role="user", host="w", session_id="s1",
                content="我们决定采用方案A。"),
        )))
        for i in range(3):
            o = sch.register_round(r_session := 1)
        pending = be.conn.execute(
            "SELECT COUNT(*) c FROM memories WHERE status='pending'"
        ).fetchone()["c"]
        self.assertGreaterEqual(pending, 1, "sweeping 触发后应产生结构化预沉淀")
        be.close()


if __name__ == "__main__":
    unittest.main()
