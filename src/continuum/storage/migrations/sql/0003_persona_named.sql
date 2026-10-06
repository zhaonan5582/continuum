-- Continuum schema v3 (migration 0003) — 多套命名人格 + 全局唯一激活。
-- 楠哥 2026-10-06：人格要可切换（多套并存，如"老王"与"小陈"各自成版本链）；
-- 激活的全局唯一性 = 装配包人格的单一事实来源（防漂移命门不变：写通道仍只在 CLI/壳）。

ALTER TABLE persona_versions ADD COLUMN name TEXT NOT NULL DEFAULT 'default';
ALTER TABLE persona_versions ADD COLUMN active INTEGER NOT NULL DEFAULT 0;

-- 存量数据：已有版本链归入 default 并激活（迁移原子性：与列添加同事务）
UPDATE persona_versions SET name = 'default'
WHERE name = 'default';
UPDATE persona_versions SET active = 1
WHERE id = (SELECT id FROM persona_versions ORDER BY version DESC LIMIT 1)
  AND NOT EXISTS (SELECT 1 FROM persona_versions WHERE active = 1);
