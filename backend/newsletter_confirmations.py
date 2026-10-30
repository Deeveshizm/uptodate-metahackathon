"""Private, explicit publisher confirmation handoffs; never auto-open email links."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .receiving import timestamp
from .retrieval import SourceError, public_url
from .source_identity import publisher_sender_rank


def signup_code(db, owner, alias, message):
    """Only current, authenticated codes for an explicitly started signup."""
    auth=message.get('authentication') or {}
    if auth.get('dkim')!='pass' and auth.get('dmarc')!='pass':return None
    subject=str(message.get('subject') or '')
    if re.search(r'password|reset|payment|bank|two.factor|2fa|security alert',subject,re.I):return None
    now=datetime.now(timezone.utc);cutoff=(now-timedelta(minutes=10)).isoformat()
    created=message.get('created_at')
    if created:
        try:received=datetime.fromisoformat(str(created).replace('Z','+00:00'))
        except ValueError:return None
        if received.tzinfo is None or received<now-timedelta(minutes=10) or received>now+timedelta(minutes=1):return None
    else:received=now
    sender=parseaddr(str(message.get('from') or ''))[1].lower()
    domain=sender.rsplit('@',1)[-1]
    sources=db.execute("SELECT * FROM sources s WHERE s.user_id=? AND s.subscription_alias=? AND s.archived_at IS NULL "
        "AND s.subscription_status IN ('queued','submitting','submitted','confirmation_required','manual_action','failed','email_received') "
        "AND (s.setup_expires_at>? OR EXISTS(SELECT 1 FROM jobs j WHERE j.source_id=s.id AND j.user_id=s.user_id AND j.kind IN ('newsletter','subscription') AND j.created_at>?))",(owner,alias,now.isoformat(),cutoff)).fetchall()
    candidates=[s for s in sources if publisher_sender_rank(s,sender)>0 or ('.' in domain and s['expected_sender_domain'].endswith('.'+domain))]
    if len(candidates)!=1:return None
    text=subject+'\n'+str(message.get('text') or '')+'\n'+BeautifulSoup(message.get('html') or '','html.parser').get_text(' ',strip=True)
    if not re.search(r'verif|confirm|sign[ -]?in|log[ -]?in|one[ -]?time|\botp\b',text,re.I):return None
    codes=set()
    patterns=[r'\b(?:code|otp)\b[^\w]{0,12}(?:is\s*)?([A-Z0-9]{4,8})\b',
        r'\b(?:verification|confirmation|sign[ -]?in|log[ -]?in|one[ -]?time)\b[^\d]{0,80}\b(\d{4,8})\b',
        r'\b(\d{4,8})\b\s+is your\b[^\n]{0,40}\bcode\b']
    for pattern in patterns:
        for match in re.finditer(pattern,text,re.I):
            value=match[1]
            if any(c.isdigit() for c in value):codes.add(value.upper())
    if len(codes)!=1:return None
    return next(iter(codes)),candidates[0],(received+timedelta(minutes=10)).isoformat()


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
    code_info=signup_code(db,owner,alias,message)
    code='';expires_at=None;kind='link'
    if code_info:
        code,matched,expires_at=code_info;kind='code';url=None
    subject = str(message.get('subject') or '')[:300]
    if kind=='code':subject='Newsletter verification code'
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
    actionable=bool(url or code)
    state = 'pending' if actionable else 'ignored'
    db.execute("INSERT INTO newsletter_confirmations(id,message_id,user_id,source_id,subject,sender_domain,action_url,status,created_at,kind,verification_code,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(message_id) DO NOTHING",
               (uuid.uuid4().hex, message_id, owner, matched['id'] if matched else None, subject if actionable else '', sender_domain if actionable else '', url or '', state, timestamp(),kind,code,expires_at))
    if actionable and matched:
        sender=parseaddr(str(message.get('from') or ''))[1].lower()
        if kind=='link' and publisher_sender_rank(matched,sender)==2:
            # Shared mail domains identify publications by their mailbox. Keep
            # that identity when the user later approves the sending domain.
            db.execute('UPDATE sources SET email_sender=? WHERE id=? AND user_id=?',(sender,matched['id'],owner))
        detail='Verification code received. Open Finish setup to view and copy it.' if kind=='code' else 'Confirmation email received. Use Confirm subscription in Manage.'
        db.execute("UPDATE sources SET subscription_status='confirmation_required',subscription_detail=? WHERE id=? AND user_id=?",(detail,matched['id'], owner))
    return matched


def complete_on_delivery(db, source, message):
    auth = message.get('authentication') or {}
    if source and (auth.get('dkim') == 'pass' or auth.get('dmarc') == 'pass'):
        db.execute("UPDATE newsletter_confirmations SET status='confirmed',action_url='',verification_code='' WHERE source_id=? AND status IN ('pending','opened')", (source['id'],))


def expire_confirmations(db):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    db.execute("UPDATE newsletter_confirmations SET status='expired',action_url='',verification_code='' WHERE status IN ('pending','opened') AND (created_at<? OR (expires_at IS NOT NULL AND expires_at<=?))", (cutoff,timestamp()))
