"""Atomic generation claims; public-source reuse with private feed associations."""
import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

from .editorial import PIPELINE_VERSION, EDITOR, VERIFIER
from .retrieval import SourceError


class GenerationPending(SourceError):
    pass


def public_identity(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password:
        return None
    # Anonymous retrieval alone is insufficient: signed/recipient-specific URLs
    # must not enter the shared cache. Unknown query parameters stay tenant-only.
    if parts.query:
        return None
    if re.search(r"/(?:private|share|shared|invite|auth|account|inbox|preview)(?:/|$)", parts.path, re.I):
        return None
    if re.search(r"/(?:[0-9a-f]{24,}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?:/|$)", parts.path, re.I):
        return None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", "", ""))


class Artifacts:
    def __init__(self, store):
        self.store = store

    def identity(self, document, user_id, variant):
        public = public_identity(document.url)
        scope = "public" if public and not document.metadata.get("private") else "user:" + user_id
        key = hashlib.sha256(json.dumps({"scope": scope, "url": public or document.url,
            "revision": document.content_hash, "variant": variant, "language": "en",
            "pipeline": PIPELINE_VERSION, "prompts": hashlib.sha256((EDITOR + VERIFIER).encode()).hexdigest()}, sort_keys=True).encode()).hexdigest()
        return key, scope

    def obtain(self, document, user_id, variant, producer):
        key, scope = self.identity(document, user_id, variant)
        now, token = time.time(), uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO generation_artifacts(cache_key,scope,source_url,content_hash,status,claim_token,lease_until,updated_at) VALUES(?,?,?,?,'processing',?,?,?)",
                       (key, scope, document.url, document.content_hash, token, now + 1200, now))
            row = db.execute("SELECT * FROM generation_artifacts WHERE cache_key=?", (key,)).fetchone()
            if row["status"] == "ready":
                return key, json.loads(row["result"]), True
            if row["claim_token"] != token:
                if row["status"] == "failed" and row["updated_at"] > now - 60:
                    raise SourceError("This source recently failed generation; retry after a minute")
                if row["status"] == "processing" and row["lease_until"] > now:
                    raise GenerationPending("This story is already being generated; your feed will attach when it is ready")
                changed = db.execute("UPDATE generation_artifacts SET status='processing',claim_token=?,lease_until=?,updated_at=?,error=NULL WHERE cache_key=? AND (status='failed' OR lease_until<=?)",
                                     (token, now + 1200, now, key, now)).rowcount
                if not changed:
                    raise GenerationPending("This story is already being generated")
        try:
            result = producer()
            deleted = False
            with self.store.connect() as db:
                if scope != "public" and not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                    db.execute("DELETE FROM generation_artifacts WHERE cache_key=? AND claim_token=?", (key, token))
                    deleted = True
                else:
                    changed = db.execute("UPDATE generation_artifacts SET status='ready',result=?,updated_at=?,claim_token=NULL WHERE cache_key=? AND claim_token=? AND status='processing'",
                                         (json.dumps(result), time.time(), key, token)).rowcount
            if deleted:
                raise SourceError("Account was deleted; private generation was discarded")
            if not changed:
                raise GenerationPending("The generation lease changed; waiting for the current result")
            return key, result, False
        except SourceError as exc:
            with self.store.connect() as db:
                db.execute("UPDATE generation_artifacts SET status='failed',error=?,updated_at=?,claim_token=NULL WHERE cache_key=? AND claim_token=?", (str(exc), time.time(), key, token))
            raise
        except Exception:
            with self.store.connect() as db:
                db.execute("UPDATE generation_artifacts SET status='failed',error='Generation interrupted',updated_at=?,claim_token=NULL WHERE cache_key=? AND claim_token=?", (time.time(), key, token))
            raise
