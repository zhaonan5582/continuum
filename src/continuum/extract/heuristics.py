# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""中文启发式规则（轨道 B 结构化预沉淀，无 judge 模式）。

定位（docs/01 §5.1）：无判决模型时的写入保障——宁可 pending 不错信。
所有条目一律 evidence_level="inferred" + status="pending"，
待 judge（P2）或人工确认后才升 active。规则错杀不影响无损层（L3 原文永远在）。

规则优先级：一条语句命中多类时按列表顺序取第一类（决策 > 约定 > 红线 > 排除 > 偏好 > 事实）。
"""

from __future__ import annotations

import re

# (编译模式, kind) —— 顺序即优先级
PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(决定|拍板|敲定|定了[，,：:]|就定|确定用|选定)"), "decision"),
    (re.compile(r"(约定|说好[了的]?|以后都|以后一律|从今以后|咱们的规矩|固定用)"), "convention"),
    (re.compile(r"(不许|不准|不能动|禁止|别再|绝对不要|红线|严禁)"), "redline"),
    (re.compile(r"(试过|试了|走不通|失败[了]|排除|放弃|不行[，,了]|不可行)"), "exclusion"),
    (re.compile(r"(偏好|我习惯|我喜欢|我一般|我喜欢用)"), "preference"),
    (re.compile(r"(注意|记住|重要[：:]|关键是)"), "fact"),
]

# 句子切分（中英文标点 + 换行）
_SENT_SPLIT = re.compile(r"[。！？!?\n；;]+")


def split_sentences(text: str) -> list[str]:
    """按中英文句读切句，去空、去纯空白，保留原文顺序。"""
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def classify(sentence: str) -> str | None:
    """返回句子命中的记忆类型（无命中返回 None）。"""
    for pattern, kind in PATTERNS:
        if pattern.search(sentence):
            return kind
    return None


def extract_candidates_from_text(text: str) -> list[tuple[str, str]]:
    """从一段文本中抽取 (kind, statement) 候选列表。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        if len(sentence) < 4:          # 过短语句没有记忆价值
            continue
        if len(sentence) > 500:        # 超长句截断（保留前 500 字，够溯源定位即可）
            sentence = sentence[:500]
        kind = classify(sentence)
        if kind and sentence not in seen:
            seen.add(sentence)
            out.append((kind, sentence))
    return out
