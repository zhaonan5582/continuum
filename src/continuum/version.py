# SPDX-FileCopyrightText: 2026 Continuum contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""版本与设计常量——单一事实来源。

修改任何常量必须同步更新对应冻结文档（docs/01 §4 数据模型 / docs/04 UDF），
并走「机械交叉检索」检查引用残留（修订纪律）。
"""

VERSION = "0.1.0"           # 包版本（P4 发布 v0.1；与 pyproject.toml 同步）
SCHEMA_VERSION = 1          # 存储库 schema 版本（PRAGMA user_version）
UDF_VERSION = 1             # 统一对话格式版本（契约冻结，只增不改）

# ---- 设计常量（与 docs/01 §5/§10 一致，改这里必须同步改文档）----
DESIGN_CONSTANTS = {
    # sweeping 触发（宪法 10：机制扳机）
    "SWEEP_EVERY_N_ROUNDS": 20,
    "SWEEP_EVERY_HOURS": 4,
    # 落库推送（§5.1 第 0 扳机）
    "APPEND_EVERY_N_ROUNDS": 5,
    # 检索（§5.3）
    "RECALL_MAX_ROUNDS": 8,
    "RECALL_MAX_ITEMS": 20,
    "RECALL_FAST_LATENCY_BUDGET_MS": 500,
    # 装配包（§5.4）
    "ASSEMBLE_TOKEN_BUDGET": 8_000,
    "L0_TOKEN_BUDGET": 2_000,
    "L1_TOKEN_BUDGET": 3_000,
    # 驱逐公式四维（§4）：priority * freshness *引用数 * 当前项目相关性
    "EVICT_CROSS_PROJECT_DECAY_FLOOR": 0.3,
    # 证据等级（宪法 2）
    "EVIDENCE_LEVELS": ("tested", "cited", "inferred"),
    "MEMORY_KINDS": (
        "fact",
        "decision",
        "exclusion",
        "convention",
        "redline",
        "preference",
    ),
    # 商业化防线（v1.2 审查）：单条 content 字节上限（DoS 防御）
    "MAX_CONTENT_BYTES": 10_000_000,
    "MAX_ID_LEN": 128,
}
