# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""代理层测试：格式检测/注入/提取/passthrough/红线拦截/token 计量。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from continuum.proxy import detect_format, process_request  # noqa: E402
from continuum.proxy.formats import (  # noqa: E402
    AnthropicAdapter,
    GeminiAdapter,
    OpenAICompatAdapter,
    OpenAIResponsesAdapter,
)

OPENAI_HEADERS = {"content-type": "application/json", "authorization": "Bearer sk-test"}
ANTHROPIC_HEADERS = {"content-type": "application/json", "x-api-key": "sk-ant-test",
                     "anthropic-version": "2023-06-01"}

OPENAI_BODY = {
    "model": "deepseek-chat",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "上周我们定了什么？"},
    ],
}

ANTHROPIC_BODY = {
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1024,
    "system": "You are a helpful assistant.",
    "messages": [
        {"role": "user", "content": [{"type": "text", "text": "上周我们定了什么？"}]},
    ],
}

GEMINI_BODY = {
    "contents": [{"role": "user", "parts": [{"text": "上周我们定了什么？"}]}],
    "systemInstruction": {"parts": [{"text": "You are helpful."}]},
}

# OpenAI Responses API：input 可为纯 string 或 message 列表（content 块化）
RESPONSES_BODY_STR = {
    "model": "glm-5.3-flash",
    "input": "上周我们定了什么？",
}
RESPONSES_BODY_LIST = {
    "model": "glm-5.3-flash",
    "instructions": "You are helpful.",
    "input": [
        {"role": "user", "content": [{"type": "input_text", "text": "上周我们定了什么？"}]},
    ],
}
RESPONSES_RESPONSE = {
    "output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "定了方案A。"}]}],
    "usage": {"input_tokens": 120, "output_tokens": 8, "total_tokens": 128},
}

ASSEMBLY = "## 红线\n- 严禁删除生产配置\n## 约定\n- 使用方案A"


class TestFormatDetection(unittest.TestCase):
    def test_openai(self):
        fmt = detect_format("/v1/chat/completions", OPENAI_HEADERS)
        self.assertIsInstance(fmt, OpenAICompatAdapter)

    def test_anthropic(self):
        fmt = detect_format("/v1/messages", ANTHROPIC_HEADERS)
        self.assertIsInstance(fmt, AnthropicAdapter)

    def test_gemini(self):
        fmt = detect_format("/v1beta/models/gemini-2.0-flash:generateContent", {})
        self.assertIsInstance(fmt, GeminiAdapter)

    def test_responses(self):
        fmt = detect_format("/v1/responses", {"content-type": "application/json"})
        self.assertIsInstance(fmt, OpenAIResponsesAdapter)

    def test_unknown_passthrough(self):
        self.assertIsNone(detect_format("/completions", {"content-type": "application/json"}))


class TestOpenAIProxy(unittest.TestCase):
    def setUp(self):
        self.fmt = OpenAICompatAdapter()

    def test_extract_conversation(self):
        conv = self.fmt.extract_conversation(OPENAI_BODY)
        self.assertEqual(len(conv), 1)  # 只取 user/assistant，跳过 system
        self.assertEqual(conv[0]["content"], "上周我们定了什么？")

    def test_inject_system_existing(self):
        body = self.fmt.inject_system(dict(OPENAI_BODY), ASSEMBLY)
        sys_msg = next(m for m in body["messages"] if m["role"] == "system")
        self.assertIn(ASSEMBLY, sys_msg["content"])
        # 原有 system 内容保留
        self.assertIn("helpful", sys_msg["content"])

    def test_inject_system_no_existing(self):
        body = {"messages": [{"role": "user", "content": "hello"}]}
        body = self.fmt.inject_system(body, ASSEMBLY)
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(body["messages"][0]["content"], ASSEMBLY)

    def test_extract_usage(self):
        usage = self.fmt.extract_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 50}})
        self.assertEqual(usage, {"base": 100, "completion": 50})


