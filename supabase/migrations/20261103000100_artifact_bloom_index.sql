-- Bounded per-worker Bloom refreshes read only artifact hashes and timestamps.
-- The existing unique cache_key remains authoritative for generation claims.
CREATE INDEX IF NOT EXISTS artifact_bloom_updates
ON uptodate_runtime.generation_artifacts(updated_at,cache_key);
