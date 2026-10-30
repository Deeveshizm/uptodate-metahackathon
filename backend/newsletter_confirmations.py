"""Private, explicit publisher confirmation handoffs; never auto-open email links."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .receiving import timestamp
from .retrieval import SourceError, public_url


def confirmation_url(message):
    subject = str(message.get('subject') or '')
    if re.search(r'password|reset|sign[ -]?in|log[ -]?in|security|one.time|account', subject, re.I):
        return None
    auth = message.get('authentication') or {}
    if auth.get('dkim') != 'pass' and auth.get('dmarc') != 'pass':
        return None
    soup = BeautifulSoup(message.get('html') or '', 'html.parser')
    links = set()
    for a in soup.find_all('a', href=True):
        label = a.get_text(' ', strip=True)
        if not re.search(r'confirm|verif|activat', label, re.I) or re.search(r'unsubscrib|password|account|sign[ -]?in|log[ -]?in', label, re.I):
            continue
        try:
            url = public_url(a['href'])
        except SourceError:
            continue
        if urlsplit(url).scheme == 'https':
            links.add(url)
    return next(iter(links)) if len(links) == 1 else None


def record_confirmation(db, owner, alias, message, matched=None):
    message_id = message['id']
    if db.execute('SELECT 1 FROM newsletter_confirmations WHERE message_id=?', (message_id,)).fetchone():
        return matched
    url = confirmation_url(message)
    subject = str(message.get('subject') or '')[:300]
    sender_domain = parseaddr(str(message.get('from') or ''))[1].rsplit('@', 1)[-1].lower()
    if url and not matched:
        # Match an explicitly requested publisher by a distinctive subject
        # prefix, even when its mail service uses a different sending domain.
        prefix = re.split(r'\bconfirm|\bverif|\bactivat', subject, maxsplit=1, flags=re.I)[0]
        words = set(re.findall(r'[a-z0-9]{3,}', prefix.lower())) - {'please','your','email','newsletter','subscription','signup','the','our','welcome'}
        sources = db.execute("SELECT * FROM sources WHERE user_id=? AND subscription_alias=? "
                             "AND archived_at IS NULL AND subscription_status IN ('manual_action','failed','submitting','submitted','confirmation_required','email_received')", (owner, alias)).fetchall()
        candidates = [s for s in sources if words and words <= set(re.findall(r'[a-z0-9]{3,}', s['name'].lower()))]
        if len(candidates) == 1:
            matched = candidates[0]
        elif not candidates:
            removed=db.execute("SELECT name FROM sources WHERE user_id=? AND subscription_alias=? AND subscription_status='unsubscribed' AND archived_at IS NOT NULL",(owner,alias)).fetchall()
            if words and any(words <= set(re.findall(r'[a-z0-9]{3,}',s['name'].lower())) for s in removed):
                url=None
    # Unknown account-verification emails cannot masquerade as newsletter
    # confirmations. An unmatched newsletter needs explicit body evidence.
    text = BeautifulSoup(message.get('html') or '', 'html.parser').get_text(' ', strip=True) + ' ' + str(message.get('text') or '')
    if not matched and not re.search(r'\bnewsletter\b', text, re.I):
        url = None
    if matched and matched['subscription_status'] in ('unsubscribed','unsubscribe_pending','unsubscribing'):
        url = None
    state = 'pending' if url else 'ignored'
    db.execute("INSERT INTO newsletter_confirmations(id,message_id,user_id,source_id,subject,sender_domain,action_url,status,created_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) DO NOTHING",
               (uuid.uuid4().hex, message_id, owner, matched['id'] if matched else None, subject if url else '', sender_domain if url else '', url or '', state, timestamp()))
    if url and matched:
        db.execute("UPDATE sources SET subscription_status='confirmation_required',subscription_detail='Confirmation email received. Use Confirm subscription in Manage.' WHERE id=? AND user_id=?",
                   (matched['id'], owner))
    return matched


def complete_on_delivery(db, source, message):
    auth = message.get('authentication') or {}
    if source and (auth.get('dkim') == 'pass' or auth.get('dmarc') == 'pass'):
        db.execute("UPDATE newsletter_confirmations SET status='confirmed',action_url='' WHERE source_id=? AND status IN ('pending','opened')", (source['id'],))


def expire_confirmations(db):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    db.execute("UPDATE newsletter_confirmations SET status='expired',action_url='' WHERE created_at<? AND status IN ('pending','opened')", (cutoff,))
