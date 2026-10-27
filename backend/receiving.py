"""Read-only Resend adapter for a dedicated, single-account local relay test.

Temporary addresses live only in SQLite. Hosted profiles and the future permanent
domain stay untouched. Resend's header-derived recipients are NOT our production
multi-tenant routing boundary; Cloudflare will supply the SMTP envelope recipient.
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
        parsed = parse_newsletter(mail.as_bytes())
    except (ValueError, TypeError, LookupError, AttributeError):
        return None, "received"
    links = []
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
            continue
        links.append(url)
    parsed["urls"] = links[:3]
    # EmailMessage generates random MIME boundaries. Deduplicate normalized
    # content too, including redeliveries with a different provider message ID.
    parsed["fingerprint"] = hashlib.sha256(json.dumps({key: parsed[key] for key in ("sender", "subject", "text", "urls")}, sort_keys=True).encode()).hexdigest()
    return (parsed, "queued") if links else (None, "received")


def unsubscribe_action(message):
    """Only server-authenticated publisher headers can supply mail actions."""
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
    return None


class TemporaryRelay:
    def __init__(self, domain, owner_id, reader=None):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.resend\.app", domain):
            raise ValueError("RELAY_TEST_DOMAIN must be your Resend receiving subdomain")
        self.domain, self.owner_id, self.reader = domain, str(uuid.UUID(owner_id)), reader
        self.lock = threading.Lock()
        self.last_error = None

    @classmethod
    def from_environment(cls):
        domain, owner = os.environ.get("RELAY_TEST_DOMAIN", ""), os.environ.get("RELAY_TEST_USER_ID", "")
        if not domain and not owner:
            return None
        if not domain or not owner:
            raise ValueError("Set both RELAY_TEST_DOMAIN and RELAY_TEST_USER_ID for the isolated relay test")
        key = os.environ.get("RESEND_API_KEY", "")
        return cls(domain, owner, ResendReader(key) if key else None)

    def profile(self, db, profile):
        if profile["id"] != self.owner_id:
            return profile
        if profile.get("alias") or profile.get("alias_status") == "disabled":
            raise ValueError("Temporary receiving cannot override a hosted relay")
        existing = db.execute("SELECT * FROM test_relay_aliases WHERE user_id=?", (self.owner_id,)).fetchone()
        if not existing:
            address = "n_" + uuid.uuid4().hex + "@" + self.domain
            db.execute("INSERT INTO test_relay_aliases(address,user_id,status,created_at) VALUES(?,?,'provisioning',?)",
                       (address, self.owner_id, timestamp()))
            existing = db.execute("SELECT * FROM test_relay_aliases WHERE user_id=?", (self.owner_id,)).fetchone()
        if not existing["address"].endswith("@" + self.domain):
            raise ValueError("Use a separate test database when changing temporary receiving domains")
        return {**profile, "alias": existing["address"], "alias_status": existing["status"], "alias_scope": "temporary_test"}

    def sync(self, store):
        if not self.reader:
            raise ReceivingError("Save RESEND_API_KEY in the ignored backend settings file")
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
            relay = db.execute("SELECT * FROM test_relay_aliases WHERE user_id=?", (self.owner_id,)).fetchone()
        if not relay:
            return {"status": "waiting_for_account", "received": 0, "queued": 0}
        result = {"status": "checked", "received": 0, "queued": 0}
        for summary in self.reader.messages():
            # This dedicated-domain test has exactly ONE permitted account.
            # No guessed owner or header-based cross-user dispatch is allowed.
            if recipient_set(summary) != {relay["address"]}:
                continue
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
            with store.connect() as db:
                # Recheck ownership after network calls; deletion revokes delivery.
                live = db.execute("SELECT * FROM test_relay_aliases WHERE address=? AND user_id=?",
                                  (relay["address"], self.owner_id)).fetchone()
                if not live:
                    return result
                sender = parseaddr(str(message.get("from", "")))[1].lower()
                sender_domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
                sources = db.execute("SELECT * FROM sources WHERE user_id=? AND subscription_alias=? AND expected_sender_domain!='' AND sender LIKE 'newsletter:%'",
                                     (self.owner_id, relay["address"])).fetchall()
                matches = [source for source in sources if sender_domain == source["expected_sender_domain"] or sender_domain.endswith("." + source["expected_sender_domain"])]
                matched = matches[0] if len(matches) == 1 else None
                if not matches:
                    matched = db.execute("SELECT * FROM sources WHERE user_id=? AND sender=?", (self.owner_id, sender)).fetchone()
                if len(matches) > 1:
                    parsed, state = None, "requires_source_match"
                if matched:
                    action = unsubscribe_action(message)
                    if action:
                        db.execute("INSERT INTO source_mail_actions(source_id,user_id,unsubscribe_url,one_click,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET unsubscribe_url=excluded.unsubscribe_url,one_click=excluded.one_click,updated_at=excluded.updated_at",
                                   (matched["id"], self.owner_id, action[0], int(action[1]), timestamp()))
                    if matched["subscription_status"] in ("unsubscribed", "unsubscribe_pending", "unsubscribing"):
                        parsed, state = None, "ignored_unsubscribed"
                    else:
                        status = "receiving" if parsed else ("confirmation_required" if state == "manual_action" else "email_received")
                        detail = ("Newsletter email received at your account alias and queued for local generation" if parsed else
                                  "Publisher email requires confirmation; open it in Receiving" if state == "manual_action" else
                                  "Publisher email received at your account alias; waiting for a readable newsletter edition")
                        db.execute("UPDATE sources SET subscription_status=?,subscription_detail=? WHERE id=? AND user_id=?",
                                   (status, detail, matched["id"], self.owner_id))
                job_id = None
                if parsed:
                    fingerprint = "received-email:" + parsed["fingerprint"]
                    previous = db.execute("SELECT id FROM jobs WHERE user_id=? AND fingerprint=?", (self.owner_id, fingerprint)).fetchone()
                    if previous:
                        job_id = previous["id"]
                    else:
                        source = matched or db.execute("SELECT id FROM sources WHERE user_id=? AND sender=?", (self.owner_id, parsed["sender"])).fetchone()
                        source_id = source["id"] if source else uuid.uuid4().hex
                        if not source:
                            db.execute("INSERT INTO sources(id,user_id,name,sender,subscription_status,subscription_detail,subscription_alias,expected_sender_domain) VALUES(?,?,?,?,'receiving','Email received at the temporary test alias',?,?)",
                                       (source_id, self.owner_id, parsed["source_name"], parsed["sender"], relay["address"], sender_domain))
                        if not matched:
                            action = unsubscribe_action(message)
                            if action:
                                db.execute("INSERT INTO source_mail_actions(source_id,user_id,unsubscribe_url,one_click,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET unsubscribe_url=excluded.unsubscribe_url,one_click=excluded.one_click,updated_at=excluded.updated_at",
                                           (source_id, self.owner_id, action[0], int(action[1]), timestamp()))
                        job_id = uuid.uuid4().hex
                        db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'received_email')",
                                   (job_id, self.owner_id, source_id, fingerprint, parsed["subject"], json.dumps(parsed), timestamp()))
                        result["queued"] += 1
                db.execute("INSERT INTO test_relay_receipts(message_id,user_id,state,job_id,received_at) VALUES(?,?,?,?,?)",
                           (message_id, self.owner_id, state, job_id, timestamp()))
                db.execute("UPDATE test_relay_aliases SET status='active' WHERE address=? AND user_id=?", (relay["address"], self.owner_id))
                db.execute("UPDATE users SET alias=? WHERE id=?", (relay["address"], self.owner_id))
                result["received"] += 1
        return result
