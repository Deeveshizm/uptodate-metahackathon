"""Stable publisher identity and preservation of existing subscription history."""
import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def publisher_sender_rank(source, sender, message=None):
    """Prefer a bound mailbox, then a publication subdomain, then its domain."""
    source=dict(source)
    local, separator, domain=sender.lower().rpartition('@')
    if not separator:return 0
    host=(urlsplit(source.get('url') or source.get('signup_url') or '').hostname or '').removeprefix('www.')
    if host=='tldr.tech' and domain=='tldrnewsletter.com':
        # TLDR publishes on a different domain and several editions share a
        # mailbox. An explicit edition header outranks a generic sender match.
        channel=urlsplit(source.get('url') or source.get('signup_url')).path.strip('/').split('/')[0] or 'tech'
        if message:
            text=str(message.get('text') or '')
            if not text and message.get('html'):
                from bs4 import BeautifulSoup
                text=BeautifulSoup(message['html'],'html.parser').get_text('\n',strip=True)
            import re
            found=re.search(r'(?m)^\s*TLDR(?:\s+([A-Za-z][A-Za-z &-]{0,30}?))?\s+\d{4}-\d{2}-\d{2}\s*$',text)
            if found:
                actual=(found[1] or 'tech').strip().lower().replace(' ','-')
                return 4 if actual==channel.lower() else 0
        return 1
    bound=source.get('email_sender','').lower()
    if bound:return 3 if sender.lower()==bound else 0
    expected=source.get('expected_sender_domain','').lower()
    if expected and expected==local+'.'+domain:return 2
    return int(bool(expected and (domain==expected or domain.endswith('.'+expected))))


def publisher_details(url,name):
    """Publisher-specific transport identity, without guessing a signup API."""
    parts=urlsplit(url)
    if (parts.hostname or '').removeprefix('www.')=='tldr.tech':
        channel=parts.path.strip('/').split('/')[0]
        if channel in ('','tech'):return 'TLDR','tldrnewsletter.com'
        if channel in ('ai','dev','devops','design','marketing','crypto','infosec','product','founders','data'):
            return 'TLDR '+('AI' if channel=='ai' else channel.title()),'tldrnewsletter.com'
    domain=parts.hostname or ''
    for prefix in ('www.','newsletter.','newsletters.'):
        if domain.startswith(prefix):domain=domain[len(prefix):];break
    return name,domain


def newsletter_identity(url):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().lstrip("?").startswith("utm_") and key.lower() not in (
                 "fbclid", "gclid", "gad_source", "gad_campaignid", "gbraid", "wbraid", "msclkid", "ttclid", "mc_cid", "mc_eid")]
    canonical = urlunsplit((parts.scheme.lower(), host, parts.path.rstrip("/"), urlencode(sorted(query)), ""))
    return "newsletter:" + hashlib.sha256(canonical.encode()).hexdigest()


def reconcile_newsletters(db):
    groups = {}
    for row in db.execute("SELECT * FROM sources WHERE sender LIKE 'newsletter:%' AND url!=''"):
        groups.setdefault((row["user_id"], newsletter_identity(row["url"])), []).append(dict(row))
    priority = {"receiving": 8, "email_received": 7, "confirmation_required": 6, "submitted": 5,
                "submitting": 4, "queued": 3, "unsubscribed": -3, "unsubscribe_pending": -2, "unsubscribing": -2}
    for (_, identity), records in groups.items():
        keep = max(records, key=lambda row: (priority.get(row["subscription_status"], 0), row["id"]))
        for record in records:
            if record["id"] == keep["id"]:
                continue
            old, new = record["id"], keep["id"]
            db.execute("INSERT OR IGNORE INTO post_sources(post_id,source_id,user_id) SELECT post_id,?,user_id FROM post_sources WHERE source_id=?", (new, old))
            db.execute("DELETE FROM post_sources WHERE source_id=?", (old,))
            db.execute("UPDATE posts SET source_id=? WHERE source_id=?", (new, old))
            db.execute("UPDATE jobs SET source_id=? WHERE source_id=?", (new, old))
            db.execute("INSERT INTO source_mail_actions(source_id,user_id,unsubscribe_url,one_click,updated_at) SELECT ?,user_id,unsubscribe_url,one_click,updated_at FROM source_mail_actions WHERE source_id=? ON CONFLICT(source_id) DO UPDATE SET unsubscribe_url=excluded.unsubscribe_url,one_click=excluded.one_click,updated_at=excluded.updated_at WHERE excluded.updated_at>source_mail_actions.updated_at", (new, old))
            db.execute("DELETE FROM sources WHERE id=?", (old,))
        db.execute("UPDATE sources SET sender=? WHERE id=?", (identity, keep["id"]))
