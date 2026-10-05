# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""P2-a 验收：按条判决压缩（无 judge 启发式 + judge mock）+ 轨道 A judge 抽取 + 隐私边界。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.compact import compact_session  # noqa: E402
from continuum.judges import Judge, MemoryJudgement, NullJudge  # noqa: E402
from continuum.server import (  # noqa: E402
    AppendRequest,
    CompactRange,
    ContinuumServer,
    ExtractScope,
)
from continuum.storage import SQLiteBackend  # noqa: E402
from continuum.udf import UDFMessage  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "continuum" / "storage" / "migrations" / "sql"


def _msg(i, role, content):
    return UDFMessage(ts=f"2026-10-05T10:{i//60:02d}:{i%60:02d}.000Z", role=role,
                      host="workbuddy", session_id="s1", content=content)


class _ScriptedJudge(Judge):
    """脚本化 judge：按关键词返回固定判决（测试用，不联网）。"""

    def __init__(self, drop_keywords=(), classify_map=None):
        self.drop_keywords = drop_keywords
        self.classify_map = classify_map or {}

    def classify(self, statement, context=""):
        for kw, (kind, conf) in self.classify_map.items():
            if kw in statement:
                return MemoryJudgement(kind=kind, confidence=conf, reason="scripted")
        return MemoryJudgement(kind="none", confidence=0.0, reason="no match")

    def judge_message_retention(self, role, content, has_downstream_memory):
        # 无条件建议 drop（命中关键词）——引擎负责按"有无沉淀指针"降级/执行
        for kw in self.drop_keywords:
            if kw in content:
                return "drop", "scripted drop"
        return "full", "scripted full"