class TestResponsesProxy(unittest.TestCase):
    """OpenAI Responses API（codex wire_api="responses"）适配器。"""

    def setUp(self):
        self.fmt = OpenAIResponsesAdapter()

    def test_extract_conversation_str_input(self):
        conv = self.fmt.extract_conversation(RESPONSES_BODY_STR)
        self.assertEqual(conv, [{"role": "user", "content": "上周我们定了什么？"}])

    def test_extract_conversation_list_input(self):
        conv = self.fmt.extract_conversation(RESPONSES_BODY_LIST)
        self.assertEqual(conv, [{"role": "user", "content": "上周我们定了什么？"}])

    def test_inject_instructions_appends(self):
        out = self.fmt.inject_system(RESPONSES_BODY_LIST, ASSEMBLY)
        self.assertEqual(out["instructions"], f"You are helpful.\n\n{ASSEMBLY}")
        # 原 input 不变（深拷贝注入，不改调用方请求体）
        self.assertEqual(len(RESPONSES_BODY_LIST["input"]), 1)

    def test_inject_instructions_creates(self):
        out = self.fmt.inject_system(RESPONSES_BODY_STR, ASSEMBLY)
        self.assertEqual(out["instructions"], ASSEMBLY)
        self.assertEqual(out["input"], "上周我们定了什么？")

    def test_extract_usage(self):
        usage = self.fmt.extract_usage(RESPONSES_RESPONSE)
        self.assertEqual(usage, {"base": 120, "completion": 8})


class TestAnthropicProxy(unittest.TestCase):
    def setUp(self):
        self.fmt = AnthropicAdapter()

    def test_extract_conversation(self):
        conv = self.fmt.extract_conversation(ANTHROPIC_BODY)
        self.assertEqual(len(conv), 1)
        self.assertEqual(conv[0]["content"], "上周我们定了什么？")

    def test_inject_system_string(self):
        body = self.fmt.inject_system(dict(ANTHROPIC_BODY), ASSEMBLY)
        self.assertIn(ASSEMBLY, body["system"])

    def test_extract_usage(self):
        usage = self.fmt.extract_usage({"usage": {"input_tokens": 200, "output_tokens": 80}})
        self.assertEqual(usage, {"base": 200, "completion": 80})


class TestGeminiProxy(unittest.TestCase):
    def setUp(self):
        self.fmt = GeminiAdapter()

    def test_extract_conversation(self):
        conv = self.fmt.extract_conversation(GEMINI_BODY)
        self.assertEqual(len(conv), 1)
        self.assertEqual(conv[0]["content"], "上周我们定了什么？")

    def test_inject_system(self):
        body = self.fmt.inject_system(dict(GEMINI_BODY), ASSEMBLY)
        si_text = body["systemInstruction"]["parts"][-1]["text"]
        self.assertIn(ASSEMBLY, si_text)

    def test_extract_usage(self):
        usage = self.fmt.extract_usage({"usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 60}})
        self.assertEqual(usage, {"base": 300, "completion": 60})


class TestProxyCore(unittest.TestCase):
    def test_process_openai_with_assembly(self):
        fmt, body, headers = process_request(
            "/v1/chat/completions", OPENAI_HEADERS, dict(OPENAI_BODY), ASSEMBLY, judge=None)
        self.assertEqual(fmt, "openai-compat")
        self.assertIsNotNone(body)
        sys_msg = next(m for m in body["messages"] if m["role"] == "system")
        self.assertIn("严禁删除", sys_msg["content"])
        self.assertIn(_INJ_HDR, headers)

    def test_process_unknown_passthrough(self):
        fmt, body, headers = process_request(
            "/completions", {"content-type": "app/json"}, {"prompt": "x"}, ASSEMBLY, judge=None)
        self.assertIsNone(fmt)
        self.assertIsNone(body)
        self.assertEqual(headers, {})

    def test_process_redline_block(self):
        """红线命中 block → 拒绝信号，不返回修改后 body。"""
        class _BlockJudge:
            def judge_message_retention(self, role, content, has_downstream_memory):
                return "drop", ""   # 在代理层语义中映射为 block

        fmt, body, headers = process_request(
            "/v1/chat/completions", OPENAI_HEADERS, dict(OPENAI_BODY),
            assembly_text=ASSEMBLY,
            judge=_ProxyJudgeWrapper(_BlockJudge()))
        self.assertIsNotNone(headers.get("X-Continuum-Blocked"))


_INJ_HDR = "X-Continuum-Injected-Tokens"


class _ProxyJudgeWrapper:
    """把 compact 的 retention 语义映射为代理层的 block 判定。"""
    def __init__(self, inner):
        self.inner = inner
    def judge_message_retention(self, role, content, has_downstream_memory):
        verdict, reason = self.inner.judge_message_retention(role, content, has_downstream_memory)
        if verdict == "drop":
            return "block", reason
        return "allow", reason
    def classify(self, statement, context=""):
        from continuum.judges import MemoryJudgement
        return MemoryJudgement(kind="none", confidence=0.0)


if __name__ == "__main__":
    unittest.main()
