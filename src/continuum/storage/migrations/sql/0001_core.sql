-- Continuum schema v1 (migration 0001) — freezes: sessions / messages / memories /
-- entities / edges / audit_log / mem_mentions (docs/01 §4, P0 core freeze).
-- Later tables (redlines / persona_* / vectors) arrive in their own migrations —
-- evolving schema, core frozen here: messages + memories incl. four mandatory fields.
-- Conventions: timestamps ISO8601 UTC strings; ids INTEGER PRIMARY KEY (rowid alias).

CREATE TABLE sessions (
    id          INTEGER PRIMARY KEY,
    host_agent  TEXT    NOT NULL,             -- 'workbuddy' / 'claude-code' / ...
    external_id TEXT    NOT NULL,             -- host-side session id (opaque)
    title       TEXT,
    project_id  TEXT,                         -- neutral project key (core is project-agnostic)
    started_at  TEXT    NOT NULL,
    ended_at    TEXT,
    UNIQUE(host_agent, external_id)
);

CREATE INDEX idx_sessions_host ON sessions(host_agent);

-- L3 verbatim layer: append-only, never rewritten (Constitution 1/9).
CREATE TABLE messages (
    id           INTEGER PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES sessions(id),
    seq          INTEGER NOT NULL,            -- per-session ordinal, strictly increasing
    role         TEXT    NOT NULL CHECK (role IN ('user','assistant','tool')),
    host         TEXT    NOT NULL,
    content      TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,            -- sha256 hex of (role|ts|content)
    ts           TEXT    NOT NULL,
    token_count  INTEGER,
    UNIQUE(session_id, seq),
    UNIQUE(session_id, ts, role, content_hash)  -- idempotent re-append key
);
CREATE INDEX idx_messages_session_ts ON messages(session_id, ts);
CREATE INDEX idx_messages_hash ON messages(session_id, content_hash);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

-- Entities with disambiguation aliases.
CREATE TABLE entities (
    id             INTEGER PRIMARY KEY,
    kind           TEXT    NOT NULL CHECK (kind IN
                     ('person','project','file','concept','tool','constraint','decision','other')),
    name           TEXT    NOT NULL,
    canonical_name TEXT    NOT NULL,
    aliases_json   TEXT    NOT NULL DEFAULT '[]',
    confidence     REAL    NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    created_at     TEXT    NOT NULL,
    UNIQUE(kind, canonical_name)
);
CREATE INDEX idx_entities_canonical ON entities(canonical_name);

-- Memories (convergence products). Four mandatory fields:
--   source_message_id + created_at + evidence_level + valid_until  (Constitution 2)
-- stated_by: user / agent  — anti-poisoning split (docs/06).
CREATE TABLE memories (
    id                 INTEGER PRIMARY KEY,
    kind               TEXT    NOT NULL CHECK (kind IN
                         ('fact','decision','exclusion','convention','redline','preference')),
    statement          TEXT    NOT NULL,
    source_message_id  INTEGER REFERENCES messages(id),
    session_id         INTEGER REFERENCES sessions(id),
    stated_by          TEXT    NOT NULL CHECK (stated_by IN ('user','agent')),
    evidence_level     TEXT    NOT NULL CHECK (evidence_level IN ('tested','cited','inferred')),
    created_at         TEXT    NOT NULL,
    valid_from         TEXT    NOT NULL,
    valid_until        TEXT,
    status             TEXT    NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','superseded','revoked','quarantined','pending')),
    superseded_by      INTEGER REFERENCES memories(id)
);
CREATE INDEX idx_memories_status ON memories(status, kind);
CREATE INDEX idx_memories_source ON memories(source_message_id);
CREATE INDEX idx_memories_created ON memories(created_at);

-- Four-relation edges. P1 enables only entity+temporal at the application layer;
-- causal (P2+) requires high confidence + mandatory source pointer (docs/01 §4).
CREATE TABLE edges (
    id         INTEGER PRIMARY KEY,
    src_id     INTEGER NOT NULL REFERENCES entities(id),
    dst_id     INTEGER NOT NULL REFERENCES entities(id),
    rel_type   TEXT    NOT NULL CHECK (rel_type IN ('semantic','temporal','causal','entity')),
    memory_id  INTEGER REFERENCES memories(id),
    confidence REAL    NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    created_at TEXT    NOT NULL,
    UNIQUE(src_id, dst_id, rel_type, memory_id)
);
CREATE INDEX idx_edges_src ON edges(src_id, rel_type);
CREATE INDEX idx_edges_dst ON edges(dst_id, rel_type);

-- Message→entity mentions (structured retrieval main path, docs/01 §5.3).
CREATE TABLE mem_mentions (
    message_id INTEGER NOT NULL REFERENCES messages(id),
    entity_id  INTEGER NOT NULL REFERENCES entities(id),
    span_start INTEGER,
    span_end   INTEGER,
    PRIMARY KEY (message_id, entity_id, span_start)
);
CREATE INDEX idx_mentions_entity ON mem_mentions(entity_id);

-- Audit ledger: every operation lands here (Constitution 2 traceability / 9 sovereignty).
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,                -- 'core' / 'host:<name>' / 'user'
    action      TEXT NOT NULL,                -- 'append' / 'extract' / 'recall' / ...
    target      TEXT,                         -- affected object descriptor
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_audit_ts ON audit_log(ts);
CREATE INDEX idx_audit_action ON audit_log(action);
