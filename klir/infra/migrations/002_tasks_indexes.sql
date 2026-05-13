-- 002_tasks_indexes.sql
-- Additional indexes for task queries

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(parent_agent);
CREATE INDEX IF NOT EXISTS idx_tasks_chat_created ON tasks(chat_id, created_at DESC);
