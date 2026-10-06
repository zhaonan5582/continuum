# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""多宿主探测测试（docs/10 第一层：注册表 + 只读探测）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from continuum.agents import build_registry, detect_all  # noqa: E402


class TestAgentRegistry(unittest.TestCase):
    def test_registry_covers_first_batch(self):
        keys = {p.key for p in build_registry()}
        self.assertTrue({"workbuddy", "codex", "claude-code", "hermes"} <= keys,
                        f"首批宿主必须齐全，实际: {keys}")

    def test_detect_empty_home(self):
        with tempfile.TemporaryDirectory() as td:
            rows = detect_all(Path(td))
            self.assertTrue(all(not r.found for r in rows), "空 home 不得探出任何宿主")

    def test_detect_with_fake_hosts(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".workbuddy" / "projects" / "ws1").mkdir(parents=True)
            (home / ".workbuddy" / "projects" / "ws1" / "s1.jsonl").write_text("{}\n", encoding="utf-8")
            (home / ".codex" / "sessions" / "2026").mkdir(parents=True)
            for i in range(3):
                (home / ".codex" / "sessions" / "2026" / f"s{i}.jsonl").write_text("{}\n", encoding="utf-8")
            rows = {r.profile.key: r for r in detect_all(home)}
            self.assertTrue(rows["workbuddy"].found)
            self.assertEqual(rows["workbuddy"].session_count, 1)
            self.assertTrue(rows["codex"].found)
            self.assertEqual(rows["codex"].session_count, 3)
            self.assertFalse(rows["claude-code"].found)
            self.assertFalse(rows["hermes"].found)

    def test_mechanisms_reflect_support(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".claude" / "projects" / "x").mkdir(parents=True)
            (home / ".claude" / "projects" / "x" / "a.jsonl").write_text("{}\n", encoding="utf-8")
            rows = {r.profile.key: r for r in detect_all(home)}
            cc = rows["claude-code"]
            self.assertIn("hooks", cc.mechanisms, "CC 声明支持 hooks")
            self.assertIn("session-file", cc.mechanisms)
            self.assertIn("proxy", cc.mechanisms, "代理机制与宿主无关，恒可用")
            # codex 不声明 hooks
            (home / ".codex").mkdir(exist_ok=True)
            rows = {r.profile.key: r for r in detect_all(home)}
            self.assertNotIn("hooks", rows["codex"].mechanisms)


class TestFullRegistry(unittest.TestCase):
    """全量注册表（35+ 宿主）与安全红线（永读不得碰凭据）。"""

    def test_registry_is_broad(self):
        from continuum.agents import build_registry
        regs = build_registry()
        self.assertGreaterEqual(len(regs), 30, f"主流宿主覆盖不足: {len(regs)}")
        keys = {a.key for a in regs}
        for must in ("claude-code", "codex", "workbuddy", "cursor", "cline", "aider",
                     "continue", "gemini-cli", "opencode", "goose", "hermes", "zed",
                     "qwen-code", "roo-code", "kilo-code", "openhands", "trae",
                     "windsurf", "amp", "crush", "kimi", "kiro", "copilot-cli"):
            self.assertIn(must, keys, f"缺主流宿主: {must}")

    def test_format_kinds_declared(self):
        from continuum.agents import build_registry
        kinds = {a.format_kind for a in build_registry()}
        self.assertTrue({"jsonl", "json", "sqlite"} <= kinds, f"格式分类异常: {kinds}")
        for a in build_registry():
            self.assertIn(a.support_level,
                          ("parser_ready", "profile_only", "planned"))

    def test_never_read_guard(self):
        from continuum.agents import is_never_read
        for bad in ("/x/.claude/.credentials.json", "/x/auth.json",
                    "/x/.continue/.env", "/x/secrets.json", "/x/mcp-auth.json",
                    "/x/config.toml", "/x/state.vscdb".replace("vscdb", "key")):
            self.assertTrue(is_never_read(bad), f"凭据未拦截: {bad}")
        for ok in ("/x/projects/s1.jsonl", "/x/sessions/rollout-1.jsonl",
                   "/x/threads.db", "/x/sessions/a.json"):
            self.assertFalse(is_never_read(ok), f"会话文件被误拦: {ok}")

    def test_all_profiles_declare_never_read(self):
        from continuum.agents import build_registry
        for a in build_registry():
            self.assertTrue(a.never_read, f"{a.key} 未声明安全红线")


