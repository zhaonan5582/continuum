-- Continuum schema v2 (migration 0002) — P3: persona engine + redline gate.
-- 契约于 P0 冻结（docs/01 §4），本迁移为表落地。

-- 人格状态块（版本化：可 diff / 回滚 / 审计，docs/01 §5.4）
CREATE TABLE persona_versions (
    id             INTEGER PRIMARY KEY,
    version        INTEGER NOT NULL,           -- 递增版本号
    snapshot_md    TEXT    NOT NULL,           -- 状态块全文（默契规则 + 风格参数）
    parent_version INTEGER REFERENCES persona_versions(id),
    change_reason  TEXT    NOT NULL,
    created_at     TEXT    NOT NULL,
    UNIQUE(version)
);

-- few-shot 对话样本（人工挑选 CLI 录入，docs/01 §5.4：自动聚类不做承诺）
CREATE TABLE persona_samples (
    id                INTEGER PRIMARY KEY,
    persona_version   INTEGER NOT NULL REFERENCES persona_versions(id),
    user_utterance    TEXT    NOT NULL,
    agent_response    TEXT    NOT NULL,
    source_message_id INTEGER REFERENCES messages(id),
    tag               TEXT
);
CREATE INDEX idx_samples_version ON persona_samples(persona_version);

-- 红线表：逐字存储，禁止收敛改写（宪法 6/10：hook 管不能违反）
CREATE TABLE redlines (
    id               INTEGER PRIMARY KEY,
    pattern          TEXT    NOT NULL,        -- 匹配模式（对 operation.target / detail）
    scope            TEXT    NOT NULL DEFAULT 'global',  -- global / project:<id>
    action           TEXT    NOT NULL CHECK (action IN ('block','warn','ask')),
    statement        TEXT    NOT NULL,        -- 红线原文（逐字）
    source_memory_id INTEGER REFERENCES memories(id),
    enabled          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_redlines_enabled ON redlines(enabled);

-- 红线正反测试集（每条红线必须配正/反用例，防误伤——docs/01 §5.5）
CREATE TABLE redline_tests (
    id         INTEGER PRIMARY KEY,
    redline_id INTEGER NOT NULL REFERENCES redlines(id),
    case_type  TEXT    NOT NULL CHECK (case_type IN ('positive','negative')),
    -- positive = 该拦的样例；negative = 不该拦的样例
    sample_target    TEXT NOT NULL,
    sample_detail    TEXT,
    expected_action  TEXT NOT NULL CHECK (expected_action IN ('block','warn','allow','ask'))
);
