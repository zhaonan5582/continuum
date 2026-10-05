# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""Judge 抽象——可插拔的判决模型接口（docs/01 §5：判决模型可插拔，BYOK 优先）。

设计约束：
- **核心零外联**：本模块只有抽象与消费逻辑，不含任何网络调用；
- HTTP 实现在 `continuum.judges.openai_judge`（用户显式配置 endpoint+key 才外联，
  该文件带 BYOK-USER-INITIATED-NETWORK 标记，隐私检查白名单）；
- 无 judge 时使用 `NullJudge`：一切走启发式兜底，绝不冒充判断。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryJudgement:
    """对一条候选记忆的判决。"""

    kind: str                  # fact/decision/exclusion/convention/redline/preference/none
    confidence: float          # 0.0~1.0；< 高阈值 → pending，≥ 阈值 → active
    reason: str = ""


class Judge(ABC):
    @abstractmethod
    def classify(self, statement: str, context: str = "") -> MemoryJudgement:
        """判断一条语句属于哪类记忆及置信度。实现必须容错：内部失败应返回
        MemoryJudgement(kind="none", confidence=0.0, reason=...) 而不是抛网络异常。"""

    @abstractmethod
    def judge_message_retention(
        self,
        role: str,
        content: str,
        has_downstream_memory: bool,
    ) -> tuple[str, str]:
        """按条判决（压缩用，§5.2）。返回 (verdict, reason)：
        - "full"     完整保留
        - "truncate" 保留调用/要点，正文截断为指针
        - "drop"     可丢弃（仅当 has_downstream_memory=True 时才允许建议 drop）
        实现必须容错：内部失败返回 ("full", "judge 不可用，宁可胖不可丢")。
        """


class NullJudge(Judge):
    """无 judge 语义：一切不判断，全部走调用方的启发式兜底。"""

    def classify(self, statement: str, context: str = "") -> MemoryJudgement:
        return MemoryJudgement(kind="none", confidence=0.0, reason="no judge configured")

    def judge_message_retention(
        self, role: str, content: str, has_downstream_memory: bool
    ) -> tuple[str, str]:
        # 无 judge 时压缩判决退化为最保守：全部保留（宁可胖不可丢）
        return "full", "no judge configured"
