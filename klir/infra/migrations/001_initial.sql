-- 001_initial.sql
-- Unified SQLite store: messages, cron_runs, tasks, sessions, chat_activity

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    origin TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    topic_id INTEGER,
    direction TEXT NOT NULL,
    text TEXT NOT NULL,
    provider TEXT DEFAULT '',
    model TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    session_name TEXT DEFAULT '',
    cost_usd REAL DEFAULT 0.0,
    tokens INTEGER DEFAULT 0,
    elapsed_seconds REAL DEFAULT 0.0,
    is_error INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS cron_runs (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    job_id TEXT NOT NULL,
    status TEXT,
    error TEXT,
    summary TEXT,
    duration_ms INTEGER,
    delivery_status TEXT,
    delivery_error TEXT,
    provider TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    output_path TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    parent_agent TEXT NOT NULL,
    name TEXT DEFAULT '',
    prompt_preview TEXT DEFAULT '',
    original_prompt TEXT DEFAULT '',
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    session_id TEXT DEFAULT '',
    created_at REAL NOT NULL,
    completed_at REAL DEFAULT 0.0,
    elapsed_seconds REAL DEFAULT 0.0,
    error TEXT DEFAULT '',
    result_preview TEXT DEFAULT '',
    question_count INTEGER DEFAULT 0,
    num_turns INTEGER DEFAULT 0,
    last_question TEXT DEFAULT '',
    thinking TEXT DEFAULT '',
    tasks_dir TEXT DEFAULT '',
    thread_id INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    storage_key TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    topic_id INTEGER,
    user_id INTEGER,
    topic_name TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_active TEXT NOT NULL,
    thinking_level TEXT,
    provider_sessions TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS chat_activity (
    chat_id INTEGER PRIMARY KEY,
    title TEXT DEFAULT '',
    type TEXT DEFAULT '',
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    status TEXT DEFAULT 'active',
    metadata TEXT DEFAULT '{}'
);

-- messages: timeline queries per chat, filtering by origin
CREATE INDEX IF NOT EXISTS idx_messages_chat_ts ON messages (chat_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_origin_ts ON messages (origin, ts);

-- cron_runs: per-job run history
CREATE INDEX IF NOT EXISTS idx_cron_runs_job_ts ON cron_runs (job_id, ts);

-- tasks: active task lookups per chat, chronological listing
CREATE INDEX IF NOT EXISTS idx_tasks_chat_status ON tasks (chat_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at);

-- sessions: per-chat lookups, cleanup by staleness
CREATE INDEX IF NOT EXISTS idx_sessions_chat_id ON sessions (chat_id);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions (last_active);

-- chat_activity: retention cleanup
CREATE INDEX IF NOT EXISTS idx_chat_activity_last_seen ON chat_activity (last_seen);
