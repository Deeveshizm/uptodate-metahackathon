"""Management categories and expiring subscription entries; posts are retained."""
from datetime import datetime, timedelta, timezone

IMPORT_NAMES = ('imported email', 'imported emails')


def category(source):
    stored = source.get('source_category', '')
    if stored in ('newsletter', 'custom'):
        return stored
    domain = source.get('email_sender', '').rsplit('@', 1)[-1] or source.get('sender', '').rsplit('@', 1)[-1]
    personal = domain in ('gmail.com','googlemail.com','outlook.com','hotmail.com','yahoo.com','icloud.com','me.com','proton.me','protonmail.com')
    return 'custom' if source.get('source_kind') == 'email' or personal or source['name'].strip().casefold() in ('muse', 'claude') else 'newsletter'


def maintain_sources(db, user_id=None, at=None):
    stamp = at or datetime.now(timezone.utc)
    now = stamp.isoformat()
    scope = ' AND user_id=?' if user_id else ''
    args = [user_id] if user_id else []
    # Legacy entries receive a full recovery window rather than disappearing
    # immediately because their original unsubscribe time was not recorded.
    db.execute("UPDATE sources SET unsubscribed_at=? WHERE subscription_status='unsubscribed' AND unsubscribed_at IS NULL" + scope, [now, *args])
    # Empty generic import placeholders are not actual subscriptions. Keep a
    # stopped identity so incoming mail cannot silently reactivate them.
    db.execute("""UPDATE sources SET subscription_status='unsubscribed',unsubscribed_at=?,archived_at=?,
                  subscription_detail='Unused import placeholder removed'
                  WHERE lower(name) IN ('imported email','imported emails') AND source_kind='email' AND archived_at IS NULL
                  AND NOT EXISTS(SELECT 1 FROM posts WHERE posts.source_id=sources.id)
                  AND NOT EXISTS(SELECT 1 FROM jobs WHERE jobs.source_id=sources.id)
                  AND NOT EXISTS(SELECT 1 FROM post_sources WHERE post_sources.source_id=sources.id)""" + scope, [now, now, *args])
    cutoff = (stamp - timedelta(days=30)).isoformat()
    db.execute("UPDATE sources SET archived_at=? WHERE subscription_status='unsubscribed' AND archived_at IS NULL AND unsubscribed_at<=?" + scope,
               [now, cutoff, *args])
    db.execute('DELETE FROM source_mail_actions WHERE source_id IN (SELECT id FROM sources WHERE archived_at IS NOT NULL' + scope + ')', args)


def management_source(source, post_count=0):
    source = dict(source)
    source['category'] = category(source)
    source['post_count'] = post_count
    source['management_name'] = 'Personal imports' if source['name'].strip().casefold() in IMPORT_NAMES else source['name']
    return source
