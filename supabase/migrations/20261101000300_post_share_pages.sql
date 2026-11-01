-- Public post links are explicit snapshots; ordinary feed rows stay private.
BEGIN;
SET LOCAL search_path TO uptodate_runtime,pg_catalog;
CREATE TABLE IF NOT EXISTS post_share_pages (
 token TEXT PRIMARY KEY,
 post_id TEXT NOT NULL UNIQUE REFERENCES posts(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 snapshot TEXT NOT NULL,
 created_at TEXT NOT NULL
);
ALTER TABLE post_share_pages ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON post_share_pages FROM public,anon,authenticated;
COMMIT;
