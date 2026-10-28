-- Server-owned runtime state, separate from existing Supabase Auth/profile tables.
-- Apply explicitly through a migration connection. Never expose this schema in PostgREST.
begin;
create schema if not exists uptodate_runtime;
revoke all on schema uptodate_runtime from public, anon, authenticated;
set local search_path to uptodate_runtime, pg_catalog;


CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, alias TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'UTC'
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, sender TEXT NOT NULL, signup_url TEXT NOT NULL DEFAULT '',
 muted INTEGER NOT NULL DEFAULT 0, UNIQUE(user_id, sender)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 fingerprint TEXT NOT NULL, subject TEXT NOT NULL, status TEXT NOT NULL,
 error TEXT, payload TEXT, created_at TEXT NOT NULL, UNIQUE(user_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS posts (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 title TEXT NOT NULL, hook TEXT NOT NULL, finding TEXT NOT NULL,
 limitations TEXT NOT NULL, original_url TEXT NOT NULL, evidence TEXT NOT NULL,
 rights TEXT NOT NULL DEFAULT 'private', status TEXT NOT NULL DEFAULT 'review',
 is_demo INTEGER NOT NULL DEFAULT 0, generation TEXT NOT NULL DEFAULT 'extractive',
 created_at TEXT NOT NULL, published_date TEXT,
 UNIQUE(job_id, original_url)
);
CREATE TABLE IF NOT EXISTS bookmarks (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 PRIMARY KEY(user_id, post_id)
);
CREATE TABLE IF NOT EXISTS reads (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 PRIMARY KEY(user_id, post_id)
);
CREATE TABLE IF NOT EXISTS shares (
 token TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
 url TEXT NOT NULL, title TEXT NOT NULL, content_hash TEXT NOT NULL,
 spans TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS post_assets (
 id TEXT PRIMARY KEY, post_id TEXT NOT NULL UNIQUE REFERENCES posts(id) ON DELETE CASCADE,
 image BYTEA NOT NULL, content_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_relay_aliases (
 address TEXT PRIMARY KEY, user_id TEXT UNIQUE REFERENCES users(id) ON DELETE SET NULL,
 status TEXT NOT NULL CHECK(status IN ('provisioning','active')), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_relay_receipts (
 message_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 state TEXT NOT NULL, job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL, received_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_mail_actions (
 source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 unsubscribe_url TEXT NOT NULL, one_click INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inbox_candidates (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 sender TEXT NOT NULL, sender_name TEXT NOT NULL, provider TEXT,
 subject TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 channel_key TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(user_id,sender,channel_key)
);
CREATE TABLE IF NOT EXISTS venue_photo_searches (
 cache_key TEXT PRIMARY KEY, status TEXT NOT NULL, pages TEXT NOT NULL DEFAULT '[]',
 expires_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_artifacts (
 cache_key TEXT PRIMARY KEY, scope TEXT NOT NULL, source_url TEXT NOT NULL,
 content_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('processing','ready','failed')),
 claim_token TEXT, lease_until DOUBLE PRECISION NOT NULL, result TEXT, error TEXT, updated_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS post_sources (
 post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 PRIMARY KEY(post_id,source_id)
);
CREATE TABLE IF NOT EXISTS gallery_assets (
 id TEXT PRIMARY KEY, artifact_key TEXT NOT NULL REFERENCES generation_artifacts(cache_key) ON DELETE CASCADE,
 image BYTEA NOT NULL, content_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_spend (
 id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
 job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
 amount_usd DOUBLE PRECISION NOT NULL, status TEXT NOT NULL CHECK(status IN ('reserved','settled')),
 created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS model_spend_day ON model_spend(created_at);

ALTER TABLE sources ADD COLUMN IF NOT EXISTS url TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS feed_url TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'not_requested';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS subscription_detail TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS subscription_alias TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS expected_sender_domain TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'newsletter';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS email_sender TEXT NOT NULL DEFAULT '';
ALTER TABLE sources ADD COLUMN IF NOT EXISTS email_topic TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'email';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS result TEXT NOT NULL DEFAULT '{}';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS analysis TEXT NOT NULL DEFAULT '{}';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS verification TEXT NOT NULL DEFAULT 'manual_required';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS visual TEXT NOT NULL DEFAULT '{}';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS artifact_key TEXT REFERENCES generation_artifacts(cache_key) ON DELETE SET NULL;

ALTER TABLE post_assets ADD COLUMN IF NOT EXISTS storage_path TEXT;
ALTER TABLE gallery_assets ADD COLUMN IF NOT EXISTS storage_path TEXT;
CREATE TABLE IF NOT EXISTS media_objects (
 storage_path TEXT PRIMARY KEY, created_at DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL);
INSERT INTO schema_version VALUES(1,1) ON CONFLICT(id) DO NOTHING;
CREATE UNIQUE INDEX IF NOT EXISTS one_private_story_per_account ON posts(user_id,artifact_key)
 WHERE artifact_key IS NOT NULL AND rights='private' AND status!='rejected';
CREATE INDEX IF NOT EXISTS runtime_posts_feed ON posts(user_id,published_date DESC,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS runtime_jobs_pending ON jobs(status,created_at);
CREATE INDEX IF NOT EXISTS runtime_documents_revision ON documents(user_id,job_id,content_hash);
CREATE TABLE IF NOT EXISTS job_deliveries (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
 revision TEXT NOT NULL DEFAULT gen_random_uuid()::text,
 enqueued_at DOUBLE PRECISION NOT NULL DEFAULT 0,
 created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
);
CREATE TABLE IF NOT EXISTS service_leases (name TEXT PRIMARY KEY, token TEXT NOT NULL, lease_until DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS job_runs (
 job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
 token TEXT NOT NULL, lease_until DOUBLE PRECISION NOT NULL
);
CREATE OR REPLACE FUNCTION uptodate_runtime.queue_job_delivery() RETURNS trigger
LANGUAGE plpgsql SET search_path = uptodate_runtime, pg_catalog AS $$
BEGIN
 IF NEW.status='queued' AND NEW.kind!='ask_ai' THEN
  INSERT INTO job_deliveries(job_id) VALUES(NEW.id)
  ON CONFLICT(job_id) DO UPDATE SET revision=gen_random_uuid()::text,enqueued_at=0,
   created_at=extract(epoch from now());
 END IF;
 RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS runtime_job_delivery ON jobs;
CREATE TRIGGER runtime_job_delivery AFTER INSERT OR UPDATE OF status ON jobs
 FOR EACH ROW EXECUTE FUNCTION uptodate_runtime.queue_job_delivery();
-- RLS plus schema privileges keep all runtime content inaccessible to app clients.
-- Only the server's database credential is allowed to read/write these tables.
DO $$ DECLARE item record; BEGIN
 FOR item IN SELECT tablename FROM pg_tables WHERE schemaname='uptodate_runtime' LOOP
  EXECUTE format('ALTER TABLE uptodate_runtime.%I ENABLE ROW LEVEL SECURITY',item.tablename);
 END LOOP;
END; $$;
REVOKE ALL ON ALL TABLES IN SCHEMA uptodate_runtime FROM public, anon, authenticated;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA uptodate_runtime FROM public, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA uptodate_runtime REVOKE ALL ON TABLES FROM public,anon,authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA uptodate_runtime REVOKE EXECUTE ON FUNCTIONS FROM public,anon,authenticated;
commit;
