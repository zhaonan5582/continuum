-- Continuum schema v4 (migration 0004) — 召回索引模式支持（lite / fast 双模式）。
-- 楠哥 2026-10-07 拍板：**两种模式都保留，选择权留给用户**（要速度还是要更小存储，用户自己定）。
--
-- 设计（对现状零影响）：
-- - content_norm 列 = 归一化内容（去空白/零宽/反斜杠），**仅 fast 模式填充**；
-- - lite 模式（默认）下该列恒为 NULL —— SQLite 的 NULL 不占页面空间、加列是 O(1)，
--   因此本迁移对既有用户**无感知**；
-- - fast 模式的 FTS 索引**不在迁移里建**（属"大动作"，由用户显式命令触发：
--   `continuum index --mode fast`，见 indexer.py）。

ALTER TABLE messages ADD COLUMN content_norm TEXT;