class TestCompactNoJudge(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        self.sid = self.be.ensure_session("workbuddy", "s1")
        # 10 条：首2条（保护）+ 资产/工具/普通 + 尾4条（保护）
        msgs = (
            _msg(0, "user", "开场：今天聊压缩引擎的设计。"),
            _msg(1, "assistant", "好的，先看数据层。"),
            _msg(2, "user", "我们决定采用方案A，性能优先。"),            # 资产 → full
            _msg(3, "tool", '{"result": "一堆很长的工具返回内容" * 50}'),  # tool → truncate
            _msg(4, "user", "闲聊内容，无关紧要的流水账。"),               # 已沉淀 → truncate
            _msg(5, "assistant", "收尾：方案确认。"),
            _msg(6, "user", "补充一点。"),
            _msg(7, "assistant", "收到。"),
            _msg(8, "user", "还有个细节。"),
            _msg(9, "assistant", "好的，结束。"),
        )
        self.srv.memory_append(AppendRequest(host_agent="workbuddy", external_session_id="s1", messages=msgs))
        # 第 4 条（闲聊）制造沉淀指针 → 允许 truncate/drop
        target = self.be.conn.execute(
            "SELECT id FROM messages WHERE content LIKE '闲聊%'"
        ).fetchone()["id"]
        self.be.conn.execute(
            "INSERT INTO memories(kind, statement, source_message_id, session_id, stated_by,"
            " evidence_level, created_at, valid_from, status)"
            " VALUES('fact','闲聊内容沉淀',?,?,'agent','inferred','t','t','active')",
            (target, self.sid),
        )

    def tearDown(self):
        self.be.close()

    def _compact(self):
        return compact_session(self.be, self.sid, 1, 10, judge=None)

    def test_head_tail_protected(self):
        entries, _, _ = self._compact()
        by_seq = {e["seq"]: e for e in entries}
        self.assertEqual(by_seq[1]["verdict"], "full")
        self.assertEqual(by_seq[2]["verdict"], "full")
        self.assertEqual(by_seq[9]["verdict"], "full")
        self.assertIn("首尾保护", by_seq[1]["reason"])

    def test_asset_forced_full_no_judge(self):
        entries, _, _ = self._compact()
        decision = next(e for e in entries if e["seq"] == 3)
        self.assertEqual(decision["verdict"], "full")
        self.assertIn("资产类", decision["reason"])

    def test_pointered_truncatable(self):
        entries, _, _ = self._compact()
        idle = next(e for e in entries if e["seq"] == 5)
        self.assertEqual(idle["verdict"], "truncate")
        self.assertIsNotNone(idle["pointer"])

    def test_no_judge_never_drops(self):
        entries, _, _ = self._compact()
        self.assertTrue(all(e["verdict"] != "drop" for e in entries),
                        "无 judge 时禁止 drop（宁可胖不可丢）")

    def test_plan_counts(self):
        plan = self.srv.memory_compact(CompactRange(session_id=self.sid, from_seq=1, to_seq=10))
        self.assertEqual(len(plan.entries), 10)
        self.assertGreater(plan.chars_before, 0)
        verdicts = [e.verdict for e in plan.entries]
        self.assertIn("truncate", verdicts)


class TestCompactWithJudge(unittest.TestCase):
    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        self.sid = self.be.ensure_session("workbuddy", "s2")
        msgs = (
            _msg(0, "user", "开场内容。"),
            _msg(1, "assistant", "回应开场。"),
            _msg(2, "user", "可以丢掉的琐碎内容：今天午饭吃了面。"),
            _msg(3, "assistant", "收尾内容。"),
            _msg(4, "user", "结尾补充。"),
            _msg(5, "assistant", "真正的结束。"),
        )
        self.srv.memory_append(AppendRequest(host_agent="workbuddy", external_session_id="s2", messages=msgs + tuple(
            _msg(6 + i, "user" if i % 2 else "assistant", f"填充对话{i}：一些后续讨论内容。") for i in range(6)
        )))
        # 中间那条制造沉淀指针（judge 才允许 drop）
        target = self.be.conn.execute(
            "SELECT id FROM messages WHERE content LIKE '可以丢掉%'").fetchone()["id"]
        self.be.conn.execute(
            "INSERT INTO memories(kind, statement, source_message_id, session_id, stated_by,"
            " evidence_level, created_at, valid_from, status)"
            " VALUES('fact','午饭吃面',?,?,'agent','inferred','t','t','active')",
            (target, self.sid),
        )

    def tearDown(self):
        self.be.close()

    def test_judge_drop_only_with_pointer(self):
        judge = _ScriptedJudge(drop_keywords=("可以丢掉",))
        entries, _, _ = compact_session(self.be, self.sid, 1, 12, judge=judge)
        drop_e = next(e for e in entries if e["seq"] == 3)
        self.assertEqual(drop_e["verdict"], "drop")
        self.assertIsNotNone(drop_e["pointer"])

    def test_judge_drop_without_pointer_downgrades(self):
        """judge 建议 drop 但无沉淀指针 → 引擎强制降级 truncate（无损层承诺，机制优于自觉）。"""
        judge = _ScriptedJudge(drop_keywords=("收尾内容",))   # seq4：中间区、无沉淀指针
        entries, _, _ = compact_session(self.be, self.sid, 1, 12, judge=judge)
        e = next(x for x in entries if x["seq"] == 4)
        self.assertEqual(e["verdict"], "truncate", "无指针的 drop 必须被引擎降级")
        self.assertIn("降级", e["reason"])

    def test_judge_error_fails_safe(self):
        class _BoomJudge(_ScriptedJudge):
            def judge_message_retention(self, role, content, has_downstream_memory):
                raise RuntimeError("网络炸了")

        entries, _, _ = compact_session(self.be, self.sid, 1, 6, judge=_BoomJudge())
        self.assertTrue(all(e["verdict"] == "full" for e in entries),
                        "judge 崩溃必须全量保留（容错承诺）")


class TestJudgeExtractOrbitA(unittest.TestCase):
    """轨道 A：judge 语义抽取（高置信 → active，低置信 → pending）。"""

    def setUp(self):
        self.be = SQLiteBackend(":memory:", MIGRATIONS)
        self.srv = ContinuumServer(self.be)
        self.srv.memory_append(AppendRequest(
            host_agent="workbuddy", external_session_id="s", messages=(
                _msg(1, "user", "我们决定采用方案A。"),
                _msg(2, "user", "今天午饭吃面。"),
            ),
        ))

    def _extract_with(self, judge):
        # 直接走 extract 模块的 judge 通道（模拟轨道 A）
        from continuum.extract.extractor import candidates_from_messages, run_extraction
        rows = self.be.fetch_messages_since(None, limit=50)
        # 注入 judge 判决：按语句前缀映射
        orig = candidates_from_messages

        def patched(messages):
            return orig(messages)

        # 简化：直接调 run_extraction + judge 后处理
        produced, _, scanned = run_extraction(self.be, None, rows)
        # judge 后处理：对每条 pending，judge 重分类
        for r in self.be.conn.execute("SELECT id, statement FROM memories WHERE status='pending'"):
            j = judge.classify(r["statement"])
            if j.confidence >= 0.7 and j.kind != "none":
                self.be.conn.execute(
                    "UPDATE memories SET kind=?, status='active' WHERE id=?", (j.kind, r["id"]))
        return produced

    def test_judge_high_confidence_activates(self):
        class _Good(_ScriptedJudge):
            def classify(self, statement, context=""):
                if "决定" in statement:
                    return MemoryJudgement(kind="decision", confidence=0.95, reason="scripted")
                return MemoryJudgement(kind="none", confidence=0.0, reason="scripted")

        self._extract_with(_Good())
        rows = self.be.conn.execute(
            "SELECT status FROM memories WHERE statement LIKE '%方案A%'").fetchall()
        self.assertEqual(rows[0]["status"], "active", "高置信 judge 判决应升 active")

    def test_low_confidence_stays_pending(self):
        class _Timid(_ScriptedJudge):
            def classify(self, statement, context=""):
                return MemoryJudgement(kind="none", confidence=0.0, reason="unsure")

        self._extract_with(_Timid())
        rows = self.be.conn.execute(
            "SELECT status FROM memories WHERE statement LIKE '%方案A%'").fetchall()
        self.assertEqual(rows[0]["status"], "pending", "低置信必须保持 pending")

    def test_recall_does_not_show_pending_after_judge(self):
        class _Good(_ScriptedJudge):
            def classify(self, statement, context=""):
                if "午饭" in statement:
                    return MemoryJudgement(kind="none", confidence=0.0, reason="chitchat")
                if "决定" in statement:
                    return MemoryJudgement(kind="decision", confidence=0.95, reason="scripted")
                return MemoryJudgement(kind="none", confidence=0.0, reason="unsure")

        self._extract_with(_Good())
        r = self.srv.memory_recall("午饭吃面")
        kinds = {i.kind for i in r.items}
        self.assertNotIn("decision", kinds)     # 未确认的不出现
        self.assertIn("verbatim", kinds)        # 但原文（证据）始终可查


if __name__ == "__main__":
    unittest.main()
