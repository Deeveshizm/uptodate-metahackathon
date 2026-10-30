"""Account-owned receiving routes; primary addresses can change without losing mail."""
import hashlib
import json

from .retrieval import SourceError


ACTIVE_ALIASES = """
SELECT r.address,r.user_id,r.status,r.created_at
FROM test_relay_aliases r JOIN users u ON u.id=r.user_id
WHERE r.status='active' AND u.alias=r.address
UNION ALL
SELECT l.address,l.user_id,'active' AS status,l.created_at
FROM legacy_relay_aliases l JOIN users u ON u.id=l.user_id
JOIN test_relay_aliases p ON p.user_id=u.id AND p.address=u.alias
WHERE p.status='active'
"""


def active_alias(db, address, owner=None):
    clause = ' AND user_id=?' if owner is not None else ''
    args = (address, owner) if owner is not None else (address,)
    return db.execute('SELECT * FROM (' + ACTIVE_ALIASES + ') routes WHERE address=?' + clause, args).fetchone()


def alias_digest(address):
    return hashlib.sha256(address.lower().encode()).hexdigest()


def revoke_account_aliases(db, owner):
    from .receiving import timestamp
    addresses = {r['address'] for r in db.execute('SELECT address FROM test_relay_aliases WHERE user_id=? UNION SELECT address FROM legacy_relay_aliases WHERE user_id=?', (owner,owner)).fetchall()}
    for address in addresses:
        db.execute('INSERT INTO revoked_relay_aliases(digest,revoked_at) VALUES(?,?) ON CONFLICT(digest) DO NOTHING', (alias_digest(address),timestamp()))
    db.execute('DELETE FROM legacy_relay_aliases WHERE user_id=?', (owner,))
    for address in addresses:
        db.execute('DELETE FROM test_relay_aliases WHERE address=?', (address,))


def purge_orphaned_aliases(store):
    from .receiving import timestamp
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT address FROM test_relay_aliases r WHERE user_id IS NULL AND NOT EXISTS(SELECT 1 FROM legacy_relay_aliases l WHERE l.address=r.address AND l.user_id IS NOT NULL)').fetchall()
        for row in rows:
            db.execute('INSERT INTO revoked_relay_aliases(digest,revoked_at) VALUES(?,?) ON CONFLICT(digest) DO NOTHING', (alias_digest(row['address']),timestamp()))
            db.execute('DELETE FROM test_relay_aliases WHERE address=?', (row['address'],))
        return len(rows)


def content_snapshot(db, owner):
    result = {}
    for table in ('sources','posts','bookmarks','reads','shares','jobs'):
        query = ('SELECT s.* FROM shares s JOIN posts p ON p.id=s.post_id WHERE p.user_id=?'
                 if table=='shares' else 'SELECT * FROM ' + table + ' WHERE user_id=?')
        rows = [dict(row) for row in db.execute(query, (owner,)).fetchall()]
        encoded = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
        result[table] = (len(rows), hashlib.sha256('\n'.join(encoded).encode()).hexdigest())
    return result


def migrate_resend_accounts(store, cloudflare_domain, resend_domain, apply=False):
    from .cloudflare_receiving import CloudflareRelay
    from .receiving import timestamp
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute("SELECT u.id,u.alias FROM users u JOIN test_relay_aliases r ON r.user_id=u.id AND r.address=u.alias WHERE r.status='active'").fetchall()
        candidates = [row for row in rows if row['alias'].endswith('@' + resend_domain)]
        result = {'accounts': len(candidates), 'applied': apply, 'content_unchanged': True}
        if not apply:
            return result
        relay = CloudflareRelay(cloudflare_domain, store)
        for row in candidates:
            owner, old = row['id'], row['alias']
            pending = db.execute("SELECT 1 FROM jobs WHERE user_id=? AND kind IN ('newsletter','subscription') AND status IN ('queued','processing')", (owner,)).fetchone()
            if pending:
                raise SourceError('Wait for pending signup jobs before migrating receiving addresses')
            before = content_snapshot(db, owner)
            previous = db.execute('SELECT user_id FROM legacy_relay_aliases WHERE address=?', (old,)).fetchone()
            if previous and previous['user_id'] != owner:
                raise SourceError('Legacy receiving address has a conflicting owner; nothing migrated')
            db.execute('INSERT INTO legacy_relay_aliases(address,user_id,created_at) VALUES(?,?,?) ON CONFLICT(address) DO NOTHING', (old, owner, timestamp()))
            db.execute('UPDATE test_relay_aliases SET user_id=NULL WHERE address=? AND user_id=?', (old, owner))
            profile = relay.profile(db, {'id': owner, 'alias': None, 'alias_status': 'active'})
            db.execute('UPDATE users SET alias=? WHERE id=?', (profile['alias'], owner))
            if not active_alias(db, old, owner) or not active_alias(db, profile['alias'], owner):
                raise SourceError('Receiving route verification failed; migration rolled back')
            if content_snapshot(db, owner) != before:
                raise SourceError('Account content changed during migration; migration rolled back')
        return result
