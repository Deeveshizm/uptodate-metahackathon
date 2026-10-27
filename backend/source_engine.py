"""Durable source-to-post and newsletter discovery jobs."""
import json
import hashlib
import re
import uuid
from urllib.parse import urlsplit
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .documents import Document, digest_links, make_spans, parse_document, primary_source_urls, publication_name
from .discovery import discover_issue, issue_mode
from .generation import generate
from .retrieval import PublicFetcher, SourceError
from .visuals import choose_visual


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class SourceEngine:
    def __init__(self, store, fetcher=None, generator=None, subscription_agent=None, illustrator=None):
        self.store = store
        self.fetcher = fetcher or PublicFetcher()
        self.generator = generator or generate
        # Muse plugs into this boundary after its actual API/runtime is identified.
        # An agent returns a truthful state, never an assumed subscription success.
        self.subscription_agent = subscription_agent
        self.illustrator = illustrator

    def stage(self, job_id, stage):
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET stage=? WHERE id=?", (stage, job_id))

    def process(self, job_id):
        with self.store.connect() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=? AND status='queued'", (job_id,)).fetchone()
            if job and job["kind"] != "visual":
                source = db.execute("SELECT subscription_status FROM sources WHERE id=? AND user_id=?", (job["source_id"], job["user_id"])).fetchone()
                if source and source["subscription_status"] in ("unsubscribed", "unsubscribe_pending", "unsubscribing"):
                    db.execute("UPDATE jobs SET status='cancelled',stage='cancelled',payload=NULL WHERE id=?", (job_id,))
                    return
            if not job or not db.execute("UPDATE jobs SET status='processing',error=NULL WHERE id=? AND status='queued'", (job_id,)).rowcount:
                return
        try:
            payload = json.loads(job["payload"])
            if job["kind"] == "visual":
                self.render_visual(job, payload)
            elif job["kind"] in ("newsletter", "subscription"):
                self.inspect_newsletter(job, payload)
            else:
                self.generate_posts(job, payload)
        except SourceError as exc:
            self.fail(job, str(exc))
        except Exception:
            self.fail(job, "Source processing failed; inspect the source or retry the job")

    def fail(self, job, message):
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='failed',stage='failed',error=? WHERE id=?", (message, job["id"]))
            if job["kind"] in ("newsletter", "subscription"):
                db.execute("UPDATE sources SET subscription_status='failed',subscription_detail=? WHERE id=? AND subscription_status NOT IN ('unsubscribed','unsubscribe_pending')", (message, job["source_id"]))

    def inspect_newsletter(self, job, payload):
        self.stage(job["id"], "inspecting_newsletter")
        resource = self.fetcher.fetch(payload["url"])
        if "html" not in resource.content_type:
            raise SourceError("Use the newsletter's public signup or homepage URL")
        soup = BeautifulSoup(resource.body, "html.parser")
        name = soup.title.get_text(" ", strip=True)[:150] if soup.title else payload["url"]
        name = publication_name(soup) or name
        from urllib.parse import urljoin
        from .pipeline import safe_url
        feed = soup.find("link", attrs={"type": re.compile(r"application/(rss|atom)\+xml")})
        feed_url = safe_url(urljoin(resource.url, feed.get("href", ""))) if feed else ""
        with self.store.connect() as db:
            user = db.execute("SELECT alias FROM users WHERE id=?", (job["user_id"],)).fetchone()
            source = db.execute("SELECT * FROM sources WHERE id=? AND user_id=?", (job["source_id"], job["user_id"])).fetchone()
        if not user or not source:
            return
        if source["subscription_status"] in ("unsubscribed", "unsubscribe_pending", "submitted", "confirmation_required", "email_received", "receiving"):
            status, detail = source["subscription_status"], source["subscription_detail"]
        elif source["subscription_status"] == "submitting":
            status, detail = "manual_action", "A previous signup may have been transmitted; check incoming mail before explicitly retrying"
        elif not payload.get("auto_subscribe", True):
            status, detail = "not_requested", "Newsletter saved; automatic signup was not requested"
        elif user["alias"].endswith(".invalid"):
            status, detail = "needs_inbox", "Connect a receiving email domain before signup; the current account alias cannot receive confirmation emails"
        elif self.subscription_agent is None:
            status, detail = "needs_agent", "Receiving alias is ready; configure the signup agent to continue"
        else:
            self.stage(job["id"], "submitting_signup")
            domain = urlsplit(resource.url).hostname or ""
            for prefix in ("www.", "newsletter.", "newsletters."):
                if domain.startswith(prefix):
                    domain = domain[len(prefix):]
                    break
            with self.store.connect() as db:
                claimed = db.execute("UPDATE sources SET subscription_status='submitting',subscription_alias=?,expected_sender_domain=? WHERE id=? AND user_id=? AND subscription_status NOT IN ('submitting','submitted','confirmation_required','email_received','receiving','unsubscribed','unsubscribe_pending')",
                                     (user["alias"], domain, job["source_id"], job["user_id"])).rowcount
            if not claimed:
                with self.store.connect() as db:
                    db.execute("UPDATE jobs SET status='completed',stage='completed',result=?,payload=NULL WHERE id=?", (json.dumps({"subscription_status": "in_progress", "detail": "Another signup already owns this subscription"}), job["id"]))
                return
            try:
                def still_allowed():
                    with self.store.connect() as db:
                        owner = db.execute("SELECT u.alias,s.subscription_status FROM users u JOIN sources s ON s.user_id=u.id WHERE u.id=? AND s.id=?", (job["user_id"], job["source_id"])).fetchone()
                    return bool(owner and owner["alias"] == user["alias"] and owner["subscription_status"] not in ("unsubscribed", "unsubscribe_pending", "unsubscribing"))
                options = {"before_submit": still_allowed} if getattr(self.subscription_agent, "supports_submission_guard", False) is True else {}
                outcome = self.subscription_agent.subscribe(url=resource.url, email=user["alias"], **options)
                status, detail = outcome["status"], outcome["detail"]
            except SourceError as exc:
                status, detail = "failed", str(exc)
            if status not in ("submitted", "confirmation_required", "manual_action", "failed"):
                raise SourceError("Signup agent returned an unrecognized subscription status")
        content = {"status": "not_requested", "detail": "Subscription-only retry; public discovery was not repeated"}
        if job["kind"] == "newsletter":
            self.stage(job["id"], "discovering_public_issue")
            try:
                content = discover_issue(resource, self.fetcher)
            except SourceError as exc:
                content = {"status": "discovery_failed", "detail": str(exc), "feed_url": feed_url}
        generation_job_id = None
        with self.store.connect() as db:
            db.execute("UPDATE sources SET name=?,signup_url=?,feed_url=?,subscription_status=CASE WHEN subscription_status IN ('unsubscribed','unsubscribe_pending','email_received','receiving') THEN subscription_status ELSE ? END,subscription_detail=CASE WHEN subscription_status IN ('unsubscribed','unsubscribe_pending','email_received','receiving') THEN subscription_detail ELSE ? END WHERE id=?",
                       (name, resource.url, feed_url, status, detail, job["source_id"]))
            if content["status"] == "found":
                identity = "auto-generate-v1:" + hashlib.sha256((job["source_id"] + ":" + content["url"]).encode()).hexdigest()
                existing = db.execute("SELECT id FROM jobs WHERE user_id=? AND fingerprint=?", (job["user_id"], identity)).fetchone()
                generation_job_id = existing["id"] if existing else uuid.uuid4().hex
                if not existing:
                    db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'generation')",
                               (generation_job_id, job["user_id"], job["source_id"], identity, "Generate from discovered public issue",
                                json.dumps({"url": content["url"], "mode": "auto"}), timestamp()))
            db.execute("UPDATE jobs SET status='completed',stage='completed',result=?,payload=NULL WHERE id=?",
                       (json.dumps({"subscription_status": status, "detail": detail, "content_discovery": content,
                                    "generation_job_id": generation_job_id}), job["id"]))
        if generation_job_id:
            self.process(generation_job_id)

    def save_document(self, job, document):
        with self.store.connect() as db:
            # Retrying a failed job reuses source evidence instead of duplicating it.
            existing = db.execute("SELECT id FROM documents WHERE job_id=? AND url=? AND content_hash=?",
                                  (job["id"], document.url, document.content_hash)).fetchone()
            if not existing:
                db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)",
                           (uuid.uuid4().hex, job["user_id"], job["id"], document.url, document.title,
                            document.content_hash, json.dumps(document.spans), json.dumps(document.metadata), timestamp()))

    def original_document(self, job, document):
        urls = primary_source_urls(document)
        if not urls:
            return document
        self.stage(job["id"], "fetching_original_content")
        errors = []
        for url in urls:
            try:
                original = parse_document(self.fetcher.fetch(url))
                self.save_document(job, original)
                return original
            except SourceError as exc:
                errors.append(str(exc))
        raise SourceError("The linked original source could not be read; newsletter text was not substituted. " + errors[0])

    def publish_ready(self, db, post_id):
        """Automatic private Home placement is not human/public approval."""
        post = db.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        if post["status"] != "review" or post["rights"] != "private":
            return False
        if post["generation"] != "extractive" and (post["verification"] != "machine_checked" or not post["generation"].startswith("ollama:")):
            return False
        user = db.execute("SELECT timezone FROM users WHERE id=?", (post["user_id"],)).fetchone()
        analysis = json.loads(post["analysis"])
        analysis["publication"] = {"mode": "automatic_private", "human_reviewed": False}
        db.execute("UPDATE posts SET status='approved',rights='private',published_date=COALESCE(published_date,?),analysis=? WHERE id=?",
                   (datetime.now(ZoneInfo(user["timezone"])).date().isoformat(), json.dumps(analysis), post_id))
        if not db.execute("SELECT 1 FROM posts WHERE job_id=? AND status='review'", (post["job_id"],)).fetchone():
            db.execute("UPDATE jobs SET status='completed',stage='completed' WHERE id=?", (post["job_id"],))
        return True

    def render_visual(self, job, payload):
        with self.store.connect() as db:
            post = db.execute("SELECT * FROM posts WHERE id=? AND user_id=?", (payload["post_id"], job["user_id"])).fetchone()
        if not post:
            raise SourceError("Post no longer exists")
        self.stage(job["id"], "fetching_source_visuals")
        document = parse_document(self.fetcher.fetch(post["original_url"]))
        draft = dict(post)
        draft["analysis"] = json.loads(post["analysis"])
        draft["evidence"] = json.loads(post["evidence"])
        visual, image = choose_visual(document, draft, self.fetcher, self.illustrator,
                                      stage=lambda stage: self.stage(job["id"], stage))
        with self.store.connect() as db:
            if image:
                visual["asset_id"] = uuid.uuid4().hex
                db.execute("INSERT INTO post_assets VALUES(?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET id=excluded.id,image=excluded.image,content_type=excluded.content_type",
                           (visual["asset_id"], post["id"], image, "image/png"))
            db.execute("UPDATE posts SET visual=? WHERE id=?", (json.dumps(visual), post["id"]))
            self.publish_ready(db, post["id"])
            db.execute("UPDATE jobs SET status=?,stage=?,error=?,result=?,payload=CASE WHEN ? THEN NULL ELSE payload END WHERE id=?",
                       ("completed" if image else "failed", "completed" if image else "needs_image_generator",
                        None if image else visual["detail"], json.dumps({"visual_status": visual["status"]}), bool(image), job["id"]))

    def generate_posts(self, job, payload):
        self.stage(job["id"], "fetching_source")
        if job["kind"] == "received_email":
            # A new input adapter, using the same original-source selection,
            # local model, verifier and source-first image handling as URL jobs.
            # The synthetic edition ID is never fetched or used as a post link.
            root = Document("email:" + payload["fingerprint"], payload["subject"],
                            make_spans([(payload["text"], "Newsletter", None)]),
                            [{"url": url, "title": url} for url in payload["urls"]],
                            {"format": "email"})
        else:
            root = parse_document(self.fetcher.fetch(payload["url"]))
        self.save_document(job, root)
        mode = "digest" if job["kind"] == "received_email" else (issue_mode(root) if payload.get("mode") == "auto" else payload.get("mode"))
        if mode == "digest":
            candidates = digest_links(root, limit=3)
            if not candidates:
                raise SourceError("No article links found in this edition; use Article mode for a single source")
        else:
            candidates = [{"url": root.url, "title": root.title}]
        results = []
        for candidate in candidates:
            url = candidate["url"]
            with self.store.connect() as db:
                existing = db.execute("SELECT id FROM posts WHERE job_id=? AND original_url=?", (job["id"], url)).fetchone()
            if existing:
                results.append({"url": url, "status": "already_generated", "post_id": existing["id"]})
                continue
            try:
                self.stage(job["id"], "extracting_original")
                document = root if url == root.url else parse_document(self.fetcher.fetch(url))
                self.save_document(job, document)
                secondary = document
                document = self.original_document(job, document)
                # Suppress exact source revisions already processed for this account.
                with self.store.connect() as db:
                    duplicate = db.execute("""SELECT p.id FROM posts p JOIN documents d ON d.job_id=p.job_id
                        WHERE p.user_id=? AND p.original_url=? AND d.url=? AND d.content_hash=? AND p.status!='rejected'""",
                        (job["user_id"], document.url, document.url, document.content_hash)).fetchone()
                if duplicate and duplicate["id"] != payload.get("replace_post_id"):
                    results.append({"url": document.url, "status": "duplicate", "post_id": duplicate["id"]})
                    continue
                draft = self.generator(document, stage=lambda stage: self.stage(job["id"], stage))
                draft["analysis"]["provenance"] = {"newsletter_issue_url": "" if job["kind"] == "received_email" else root.url, "secondary_url": secondary.url,
                                                    "original_url": document.url, "content_author": document.metadata.get("author", "")}
                visual, image = choose_visual(document, draft, self.fetcher, self.illustrator,
                                              stage=lambda stage: self.stage(job["id"], stage))
                post_id = uuid.uuid4().hex
                if image:
                    visual["asset_id"] = uuid.uuid4().hex
                with self.store.connect() as db:
                    if root.metadata.get("publisher"):
                        db.execute("UPDATE sources SET name=? WHERE id=? AND user_id=? AND sender LIKE 'newsletter:%'",
                                   (root.metadata["publisher"], job["source_id"], job["user_id"]))
                    replacement = payload.get("replace_post_id")
                    if replacement:
                        old = db.execute("SELECT * FROM posts WHERE id=? AND user_id=?", (replacement, job["user_id"])).fetchone()
                        if not old or old["rights"] != "private" or old["status"] == "rejected":
                            raise SourceError("Only active private posts can be regenerated")
                        if draft["verification"] != "machine_checked" or not image:
                            raise SourceError("Replacement is not ready; the previous post was retained")
                        post_id = replacement
                        db.execute("""UPDATE posts SET job_id=?,title=?,hook=?,finding=?,limitations=?,original_url=?,evidence=?,generation=?,analysis=?,verification=?,visual=?,status='review'
                                      WHERE id=? AND user_id=?""",
                                   (job["id"], draft["title"], draft["hook"], draft["finding"], draft["limitations"], document.url,
                                    json.dumps(draft["evidence"]), draft["generation"], json.dumps(draft["analysis"]), draft["verification"], json.dumps(visual), post_id, job["user_id"]))
                        if old["job_id"] != job["id"] and not db.execute("SELECT 1 FROM posts WHERE job_id=? AND status='review'", (old["job_id"],)).fetchone():
                            db.execute("UPDATE jobs SET status='completed',stage='completed' WHERE id=?", (old["job_id"],))
                    else:
                        self.insert_post(db, post_id, job, document, draft, visual)
                    if image:
                        db.execute("INSERT INTO post_assets VALUES(?,?,?,?) ON CONFLICT(post_id) DO UPDATE SET id=excluded.id,image=excluded.image,content_type=excluded.content_type",
                                   (visual["asset_id"], post_id, image, "image/png"))
                    published = self.publish_ready(db, post_id)
                results.append({"url": document.url, "status": "published" if published else "review", "post_id": post_id})
            except SourceError as exc:
                # One unavailable paper must not discard the rest of a digest.
                results.append({"url": url, "status": "failed", "error": str(exc)})
        created = any(r["status"] in ("review", "published", "already_generated") for r in results)
        succeeded = created or any(r["status"] == "duplicate" for r in results)
        with self.store.connect() as db:
            pending = db.execute("SELECT 1 FROM posts WHERE job_id=? AND status='review'", (job["id"],)).fetchone()
            state = "review" if created and pending else ("completed" if succeeded else "failed")
            error = None if succeeded else (results[0].get("error", "No eligible posts generated") if results else "No items found")
            db.execute("UPDATE jobs SET status=?,stage=?,error=?,result=?,payload=CASE WHEN ?='failed' THEN payload ELSE NULL END WHERE id=?",
                       (state, state, error, json.dumps({"items": results}), state, job["id"]))

    def insert_post(self, db, post_id, job, document, draft, visual):
        source = db.execute("SELECT subscription_status FROM sources WHERE id=? AND user_id=?", (job["source_id"], job["user_id"])).fetchone()
        if not source or source["subscription_status"] in ("unsubscribed", "unsubscribe_pending", "unsubscribing"):
            raise SourceError("Newsletter was unsubscribed; no new post was published")
        db.execute("""INSERT INTO posts
            (id,user_id,source_id,job_id,title,hook,finding,limitations,original_url,evidence,generation,analysis,verification,visual,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (post_id, job["user_id"], job["source_id"], job["id"], draft["title"], draft["hook"],
             draft["finding"], draft["limitations"], document.url, json.dumps(draft["evidence"]),
             draft["generation"], json.dumps(draft["analysis"]), draft["verification"], json.dumps(visual), timestamp()))