if __name__ == "__main__":
    unittest.main()


class TestMultiHostFeed(unittest.TestCase):
    """N1 多宿主喂食（docs/10）：通用引擎 + 嗅探器。"""

    def _mk_be(self, td):
        from continuum.storage import SQLiteBackend
        from pathlib import Path
        return SQLiteBackend(str(Path(td) / "t.db"), REPO / "src" / "continuum" / "storage" / "migrations" / "sql")

    def test_sniffer_shapes(self):
        from continuum.importers.sources import _sniff_message
        # A: type=message 单层
        self.assertEqual(_sniff_message({"type": "message", "role": "user", "content": "hi"}), ("user", "hi"))
        # B: codex 双层
        self.assertEqual(_sniff_message({"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "yo"}]}}), ("assistant", "yo"))
        # C: OpenAI 风格
        self.assertEqual(_sniff_message({"role": "user", "content": "plain"}), ("user", "plain"))
        # D: CC 嵌套 message
        self.assertEqual(_sniff_message({"type": "user", "message": {"role": "user", "content": "cc"}}), ("user", "cc"))
        # 过滤：system/developer 不算对话；无文本不算
        self.assertIsNone(_sniff_message({"type": "message", "role": "developer", "content": "x"}))
        self.assertIsNone(_sniff_message({"type": "world_state", "payload": {}}))

    def test_multihost_sweep(self):
        import json, tempfile, time
        from pathlib import Path
        from continuum.importers.feed_base import SessionFeeder
        from continuum.importers.sources import GenericJsonlSource, WorkbuddySource
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            be = self._mk_be(td)
            # 宿主 A：workbuddy 格式
            a = td / "wb" / "ws1"; a.mkdir(parents=True)
            (a / "s1.jsonl").write_text(json.dumps({"type": "message", "timestamp": int(time.time()*1000), "role": "user", "content": [{"text": "wb 内容"}]}, ensure_ascii=False) + chr(10), encoding="utf-8")
            # 宿主 B：通用 jsonl 形态（OpenAI 风格）
            b = td / "other" / "proj"; b.mkdir(parents=True)
            (b / "s2.jsonl").write_text(json.dumps({"role": "assistant", "content": "other 内容", "timestamp": int(time.time()*1000)}, ensure_ascii=False) + chr(10), encoding="utf-8")
            feed = SessionFeeder(be, [
                WorkbuddySource(root=td / "wb"),
                GenericJsonlSource(host="other-agent", root=td / "other"),
            ], state_path=td / "state.json")
            feed._initialized_at = 0
            r = feed.sweep_now()
            self.assertEqual(r.new_messages, 2)
            self.assertEqual(r.by_host.get("workbuddy"), 1)
            self.assertEqual(r.by_host.get("other-agent"), 1)
            hosts = {x[0] for x in be.conn.execute("SELECT DISTINCT host_agent FROM sessions")}
            self.assertEqual(hosts, {"workbuddy", "other-agent"})
            be.close()


    def test_state_save_merges_across_instances(self):
        """多进程 state 覆盖事故固化：两个实例共享 state，后写者不得抹掉先写者。"""
        import json, tempfile
        from pathlib import Path
        from continuum.importers.feed_base import SessionFeeder
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            sp = td / "s.json"
            be = self._mk_be(td)
            f1 = SessionFeeder(be, [], state_path=sp)
            f1._offsets[str(td / "a.jsonl")] = 100
            f1._save_state()
            f2 = SessionFeeder(be, [], state_path=sp)     # 模拟另一进程（旧映像）
            f2._offsets[str(td / "b.jsonl")] = 200
            f2._save_state()
            merged = json.loads(sp.read_text(encoding="utf-8"))["offsets"]
            self.assertIn(str(td / "a.jsonl"), merged, "先写者的登记被覆盖了")
            self.assertIn(str(td / "b.jsonl"), merged)
            be.close()


class TestSqliteSource(unittest.TestCase):
    """N4：SQLite 族（Z Code 实测结构的假库验证，不依赖真机）。"""

    def _mk_zcode_db(self, path):
        import json, sqlite3
        c = sqlite3.connect(str(path))
        c.execute("CREATE TABLE message(id TEXT, session_id TEXT, time_created INTEGER, data TEXT)")
        c.execute("CREATE TABLE part(id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT)")
        c.execute("INSERT INTO message VALUES('m1','s1',1000,?)",
                  (json.dumps({"role": "user"}),))
        c.execute("INSERT INTO message VALUES('m2','s1',2000,?)",
                  (json.dumps({"role": "assistant"}),))
        c.execute("INSERT INTO part VALUES('p1','m1','s1',1000,?)",
                  (json.dumps({"type": "text", "text": "用户问题原文"}),))
        c.execute("INSERT INTO part VALUES('p2','m2','s1',2000,?)",
                  (json.dumps({"type": "reasoning", "text": "思维链不应入库"}),))
        c.execute("INSERT INTO part VALUES('p3','m2','s1',2000,?)",
                  (json.dumps({"type": "text", "text": "助手回答原文"}),))
        c.commit()
        c.close()

    def test_reads_text_parts_only(self):
        import tempfile
        from pathlib import Path
        from continuum.importers.sources import ZCodeSource
        with tempfile.TemporaryDirectory() as t:
            db = Path(t) / "db.sqlite"
            self._mk_zcode_db(db)
            msgs, wm = ZCodeSource().read(db, 0)
            self.assertEqual(len(msgs), 2, "只应取 type=text 的两个 part")
            self.assertEqual([m.role for m in msgs], ["user", "assistant"])
            self.assertIn("用户问题原文", msgs[0].content)
            self.assertNotIn("思维链", msgs[1].content, "reasoning 必须被过滤")
            self.assertEqual(wm, 2, "水位 = message 最大 rowid")
            # 增量：再读应无新内容
            msgs2, wm2 = ZCodeSource().read(db, wm)
            self.assertEqual(msgs2, [])
            self.assertEqual(wm2, wm)

    def test_missing_tables_returns_empty(self):
        import sqlite3, tempfile
        from pathlib import Path
        from continuum.importers.sources import ZCodeSource
        with tempfile.TemporaryDirectory() as t:
            db = Path(t) / "empty.db"
            sqlite3.connect(str(db)).close()
            msgs, wm = ZCodeSource().read(db, 0)
            self.assertEqual((msgs, wm), ([], 0), "无 message/part 表 → 空且不抛")

    def test_unreadable_file_does_not_raise(self):
        import tempfile
        from pathlib import Path
        from continuum.importers.sources import ZCodeSource
        with tempfile.TemporaryDirectory() as t:
            bad = Path(t) / "not_a_db.sqlite"
            bad.write_text("not a database", encoding="utf-8")
            msgs, wm = ZCodeSource().read(bad, 5)
            self.assertEqual((msgs, wm), ([], 5), "坏文件 → 水位不变、不抛")


class TestSnifferMatrix(unittest.TestCase):
    """N5：未知宿主形态矩阵（11 正例 + 5 反例）——普适性与防误报双向契约。"""

    POS = [
        ({"type": "message", "role": "user", "content": "text"}, ("user", "text")),
        ({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                               "content": [{"text": "reply"}]}}, ("assistant", "reply")),
        ({"role": "user", "content": "plain"}, ("user", "plain")),
        ({"type": "user", "message": {"role": "user", "content": "cc"}}, ("user", "cc")),
        ({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "block"}]},
         ("user", "block")),
        ({"kind": "chat", "data": {"role": "user", "content": "nested"}}, ("user", "nested")),
        ({"event": "message", "sender": "user", "text": "event style"}, ("user", "event style")),
        ({"text": "bare text", "who": "user"}, ("user", "bare text")),
        ({"from": "ai", "body": "bot reply"}, ("assistant", "bot reply")),
        ({"author": "human", "value": "human msg"}, ("user", "human msg")),
    ]
    NEG = [
        {"type": "world_state", "payload": {"full": True}},
        {"role": "system", "content": "sys"},
        {"role": "developer", "content": "dev"},
        {"role": "user", "content": "   "},
        {"token_usage_record": 1, "total": 100},
    ]

    def test_positive_shapes_recognized(self):
        from continuum.importers.sources import _sniff_message
        for d, expect in self.POS:
            self.assertEqual(_sniff_message(d), expect, f"未识别: {d}")

    def test_negative_shapes_rejected(self):
        from continuum.importers.sources import _sniff_message
        for d in self.NEG:
            self.assertIsNone(_sniff_message(d), f"误报: {d}")
