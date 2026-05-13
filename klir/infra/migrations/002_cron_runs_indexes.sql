-- 002_cron_runs_indexes.sql
-- Add status and global timestamp indexes for cross-job queries

CREATE INDEX IF NOT EXISTS idx_cron_runs_status ON cron_runs (status);
CREATE INDEX IF NOT EXISTS idx_cron_runs_ts ON cron_runs (ts DESC);
