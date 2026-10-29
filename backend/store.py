"""Small transactional SQLite store. Each request owns its connection."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
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
 image BLOB NOT NULL, content_type TEXT NOT NULL
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
 expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_artifacts (
 cache_key TEXT PRIMARY KEY, scope TEXT NOT NULL, source_url TEXT NOT NULL,
 content_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('processing','ready','failed')),
 claim_token TEXT, lease_until REAL NOT NULL, result TEXT, error TEXT, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS post_sources (
 post_id TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 PRIMARY KEY(post_id,source_id)
);
CREATE TABLE IF NOT EXISTS gallery_assets (
 id TEXT PRIMARY KEY, artifact_key TEXT NOT NULL REFERENCES generation_artifacts(cache_key) ON DELETE CASCADE,
 image BLOB NOT NULL, content_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_spend (
 id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
 job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
 amount_usd REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('reserved','settled')),
 created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS model_spend_day ON model_spend(created_at);
"""

# Additive migrations preserve accounts created by the first prototype.
ADDITIONS = {
    "sources": {
        "url": "TEXT NOT NULL DEFAULT ''",
        "feed_url": "TEXT NOT NULL DEFAULT ''",
        "subscription_status": "TEXT NOT NULL DEFAULT 'not_requested'",
        "subscription_detail": "TEXT NOT NULL DEFAULT ''",
        "subscription_alias": "TEXT NOT NULL DEFAULT ''",
        "expected_sender_domain": "TEXT NOT NULL DEFAULT ''",
        "source_kind": "TEXT NOT NULL DEFAULT 'newsletter'",
        "email_sender": "TEXT NOT NULL DEFAULT ''",
        "email_topic": "TEXT NOT NULL DEFAULT ''",
        "source_category": "TEXT NOT NULL DEFAULT ''",
        "unsubscribed_at": "TEXT",
        "archived_at": "TEXT",
    },
    "jobs": {
        "kind": "TEXT NOT NULL DEFAULT 'email'", "stage": "TEXT NOT NULL DEFAULT ''",
        "result": "TEXT NOT NULL DEFAULT '{}'",
    },
    "posts": {
        "analysis": "TEXT NOT NULL DEFAULT '{}'",
        "verification": "TEXT NOT NULL DEFAULT 'manual_required'",
        "visual": "TEXT NOT NULL DEFAULT '{}'",
        "artifact_key": "TEXT REFERENCES generation_artifacts(cache_key) ON DELETE SET NULL",
    },
}


class Store:
    def __new__(cls, path: str):
        if str(path).startswith(('postgres://', 'postgresql://')):
            from .postgres_store import PostgresStore
            return PostgresStore(path)
        return super().__new__(cls)

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            for table, columns in ADDITIONS.items():
                existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_private_story_per_account ON posts(user_id,artifact_key) WHERE artifact_key IS NOT NULL AND rights='private' AND status!='rejected'")
            from .source_identity import reconcile_newsletters
            reconcile_newsletters(db)
            db.execute("UPDATE sources SET source_kind='email',name=CASE WHEN name=substr(sender,instr(sender,'@')+1) THEN 'Imported emails' ELSE name END WHERE url='' AND signup_url='' AND sender LIKE '%@%' AND EXISTS(SELECT 1 FROM jobs WHERE jobs.source_id=sources.id AND jobs.kind='received_email')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()
