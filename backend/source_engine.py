"""Durable source-to-post and newsletter discovery jobs."""
import json
import os
import hashlib
import re
import uuid
import copy
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .documents import Document, digest_links, make_spans, parse_document, primary_source_urls, publication_name
from .discovery import discover_issue, issue_mode
from .generation import generation_model, engine_generation
from .editorial import generate_post
from .artifacts import Artifacts, GenerationPending
from .budgets import JobBudget
from .email_content import email_documents, use_email_body
from .email_adaptation import preserve_short_email
from .retrieval import PublicFetcher, SourceError
from .visuals import choose_visual, source_gallery
from .assets import AssetStore


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class SourceEngine:
    def __init__(self, store, fetcher=None, generator=None, subscription_agent=None, illustrator=None):
        self.store = store
        self.assets = AssetStore(store)
        self.fetcher = fetcher or PublicFetcher()
        self.generator = generator or generate_post
        self.artifacts = Artifacts(store)
        self.custom_generator = generator is not None
        # Newsletter-app integrations plug into this separate signup boundary.
        # An agent returns a truthful state, never an assumed subscription success.
        self.subscription_agent = subscription_agent
        self.illustrator = illustrator
        self.photo_searcher = None

    def photo_lookup(self,job):
        if self.custom_generator or os.environ.get('GENERATION_PROVIDER','meta')!='meta' or not os.environ.get('MODEL_API_KEY'):
            return None
        if self.photo_searcher is None:
            from .venue_photo_search import VenuePhotoSearch
            self.photo_searcher=VenuePhotoSearch(self.store)
        return lambda places:self.photo_searcher.pages(places,JobBudget(self.store,job['id']))

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
        except GenerationPending:
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='queued',stage='waiting_for_shared_generation' WHERE id=?", (job["id"],))
        except SourceError as exc:
            self.fail(job, str(exc))
        except Exception:
            self.fail(job, "Source processing failed; inspect the source or retry the job")

    def fail(self, job, message):
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='failed',stage='failed',error=? WHERE id=?", (message, job["id"]))
            if job["kind"] == "visual":
                try:
                    post_id = json.loads(job["payload"] or "{}").get("post_id")
                except (ValueError, TypeError):
                    post_id = None
                post = db.execute("SELECT visual FROM posts WHERE id=? AND user_id=?", (post_id, job["user_id"])).fetchone()
                if post:
                    visual = json.loads(post["visual"])
                    if not visual.get("asset_id"):
                        visual.update(status="failed", detail=message)
                        db.execute("UPDATE posts SET visual=? WHERE id=? AND user_id=?", (json.dumps(visual), post_id, job["user_id"]))
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
        if generation_job_id and os.environ.get('UPTODATE_ENV') != 'cloud':
            self.process(generation_job_id)

    def save_document(self, job, document):
        metadata = copy.deepcopy(document.metadata)
        metadata['_links'] = document.links
        metadata['_figures'] = [{key: value for key, value in figure.items() if not isinstance(value, bytes)}
                                for figure in document.figures if not figure.get('image_data') and not figure.get('svg_data')]
        with self.store.connect() as db:
            # Retrying a failed job reuses source evidence instead of duplicating it.
            existing = db.execute("SELECT id FROM documents WHERE job_id=? AND url=? AND content_hash=?",
                                  (job["id"], document.url, document.content_hash)).fetchone()
            if not existing:
                db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)",
                           (uuid.uuid4().hex, job["user_id"], job["id"], document.url, document.title,
                            document.content_hash, json.dumps(document.spans), json.dumps(metadata), timestamp()))
            else:
                db.execute('UPDATE documents SET metadata=? WHERE id=?', (json.dumps(metadata), existing['id']))

    def saved_visual_document(self, post, user_id):
        with self.store.connect() as db:
            saved = db.execute('SELECT * FROM documents WHERE job_id=? AND user_id=? AND url=? ORDER BY created_at DESC LIMIT 1',
                               (post['job_id'], user_id, post['original_url'])).fetchone()
        if not saved:
            return None
        metadata = json.loads(saved['metadata'])
        metadata['source_content_hash'] = saved['content_hash']
        return Document(saved['url'], saved['title'], json.loads(saved['spans']),
                        metadata.get('_links', []), metadata, metadata.get('_figures', []))

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
        preserved = post['generation']=='source:preserved-email' and post['verification']=='source_preserved'
        if not preserved and post["generation"] != "extractive" and (post["verification"] != "machine_checked" or not engine_generation(post["generation"])):
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
            if post:
                current_visual = json.loads(post["visual"])
                if not current_visual.get("asset_id"):
                    current_visual.update(status="needs_image_generator", detail="Preparing an image for this post")
                    db.execute("UPDATE posts SET visual=? WHERE id=?", (json.dumps(current_visual), post["id"]))
        if not post:
            raise SourceError("Post no longer exists")
        self.stage(job["id"], "fetching_source_visuals")
        if post["original_url"].startswith('email:'):
            document = self.saved_visual_document(post, job['user_id'])
            if not document:raise SourceError('Original email content is unavailable')
            if document.metadata.get('email_images_pending'):raise SourceError('This email includes an embedded source image; attachment import is not available yet')
        else:
            try:
                document = parse_document(self.fetcher.fetch(post["original_url"]))
                self.save_document({'id': post['job_id'], 'user_id': job['user_id']}, document)
            except SourceError:
                document = self.saved_visual_document(post, job['user_id'])
                if not document:
                    raise
                self.stage(job['id'], 'using_saved_source_visuals')
        draft = dict(post)
        draft["analysis"] = json.loads(post["analysis"])
        draft["evidence"] = json.loads(post["evidence"])
        original_gallery = source_gallery(document,draft,self.fetcher,stage=lambda value:self.stage(job['id'],value),photo_search=None if payload.get('source_only') else self.photo_lookup(job))
        if original_gallery:
            visual,image=original_gallery[0]
        elif payload.get('source_only'):
            raise SourceError('No usable source image could be retrieved; illustration was not requested')
        elif post["artifact_key"] and not self.custom_generator:
            with self.store.connect() as db:
                parent = db.execute("SELECT content_hash,scope FROM generation_artifacts WHERE cache_key=?", (post["artifact_key"],)).fetchone()
            if not parent or document.content_hash != parent["content_hash"]:
                raise SourceError("The source revision changed; regenerate the post before replacing its visual")
            document.metadata["private"] = parent["scope"] != "public"
            def produce_visual():
                planner = generation_model()
                if hasattr(planner, "budget"):
                    planner.budget = JobBudget(self.store, job["id"])
                visual, image = choose_visual(document, draft, self.fetcher, self.illustrator,
                                              stage=lambda value: self.stage(job["id"], value), text_model=planner,
                                              budget=JobBudget(self.store, job["id"]),photo_search=self.photo_lookup(job))
                if not image:
                    raise SourceError(visual["detail"])
                visual["asset_id"] = hashlib.sha256((post["artifact_key"] + hashlib.sha256(image).hexdigest()).encode()).hexdigest()
                with self.store.connect() as db:
                    self.assets.save_gallery(db, visual["asset_id"], post["artifact_key"], image)
                return visual
            image_variant = "muse-image-1.0-low-v1" if os.environ.get("IMAGE_GENERATION_PROVIDER", "meta") == "meta" else "comfyui"
            _, visual, reused = self.artifacts.obtain(document, job["user_id"], "visual:" + image_variant + ":" + post["artifact_key"], produce_visual)
            with self.store.connect() as db:
                stored = db.execute("SELECT * FROM gallery_assets WHERE id=? AND artifact_key=?", (visual["asset_id"], post["artifact_key"])).fetchone()
            if not stored:
                raise SourceError("The visual is no longer available")
            image = self.assets.read(stored)
        else:
            visual, image = choose_visual(document, draft, self.fetcher, self.illustrator,
                                          stage=lambda value: self.stage(job["id"], value), budget=JobBudget(self.store, job["id"]),photo_search=self.photo_lookup(job))
        with self.store.connect() as db:
            if image:
                visual.setdefault("asset_id", uuid.uuid4().hex)
                if post["artifact_key"]:
                    self.assets.save_gallery(db, visual["asset_id"], post["artifact_key"], image)
                self.assets.save_post(db, hashlib.sha256((post["id"] + visual["asset_id"]).encode()).hexdigest(), post["id"], image)
                draft["analysis"]["visuals"] = [visual]
            else:
                visual["status"] = "failed"
            db.execute("UPDATE posts SET visual=?,analysis=? WHERE id=?", (json.dumps(visual), json.dumps(draft["analysis"]), post["id"]))
            self.publish_ready(db, post["id"])
            db.execute("UPDATE jobs SET status=?,stage=?,error=?,result=?,payload=CASE WHEN ? THEN NULL ELSE payload END WHERE id=?",
                       ("completed" if image else "failed", "completed" if image else "needs_image_generator",
                        None if image else visual["detail"], json.dumps({"visual_status": visual["status"]}), bool(image), job["id"]))

    def generate_posts(self, job, payload):
        self.stage(job["id"], "fetching_source")
        inline_documents={}
        if job["kind"] == "received_email":
            # A new input adapter, using the same original-source selection,
            # local model, verifier and source-first image handling as URL jobs.
            # The synthetic edition ID is never fetched or used as a post link.
            root, originals = email_documents(payload)
            if use_email_body(root):inline_documents={document.url:document for document in originals}
        else:
            root = parse_document(self.fetcher.fetch(payload["url"]))
        self.save_document(job, root)
        mode = "digest" if job["kind"] == "received_email" else (issue_mode(root) if payload.get("mode") == "auto" else payload.get("mode"))
        if inline_documents:
            candidates=[{'url':doc.url,'title':doc.title} for doc in inline_documents.values()]
        elif mode == "digest":
            candidates = digest_links(root, limit=None)
            if not candidates:
                raise SourceError("No article links found in this edition; use Article mode for a single source")
        else:
            candidates = [{"url": root.url, "title": root.title}]
        if payload.get('target_url'):
            candidates=[candidate for candidate in candidates if candidate['url']==payload['target_url']]
            if not candidates:raise SourceError('The original email section could not be reconstructed; no other story was substituted')
        candidates = payload.get("remaining_candidates", candidates)
        batch, remaining = candidates[:4], candidates[4:]
        results = []
        for candidate in batch:
            url = candidate["url"]
            with self.store.connect() as db:
                existing = db.execute("SELECT id FROM posts WHERE job_id=? AND original_url=?", (job["id"], url)).fetchone()
            if existing and existing['id'] != payload.get('replace_post_id'):
                results.append({"url": url, "status": "already_generated", "post_id": existing["id"]})
                continue
            try:
                self.stage(job["id"], "extracting_original")
                document = inline_documents.get(url) or (root if url == root.url else parse_document(self.fetcher.fetch(url)))
                self.save_document(job, document)
                secondary = document
                document = self.original_document(job, document)
                parts = urlsplit(document.url)
                query = parse_qsl(parts.query, keep_blank_values=True)
                cleaned = [(key, value) for key, value in query if not key.lower().startswith("utm_") and key.lower() not in ("ref", "source")]
                if len(cleaned) != len(query):
                    canonical = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(cleaned), ""))
                    try:
                        candidate_document = parse_document(self.fetcher.fetch(canonical))
                        # Only share a clean anonymous URL after verifying that
                        # it yields the same source revision. Never cache a
                        # subscriber's tracking URL into somebody else's post.
                        if candidate_document.content_hash == document.content_hash:
                            document = candidate_document
                            self.save_document(job, document)
                    except SourceError:
                        pass
                # Suppress exact source revisions already processed for this account.
                with self.store.connect() as db:
                    duplicate = db.execute("""SELECT p.id FROM posts p JOIN documents d ON d.job_id=p.job_id
                        WHERE p.user_id=? AND p.original_url=? AND d.url=? AND d.content_hash=? AND p.status!='rejected'""",
                        (job["user_id"], document.url, document.url, document.content_hash)).fetchone()
                if duplicate and duplicate["id"] != payload.get("replace_post_id"):
                    with self.store.connect() as db:
                        db.execute("INSERT OR IGNORE INTO post_sources(post_id,source_id,user_id) VALUES(?,?,?)", (duplicate["id"], job["source_id"], job["user_id"]))
                    results.append({"url": document.url, "status": "duplicate", "post_id": duplicate["id"]})
                    continue
                preserved = None if self.custom_generator else preserve_short_email(document)
                if preserved:
                    variant = 'source:preserved-email-v2-locations'
                elif self.custom_generator:
                    variant = "custom:" + getattr(self.generator, "__qualname__", type(self.generator).__name__)
                else:
                    configured = generation_model()
                    if hasattr(configured, "budget"):
                        configured.budget = JobBudget(self.store, job["id"])
                    variant = configured.provider + ":" + configured.model + ":" + getattr(configured, "reasoning", "default")
                def produce_draft():
                    if preserved:return preserved
                    if self.custom_generator:return self.generator(document,stage=lambda stage:self.stage(job['id'],stage))
                    return self.generator(document,model=configured,stage=lambda stage:self.stage(job['id'],stage))
                artifact_key, cached, reused = self.artifacts.obtain(document, job["user_id"], variant,produce_draft)
                draft = copy.deepcopy(cached)
                draft["analysis"]["cache"] = {"reused": reused, "new_inference_cost_usd": 0 if reused else draft["analysis"].get("usage", {}).get("estimated_cost_usd", 0)}
                draft["analysis"]["provenance"] = {"newsletter_issue_url": "" if job["kind"] == "received_email" else root.url, "secondary_url": secondary.url,
                                                    "original_url": document.url, "content_author": document.metadata.get("author", "")}
                gallery = source_gallery(document, draft, self.fetcher, stage=lambda value: self.stage(job["id"], value),photo_search=self.photo_lookup(job))
                if gallery:
                    visual, image = gallery[0]
                elif document.metadata.get('email_images_pending'):
                    visual,image={'kind':'source','status':'failed','detail':'This email contains an embedded source image; attachment import is not available yet'},None
                elif self.illustrator is not None:
                    visual, image = choose_visual(document, draft, self.fetcher, self.illustrator,
                                                  stage=lambda value: self.stage(job["id"], value), budget=JobBudget(self.store, job["id"]),photo_search=self.photo_lookup(job))
                    gallery = [(visual, image)] if image else []
                else:
                    visual, image = {"kind": "pending", "status": "needs_image_generator", "caption": "Illustration pending",
                                     "detail": "No usable source image could be retrieved; fallback illustration is queued separately"}, None
                post_id = uuid.uuid4().hex
                visuals = []
                for metadata, data in gallery:
                    asset_id = hashlib.sha256((artifact_key + hashlib.sha256(data).hexdigest()).encode()).hexdigest()
                    metadata["asset_id"] = asset_id
                    visuals.append(metadata)
                draft["analysis"]["visuals"] = visuals
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    existing_shared = db.execute("SELECT id FROM posts WHERE user_id=? AND artifact_key=? AND rights='private' AND status!='rejected'", (job["user_id"], artifact_key)).fetchone()
                    if existing_shared and existing_shared["id"] != payload.get("replace_post_id"):
                        db.execute("INSERT OR IGNORE INTO post_sources(post_id,source_id,user_id) VALUES(?,?,?)", (existing_shared["id"], job["source_id"], job["user_id"]))
                        results.append({"url": document.url, "status": "duplicate", "post_id": existing_shared["id"]})
                        continue
                    if root.metadata.get("publisher"):
                        db.execute("UPDATE sources SET name=? WHERE id=? AND user_id=? AND sender LIKE 'newsletter:%'",
                                   (root.metadata["publisher"], job["source_id"], job["user_id"]))
                    replacement = payload.get("replace_post_id")
                    if replacement:
                        old = db.execute("SELECT * FROM posts WHERE id=? AND user_id=?", (replacement, job["user_id"])).fetchone()
                        if not old or old["rights"] != "private" or old["status"] == "rejected":
                            raise SourceError("Only active private posts can be regenerated")
                        if draft["verification"] not in ("machine_checked", "source_preserved") or not image:
                            raise SourceError("Replacement is not ready; the previous post was retained")
                        post_id = replacement
                        db.execute("""UPDATE posts SET job_id=?,title=?,hook=?,finding=?,limitations=?,original_url=?,evidence=?,generation=?,analysis=?,verification=?,visual=?,artifact_key=?,status='review'
                                      WHERE id=? AND user_id=?""",
                                   (job["id"], draft["title"], draft["hook"], draft["finding"], draft["limitations"], document.url,
                                    json.dumps(draft["evidence"]), draft["generation"], json.dumps(draft["analysis"]), draft["verification"], json.dumps(visual), artifact_key, post_id, job["user_id"]))
                        if old["job_id"] != job["id"] and not db.execute("SELECT 1 FROM posts WHERE job_id=? AND status='review'", (old["job_id"],)).fetchone():
                            db.execute("UPDATE jobs SET status='completed',stage='completed' WHERE id=?", (old["job_id"],))
                    else:
                        self.insert_post(db, post_id, job, document, draft, visual)
                        db.execute("UPDATE posts SET artifact_key=? WHERE id=?", (artifact_key, post_id))
                    db.execute("INSERT OR IGNORE INTO post_sources(post_id,source_id,user_id) VALUES(?,?,?)", (post_id, job["source_id"], job["user_id"]))
                    for metadata, data in gallery:
                        self.assets.save_gallery(db, metadata["asset_id"], artifact_key, data)
                    if image:
                        self.assets.save_post(db, hashlib.sha256((post_id + visual["asset_id"]).encode()).hexdigest(), post_id, image)
                    published = self.publish_ready(db, post_id)
                    if image is None and not payload.get("replace_post_id") and not self.custom_generator and not document.metadata.get('email_images_pending'):
                        fingerprint = "visual:" + post_id
                        db.execute("INSERT OR IGNORE INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'visual')",
                                   (uuid.uuid4().hex, job["user_id"], job["source_id"], fingerprint, "Prepare post visual", json.dumps({"post_id": post_id}), timestamp()))
                results.append({"url": document.url, "status": "published" if published else "review", "post_id": post_id})
            except GenerationPending as exc:
                results.append({"url": url, "status": "waiting", "detail": str(exc)})
                remaining.append(candidate)
            except SourceError as exc:
                # One unavailable paper must not discard the rest of a digest.
                results.append({"url": url, "status": "failed", "error": str(exc), "usage": getattr(exc, "usage", {})})
        created = any(r["status"] in ("review", "published", "already_generated") for r in results)
        succeeded = created or any(r["status"] == "duplicate" for r in results)
        with self.store.connect() as db:
            previous_results = payload.get("processed_items", [])
            completed_results = previous_results + [item for item in results if item["status"] != "waiting"]
            if remaining:
                payload = {**payload, "remaining_candidates": remaining, "processed_items": completed_results}
                db.execute("UPDATE jobs SET status='queued',stage='waiting_for_shared_generation',payload=?,result=? WHERE id=?",
                           (json.dumps(payload), json.dumps({"items": completed_results, "remaining_stories": len(remaining), "link_limit_reached": bool(payload.get("link_limit_reached"))}), job["id"]))
                return
            if previous_results:
                results = completed_results
                created = any(r["status"] in ("review", "published", "already_generated") for r in results)
                succeeded = created or any(r["status"] == "duplicate" for r in results)
            pending = db.execute("SELECT 1 FROM posts WHERE job_id=? AND status='review'", (job["id"],)).fetchone()
            state = "review" if created and pending else ("completed" if succeeded else "failed")
            error = None if succeeded else (results[0].get("error", "No eligible posts generated") if results else "No items found")
            if succeeded and any(item["status"] == "failed" for item in results):
                state = "failed"
                error = "Some stories could not be processed; completed posts were retained. Retry to finish the remaining stories."
            if state == "failed":
                payload = {**payload,
                           "remaining_candidates": [{"url": item["url"], "title": item["url"]} for item in results if item["status"] == "failed"],
                           "processed_items": [item for item in results if item["status"] != "failed"]}
                db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), job["id"]))
            db.execute("UPDATE jobs SET status=?,stage=?,error=?,result=?,payload=CASE WHEN ?='failed' THEN payload ELSE NULL END WHERE id=?",
                       (state, state, error, json.dumps({"items": results, "link_limit_reached": bool(payload.get("link_limit_reached"))}), state, job["id"]))

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
