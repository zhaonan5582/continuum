# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
# BYOK-USER-INITIATED-NETWORK
"""OpenAI 兼容端点的 BYOK Judge 实现。

**隐私边界（显式声明）**：本模块是全包唯一允许出现网络调用的位置——
只有用户显式配置了 endpoint + api_key（自带 key，BYOK）才会发起任何网络请求。
未配置时 `available()` 为 False，调用方退回 NullJudge/启发式。
隐私检查脚本对本文件白名单（标记行见文件头）。
"""

from __future__ import annotations

import json
import urllib.request

from continuum.judges.base import Judge, MemoryJudgement

_KINDS = ("fact", "decision", "exclusion", "convention", "redline", "preference", "none")

_SYSTEM_PROMPT = (
    "你是记忆分类器。判断一条对话语句属于哪类长期记忆，输出严格 JSON："
    '{"kind":"decision|convention|redline|exclusion|preference|fact|none",'
    '"confidence":0.0~1.0,"reason":"一句话"}。'
    "规则：决定/拍板=decision；约定/以后都=convention；不许/禁止=redline；"
    "试过/走不通=exclusion；偏好/习惯=preference；其他重要事实=fact；闲聊=none。"
    "confidence<0.7 时宁可选 none。只输出 JSON。"
)


class OpenAICompatJudge(Judge):
    """OpenAI 兼容 /chat/completions 端点（DeepSeek/本地 vLLM 等均可）。"""

    def __init__(self, endpoint: str, api_key: str, model: str, timeout: float = 10.0):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.endpoint and self.api_key and self.model)

    def _chat(self, user_prompt: str) -> str | None:
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]

    def classify(self, statement: str, context: str = "") -> MemoryJudgement:
        try:
            raw = self._chat(statement if not context else f"{context}\n\n{statement}")
            data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            kind = data.get("kind") if data.get("kind") in _KINDS else "none"
            conf = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
            return MemoryJudgement(kind=kind, confidence=conf, reason=str(data.get("reason", ""))[:200])
        except Exception as e:   # 网络超时/格式坏——judge 容错承诺：不抛网络异常
            return MemoryJudgement(kind="none", confidence=0.0, reason=f"judge error: {type(e).__name__}")

    def judge_message_retention(
        self, role: str, content: str, has_downstream_memory: bool
    ) -> tuple[str, str]:
        try:
            raw = self._chat(
                f"以下是一条历史消息。判断压缩时如何处理它（verdict ∈ full/truncate/drop）。"
                f"drop 仅当它不重要。输出严格 JSON: {{\"verdict\":\"full|truncate|drop\",\"reason\":\"一句话\"}}\n"
                f"角色: {role}\n已沉淀为记忆: {has_downstream_memory}\n内容: {content[:2000]}"
            )
            data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            verdict = data.get("verdict")
            if verdict not in ("full", "truncate", "drop"):
                return "full", "judge 返回未知 verdict，保守保留"
            if verdict == "drop" and not has_downstream_memory:
                return "truncate", "内容无沉淀指针，drop 降级为 truncate"
            return verdict, str(data.get("reason", ""))[:200]
        except Exception as e:
            return "full", f"judge error: {type(e).__name__}"
