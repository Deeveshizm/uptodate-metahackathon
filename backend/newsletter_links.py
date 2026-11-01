"""Recover explicit article destinations without visiting subscriber tracking URLs."""
import html
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .retrieval import SourceError, public_url

ACTION = re.compile(r'(?:^|/)(?:unsub(?:scribe)?|subscribe|signup|sign-up|signin|sign-in|login|logout|confirm|verify|activate|reset-password|preferences|manage-preferences|email-preferences|privacy|terms|account)(?:/|$)',re.I)
PRIVATE_QUERY = re.compile(r'token|code|signature|secret|email|auth|unsub|recipient',re.I)


def article_url(value):
    if not isinstance(value,str) or len(value)>12000:return None
    value=html.unescape(html.unescape(value)).strip()
    for _ in range(3):
        try:value=public_url(value)
        except SourceError:return None
        parts=urlsplit(value)
        destinations=[v for k,v in parse_qsl(parts.query) if k.lower() in ('url','u','target','destination','dest','redirect','redirect_url','link')]
        # CL0/SendGrid-style wrappers encode the destination as one path
        # component. Opaque subscriber IDs after it are never retained.
        destinations += [unquote(part) for part in parts.path.split('/') if unquote(part).startswith(('https://','http://'))]
        target=next((candidate for candidate in destinations if candidate.startswith(('https://','http://'))),None)
        if target and target != value:
            value=target
            continue
        break
    try:value=public_url(value)
    except SourceError:return None
    parts=urlsplit(value)
    if ACTION.search(unquote(parts.path)):return None
    query=parse_qsl(parts.query,keep_blank_values=True)
    if any(PRIVATE_QUERY.search(k) for k,_ in query):return None
    host=(parts.hostname or '').lower()
    if re.match(r'^(?:tracking|click|clicks|trk|track|links)\.',host):return None
    clean=[(k,v) for k,v in query if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid','mc_cid','mc_eid')]
    return urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(clean),parts.fragment))


def sponsored(value):
    return bool(re.search(r'\((?:sponsor(?:ed)?|advertisement|paid partnership)\)|^\s*(?:sponsored|advertisement)\b',value,re.I))


def story_heading(anchor,label):
    return bool(anchor.find(['h1','h2','h3','h4']) or anchor.find_parent(['h1','h2','h3','h4']) or re.search(r'\(\s*\d+\s*(?:minute|min)s?\s+read\s*\)\s*$',label,re.I))


def public_issue_url(document):
    """A documented public archive is a fallback for an opaque email tracker.

    TLDR's own edition header identifies its channel and date. The content is
    fetched and checked before its links are used; no guessed story is created.
    """
    sender=document.metadata.get('email_sender','').rsplit('@',1)[-1].lower()
    author=document.metadata.get('author','').strip().lower()
    if sender not in ('tldrnewsletter.com','tldr.tech') and not author.startswith('tldr'):return None
    text=document.metadata.get('email_text','')
    found=re.search(r'(?m)^\s*TLDR(?:\s+([A-Za-z][A-Za-z &-]{0,30}?))?\s+(\d{4}-\d{2}-\d{2})\s*$',text)
    if not found:return None
    from datetime import date
    try:date.fromisoformat(found[2])
    except ValueError:return None
    channel=(found[1] or 'tech').strip().lower().replace(' ','-')
    if not re.fullmatch(r'[a-z][a-z-]{0,30}',channel):return None
    return f'https://tldr.tech/{channel}/{found[2]}'
