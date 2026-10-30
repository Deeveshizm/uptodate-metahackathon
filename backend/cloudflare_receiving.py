"""Authenticated Cloudflare envelope delivery and a durable private MIME inbox."""
import hashlib
import hmac
import os
import re
import threading
import time
import uuid
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from .receiving import ReceivingError, ResendRelay, timestamp
from .relay_aliases import active_alias

MAX_EMAIL_BYTES = 2_000_000
INGEST_PATH = "/webhooks/cloudflare/email"


def receiving_domain():
    domain = os.environ.get("CLOUDFLARE_RECEIVING_DOMAIN", "").lower()
    if domain and (len(domain) > 253 or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain)):
        raise ValueError("CLOUDFLARE_RECEIVING_DOMAIN must be a domain name")
    return domain


def signature(secret, sent_at, recipient, raw):
    digest = hashlib.sha256(raw).hexdigest()
    value = f"v1\nPOST\n{INGEST_PATH}\n{sent_at}\n{recipient}\n{digest}"
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def authentic_delivery(secret, sent_at, recipient, raw, supplied):
    if len(secret) < 32 or not re.fullmatch(r"[0-9]{10}", sent_at) or abs(time.time() - int(sent_at)) > 300:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", supplied):
        return False
    return hmac.compare_digest(signature(secret, sent_at, recipient, raw), supplied)


def parse_message(raw, recipient, message_id):
    mail = BytesParser(policy=policy.default).parsebytes(raw)
    message = {"id": message_id, "to": [recipient], "from": str(mail.get("From", "")),
               "subject": str(mail.get("Subject", "")), "headers": {}, "authentication": {}}
    for kind in ("plain", "html"):
        part = mail.get_body(preferencelist=(kind,))
        try:
            content = part.get_content() if part else ""
        except (LookupError, UnicodeError, ValueError):
            content = ""
        message["text" if kind == "plain" else "html"] = content if isinstance(content, str) else ""
    for key in ("List-Unsubscribe", "List-Unsubscribe-Post"):
        if mail.get(key):
            message["headers"][key] = str(mail[key])
    # MIME Authentication-Results can be forged. Verify the actual DKIM
    # signature before trusting publisher unsubscribe instructions or branding.
    sender_domain = parseaddr(message["from"])[1].rsplit("@", 1)[-1].lower()
    # Providers can prepend their own signature to the publisher's. Verify
    # the aligned signature at its actual index, rather than only index zero.
    # Bound DNS work for messages carrying many signatures.
    attempts = 0
    for index, signed in enumerate(mail.get_all("DKIM-Signature", [])[:10]):
        domain = re.search(r"(?:^|;)\s*d=([^;\s]+)", str(signed), re.I)
        signer = domain[1].lower() if domain else ""
        if not signer or not (sender_domain == signer or sender_domain.endswith("." + signer)):
            continue
        attempts += 1
        import dkim
        try:
            if dkim.DKIM(raw, timeout=3).verify(idx=index):
                message["authentication"]["dkim"] = "pass"
                break
        except Exception:
            # A failed DNS lookup cannot grant authenticated privileges or
            # prevent checking a later independent publisher signature.
            pass
        if attempts >= 3:
            break
    return message


class CloudflareReader:
    def __init__(self, store):
        self.store = store

    def messages(self):
        with self.store.connect() as db:
            rows = db.execute("SELECT i.id,i.recipient FROM cloudflare_inbound i "
                              "WHERE NOT EXISTS(SELECT 1 FROM test_relay_receipts r WHERE r.message_id=i.id) "
                              "ORDER BY i.created_at LIMIT 50").fetchall()
        return iter({"id": row["id"], "to": [row["recipient"]]} for row in rows)

    def message(self, message_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM cloudflare_inbound WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise ReceivingError("Incoming email is no longer available")
        message=parse_message(bytes(row["raw"]), row["recipient"], row["id"])
        message['created_at']=row['created_at']
        return message


class CloudflareRelay(ResendRelay):
    def __init__(self, domain, store):
        self.domain, self.reader = domain, CloudflareReader(store)
        self.lock, self.last_error = threading.Lock(), None

    def profile(self, db, profile):
        result = super().profile(db, profile)
        return {**result, "alias_scope": "cloudflare"}

    def sync(self, store):
        result = super().sync(store)
        with store.connect() as db:
            # Parsed jobs/candidates and receipts are durable. Drop raw MIME
            # immediately after processing rather than storing attachments.
            db.execute("DELETE FROM cloudflare_inbound WHERE id IN (SELECT message_id FROM test_relay_receipts)")
        return result


class ReceivingRelay:
    """New aliases use Cloudflare; existing addresses keep their provider."""
    def __init__(self, primary, legacy):
        self.primary, self.legacy = primary, legacy
        self.reader, self.last_error = primary.reader, None

    def profile(self, db, profile):
        existing = db.execute("SELECT address FROM test_relay_aliases WHERE user_id=?", (profile["id"],)).fetchone()
        if existing and not existing["address"].endswith("@" + self.primary.domain):
            if not self.legacy or not existing["address"].endswith("@" + self.legacy.domain):
                raise ValueError("Keep the original receiving provider configured for existing subscriptions")
            return self.legacy.profile(db, profile)
        return self.primary.profile(db, profile)

    def sync(self, store):
        try:
            result = self.primary.sync(store)
        except ReceivingError as exc:
            self.last_error = str(exc)
            raise
        self.last_error = None
        if self.legacy and self.legacy.reader:
            try:
                older = self.legacy.sync(store)
                result["received"] += older["received"]
                result["queued"] += older["queued"]
            except ReceivingError as exc:
                # A Resend outage must not block the independent custom domain.
                self.last_error = str(exc)
        return result


def relay_from_environment(store):
    legacy = ResendRelay.from_environment()
    if os.environ.get("CLOUDFLARE_RECEIVING_ENABLED") != "1":
        return legacy
    domain = receiving_domain()
    if not domain or len(os.environ.get("CLOUDFLARE_INGEST_SECRET", "")) < 32:
        raise ValueError("Configure the Cloudflare receiving domain and ingestion secret before activation")
    return ReceivingRelay(CloudflareRelay(domain, store), legacy)


def save_delivery(store, recipient, raw):
    # Envelope recipient comes from the signed Worker request, never MIME To,
    # Cc, forwarded headers, URL parameters, or an app-provided user ID.
    message_id = str(uuid.UUID(bytes=hashlib.sha256(recipient.encode() + b"\0" + raw).digest()[:16]))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        owner = active_alias(db, recipient)
        if not owner:
            return None
        if not db.execute("SELECT 1 FROM test_relay_receipts WHERE message_id=?", (message_id,)).fetchone():
            existing = db.execute("SELECT 1 FROM cloudflare_inbound WHERE id=?", (message_id,)).fetchone()
            pending = db.execute("SELECT COUNT(*) FROM cloudflare_inbound WHERE user_id=?", (owner["user_id"],)).fetchone()[0]
            if not existing and pending >= 100:
                raise ReceivingError("Incoming mailbox is temporarily full")
            db.execute("INSERT OR IGNORE INTO cloudflare_inbound(id,user_id,recipient,raw,created_at) VALUES(?,?,?,?,?)",
                       (message_id, owner["user_id"], recipient, raw, timestamp()))
    return message_id
