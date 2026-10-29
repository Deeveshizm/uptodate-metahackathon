"""Resend-managed aliases for verified accounts and owner-scoped email ingestion.

Only recipients returned by the authenticated provider API select an account;
message headers, forwarded-for hints and client-supplied addresses cannot route mail.
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from urllib.parse import parse_qsl, urlsplit

import httpx

from .pipeline import parse_newsletter, safe_url


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class ReceivingError(Exception):
    pass


class ResendReader:
    """Only fixed-origin GET requests; no send, forward, delete, or remote URLs."""

    def __init__(self, api_key, transport=None, interval=0.6):
        self.api_key, self.transport, self.interval = api_key, transport, interval
        self.lock, self.last_request = threading.Lock(), 0.0

    def get(self, path, params=None):
        with self.lock:
            time.sleep(max(0, self.last_request + self.interval - time.monotonic()))
            self.last_request = time.monotonic()
            try:
                with httpx.Client(timeout=20, trust_env=False, transport=self.transport) as client:
                    with client.stream("GET", "https://api.resend.com" + path,
                                       headers={"Authorization": "Bearer " + self.api_key}, params=params) as response:
                        if response.status_code in (401, 403):
                            raise ReceivingError("Receiving requires a valid Resend key with Full access")
                        if response.status_code == 429:
                            raise ReceivingError("Receiving rate limit reached; try again later")
                        if not response.is_success:
                            raise ReceivingError("Resend receiving is temporarily unavailable")
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 2_000_000:
                                raise ReceivingError("Received email exceeds the 2 MB local test limit")
                            chunks.append(chunk)
                        value = json.loads(b"".join(chunks))
            except (httpx.HTTPError, ValueError):
                raise ReceivingError("Cannot read the receiving service; try again later") from None
        if not isinstance(value, dict):
            raise ReceivingError("Unexpected receiving service response")
        return value

    def messages(self):
        after = None
        # Bounded prototype polling. Newest-first scans are repeated so a failed
        # fetch never advances a cursor past an unprocessed message.
        for _ in range(3):
            page = self.get("/emails/receiving", {"limit": 100, **({"after": after} if after else {})})
            rows = page.get("data")
            if not isinstance(rows, list):
                raise ReceivingError("Unexpected receiving service response")
            for row in reversed(rows):
                if not isinstance(row, dict):
                    raise ReceivingError("Unexpected receiving service response")
                yield row
            if not page.get("has_more") or not rows:
                return
            try:
                after = str(uuid.UUID(rows[-1]["id"]))
            except (ValueError, KeyError, TypeError):
                raise ReceivingError("Unexpected receiving service pagination cursor") from None

    def message(self, message_id):
        return self.get("/emails/receiving/" + str(uuid.UUID(message_id)), {"html_format": "cid"})


def recipient_set(message):
    values = message.get("to", [])
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        return set()
    return {address.lower() for _, address in getaddresses(values) if address}


def email_channel(sender, subject):
    # Personal mail can carry both forwarded tests and separate Muse tasks.
    # Dedicated publisher addresses remain one stream across changing headlines.
    domain=sender.rsplit('@',1)[-1]
    if domain not in ('gmail.com','googlemail.com','outlook.com','hotmail.com','yahoo.com','icloud.com','me.com'):
        return ''
    title=subject.split(':',1)[0].casefold()
    title=re.sub(r"\b(?:today|tomorrow|yesterday)(?:['’]s)?\b|\b(?:daily|weekly|edition|pick|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",' ',title)
    title=re.sub(r'\b\d[\d/.-]*\b',' ',title)
    return re.sub(r'[^\w]+',' ',title).strip()[:160]


def newsletter_payload(message):
    subject = str(message.get("subject") or "Untitled edition")[:300]
    # Confirmation and account-action messages are delivered, never crawled or
    # turned into posts. The user can confirm through the provider's dashboard.
    if re.search(r"confirm|verif(?:y|ication)|activat|password|sign[ -]?in|one.time|security.code", subject, re.I):
        return None, "manual_action"
    if re.search(r"\bwelcome\b|thanks? (?:you )?for subscrib|unsubscribed|subscription cancell", subject, re.I):
        return None, "received"
    mail = EmailMessage()
    try:
        mail["From"], mail["Subject"] = message.get("from", ""), subject
        mail.set_content(message.get("text") or "")
        if message.get("html"):
            mail.add_alternative(message["html"], subtype="html")
        parsed = parse_newsletter(mail.as_bytes(), require_links=False)
    except (ValueError, TypeError, LookupError, AttributeError):
        return None, "received"
    links = []
    parsed['image_urls']=[]
    for value in parsed["urls"]:
        url = safe_url(value)
        if not url:
            continue
        parts = urlsplit(url)
        if re.search(r"unsub|subscrib|confirm|verif|activat|sign.?in|login|logout|password|preferences|privacy|terms|tracking|click\.", url, re.I):
            continue
        if any(re.search(r"token|code|signature|secret|email|auth", key, re.I) for key, _ in parse_qsl(parts.query)):
            continue
        if parts.hostname in ("x.com", "twitter.com", "facebook.com", "instagram.com", "linkedin.com"):
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|svg|webp|ico)(?:$|\?)", url, re.I):
            parsed['image_urls'].append(url)
            continue
        links.append(url)
    parsed["urls"] = links[:50]
    parsed["links"] = [link for link in parsed.get("links", []) if link["url"] in parsed["urls"]]
    parsed["html"] = str(message.get("html") or "")[:200000]
    # EmailMessage generates random MIME boundaries. Deduplicate normalized
    # content too, including redeliveries with a different provider message ID.
    parsed["fingerprint"] = hashlib.sha256(json.dumps({key: parsed[key] for key in ("sender", "subject", "text", "urls")}, sort_keys=True).encode()).hexdigest()
    return (parsed, "queued") if links or (len(parsed["text"]) >= 160 and len(parsed["text"].split()) >= 25) else (None, "received")


def unsubscribe_action(message):
    """Use authenticated publisher instructions, never guess a recipient URL."""
    authentication = message.get("authentication") or {}
    if authentication.get("dmarc") != "pass" and authentication.get("dkim") != "pass":
        return None
    raw = message.get("headers") or {}
    if not isinstance(raw, dict):
        return None
    headers = {str(key).lower(): str(value) for key, value in raw.items()}
    links = re.findall(r"<(https://[^<>\s]+)>", headers.get("list-unsubscribe", ""), re.I)
    from .retrieval import SourceError, public_url
    for link in links[:3]:
        try:
            url = public_url(link)
        except SourceError:
            continue
        one_click = headers.get("list-unsubscribe-post", "").strip().lower() == "list-unsubscribe=one-click"
        return url, one_click
    # Some publishers provide only a footer link. Opening it can require a
    # confirmation page, so it must never become an RFC 8058 POST action.
    from bs4 import BeautifulSoup
    candidates = set()
    soup = BeautifulSoup(str(message.get("html") or ""), "html.parser")
    for anchor in soup.find_all("a", href=True):
        if re.search(r"\bunsubscrib", anchor.get_text(" ", strip=True), re.I):
            candidates.add(anchor["href"])
    for match in re.finditer(r"(?im)^\s*unsubscribe(?:\s+here)?\s*:?\s*(https://[^\s<>]+)\s*$", str(message.get("text") or "")):
        candidates.add(match.group(1))
    valid = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.startswith("https://"):
            continue
        try:
            valid.add(public_url(candidate))
        except SourceError:
            continue
    if len(valid) == 1:
        return valid.pop(), False
    return None


class ResendRelay:
    def __init__(self, domain, reader=None):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.resend\.app", domain):
            raise ValueError("RESEND_RECEIVING_DOMAIN must be your Resend receiving subdomain")
        self.domain, self.reader = domain, reader
        self.lock = threading.Lock()
        self.last_error = None

    @classmethod
    def from_environment(cls):
        # Existing installations retain their domain and reserved addresses.
        # RELAY_TEST_USER_ID no longer selects or restricts mailbox owners.
        domain = os.environ.get("RESEND_RECEIVING_DOMAIN") or os.environ.get("RELAY_TEST_DOMAIN", "")
        if not domain:
            return None
        key = os.environ.get("RESEND_API_KEY", "")
        return cls(domain, ResendReader(key) if key else None)

    def profile(self, db, profile):
        owner_id = str(uuid.UUID(profile["id"]))
        if profile.get("alias_status") == "disabled":
            # Reserve revoked addresses forever; never give them to a new user.
            db.execute("UPDATE test_relay_aliases SET user_id=NULL WHERE user_id=?", (owner_id,))
            return {**profile, "alias": None}
        if profile.get("alias"):
            raise ValueError("Resend receiving cannot override an existing permanent alias")
        status = "active" if self.reader else "provisioning"
        address = "n_" + uuid.uuid4().hex + "@" + self.domain
        db.execute("INSERT INTO test_relay_aliases(address,user_id,status,created_at) VALUES(?,?,?,?) ON CONFLICT(user_id) DO NOTHING",
                   (address, owner_id, status, timestamp()))
        existing = db.execute("SELECT * FROM test_relay_aliases WHERE user_id=?", (owner_id,)).fetchone()
        if not existing["address"].endswith("@" + self.domain):
            raise ValueError("Existing addresses require their original receiving domain")
        # A configured catch-all accepts new aliases immediately. A delivery
        # probe is not required separately for every verified account.
        db.execute("UPDATE test_relay_aliases SET status=? WHERE user_id=?", (status, owner_id))
        return {**profile, "alias": existing["address"], "alias_status": status, "alias_scope": "resend"}

    def sync(self, store):
        if not self.reader:
            raise ReceivingError("Resend receiving is not configured on the server")
        if not self.lock.acquire(blocking=False):
            return {"status": "processing", "received": 0, "queued": 0}
        try:
            result = self._sync(store)
            self.last_error = None
            return result
        except ReceivingError as exc:
            self.last_error = str(exc)
            raise
        finally:
            self.lock.release()

    def _sync(self, store):
        with store.connect() as db:
            aliases = {row["address"]: dict(row) for row in db.execute(
                "SELECT r.* FROM test_relay_aliases r JOIN users u ON u.id=r.user_id "
                "WHERE r.status='active' AND u.alias=r.address").fetchall()
                if row["address"].endswith("@" + self.domain)}
        if not aliases:
            return {"status": "waiting_for_account", "received": 0, "queued": 0}
        result = {"status": "checked", "received": 0, "queued": 0}
        for summary in self.reader.messages():
            recipients = recipient_set(summary)
            # Ambiguous multi-recipient messages are not guessed or broadcast.
            if len(recipients) != 1:
                continue
            relay = aliases.get(next(iter(recipients)))
            if not relay:
                continue
            owner_id = relay["user_id"]
            try:
                message_id = str(uuid.UUID(summary["id"]))
            except (ValueError, KeyError, TypeError):
                raise ReceivingError("Unexpected receiving service message ID") from None
            with store.connect() as db:
                if db.execute("SELECT 1 FROM test_relay_receipts WHERE message_id=?", (message_id,)).fetchone():
                    continue
            message = self.reader.message(message_id)
            if message.get("id") != message_id or recipient_set(message) != {relay["address"]}:
                raise ReceivingError("Received message identity or recipient changed; nothing was imported")
            parsed, state = newsletter_payload(message)
            if parsed:parsed['receiving_message_id']=message_id
            with store.connect() as db:
                # Recheck ownership after network calls; deletion revokes delivery.
                db.execute("BEGIN IMMEDIATE")
                live = db.execute("SELECT r.address FROM test_relay_aliases r JOIN users u ON u.id=r.user_id "
                                  "WHERE r.address=? AND r.user_id=? AND r.status='active' AND u.alias=r.address",
                                  (relay["address"], owner_id)).fetchone()
                if not live or db.execute("SELECT 1 FROM test_relay_receipts WHERE message_id=?", (message_id,)).fetchone():
                    continue
                sender = parseaddr(str(message.get("from", "")))[1].lower()
                sender_domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
                channel=email_channel(sender,str(message.get('subject') or ''))
                sources = db.execute("SELECT * FROM sources WHERE user_id=? AND subscription_alias=? AND expected_sender_domain!='' AND sender LIKE 'newsletter:%'",
                                     (owner_id, relay["address"])).fetchall()
                matches = [source for source in sources if sender_domain == source["expected_sender_domain"] or sender_domain.endswith("." + source["expected_sender_domain"])]
                matched = matches[0] if len(matches) == 1 else None
                if not matches:
                    matched = db.execute("SELECT * FROM sources WHERE user_id=? AND email_sender=? AND email_topic=?", (owner_id,sender,channel)).fetchone()
                if len(matches) > 1:
                    parsed, state = None, "requires_source_match"
                if parsed and not matched and len(matches) <= 1:
                    # A new sender cannot silently become a recurring feed.
                    display, _ = parseaddr(str(message.get("from", "")))
                    auth = message.get("authentication") or {}
                    verified = auth.get("dkim") == "pass" or auth.get("dmarc") == "pass"
                    provider = "Muse" if verified and any(sender_domain == domain or sender_domain.endswith('.'+domain) for domain in ("muse.ai", "meta.ai")) else None
                    db.execute("INSERT INTO inbox_candidates(id,user_id,sender,sender_name,provider,subject,payload,channel_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,sender,channel_key) DO UPDATE SET subject=excluded.subject,payload=excluded.payload,updated_at=excluded.updated_at",
                               (uuid.uuid4().hex,owner_id,sender,display or sender_domain,provider,parsed["subject"],json.dumps(parsed),channel,timestamp(),timestamp()))
                    parsed, state = None, "needs_newsletter_approval"
                if matched:
                    action = unsubscribe_action(message)
                    if action:
                        db.execute("INSERT INTO source_mail_actions(source_id,user_id,unsubscribe_url,one_click,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET unsubscribe_url=excluded.unsubscribe_url,one_click=excluded.one_click,updated_at=excluded.updated_at",
                                   (matched["id"], owner_id, action[0], int(action[1]), timestamp()))
                    if matched["subscription_status"] in ("unsubscribed", "unsubscribe_pending", "unsubscribing"):
                        parsed, state = None, "ignored_unsubscribed"
                    else:
                        status = "receiving" if parsed else ("confirmation_required" if state == "manual_action" else "email_received")
                        detail = ("Newsletter email received at your account alias and queued for source-grounded generation" if parsed else
                                  "Publisher email requires confirmation; open it in Receiving" if state == "manual_action" else
                                  "Publisher email received at your account alias; waiting for a readable newsletter edition")
                        db.execute("UPDATE sources SET subscription_status=?,subscription_detail=? WHERE id=? AND user_id=?",
                                   (status, detail, matched["id"], owner_id))
                job_id = None
                if parsed:
                    fingerprint = "received-email:" + parsed["fingerprint"]
                    previous = db.execute("SELECT id FROM jobs WHERE user_id=? AND fingerprint=?", (owner_id, fingerprint)).fetchone()
                    if previous:
                        job_id = previous["id"]
                    else:
                        source = matched or db.execute("SELECT id FROM sources WHERE user_id=? AND sender=?", (owner_id, parsed["sender"])).fetchone()
                        source_id = source["id"] if source else uuid.uuid4().hex
                        if not source:
                            db.execute("INSERT INTO sources(id,user_id,name,sender,subscription_status,subscription_detail,subscription_alias,expected_sender_domain,source_kind) VALUES(?,?,?,?,'receiving','Email received at the account alias',?,?,'email')",
                                       (source_id, owner_id, parsed["source_name"], parsed["sender"], relay["address"], sender_domain))
                        if not matched:
                            action = unsubscribe_action(message)
                            if action:
                                db.execute("INSERT INTO source_mail_actions(source_id,user_id,unsubscribe_url,one_click,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET unsubscribe_url=excluded.unsubscribe_url,one_click=excluded.one_click,updated_at=excluded.updated_at",
                                           (source_id, owner_id, action[0], int(action[1]), timestamp()))
                        job_id = uuid.uuid4().hex
                        db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'received_email')",
                                   (job_id, owner_id, source_id, fingerprint, parsed["subject"], json.dumps(parsed), timestamp()))
                        result["queued"] += 1
                db.execute("INSERT INTO test_relay_receipts(message_id,user_id,state,job_id,received_at) VALUES(?,?,?,?,?)",
                           (message_id, owner_id, state, job_id, timestamp()))
                db.execute("UPDATE test_relay_aliases SET status='active' WHERE address=? AND user_id=?", (relay["address"], owner_id))
                db.execute("UPDATE users SET alias=? WHERE id=?", (relay["address"], owner_id))
                result["received"] += 1
        return result
