-- Private connector credentials stay in the server-only runtime schema.
BEGIN;
SET LOCAL search_path TO uptodate_runtime, pg_catalog;
CREATE TABLE IF NOT EXISTS muse_connector_grants (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE, created_at DOUBLE PRECISION NOT NULL,
 expires_at DOUBLE PRECISION NOT NULL, revoked_at DOUBLE PRECISION, last_used_at DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS muse_connector_grants_owner ON muse_connector_grants(user_id);
CREATE TABLE IF NOT EXISTS muse_connector_deliveries (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 grant_id TEXT NOT NULL REFERENCES muse_connector_grants(id) ON DELETE CASCADE,
 newsletter_key TEXT NOT NULL, edition_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
 created_at DOUBLE PRECISION NOT NULL, UNIQUE(user_id,newsletter_key,edition_id)
);
CREATE INDEX IF NOT EXISTS muse_connector_deliveries_owner ON muse_connector_deliveries(user_id,created_at);
ALTER TABLE muse_connector_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE muse_connector_deliveries ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON muse_connector_grants,muse_connector_deliveries FROM public,anon,authenticated;
COMMIT;
