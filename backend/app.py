import hashlib
import asyncio
from contextlib import suppress
import html
import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .demo import DEMO_LIMITS, DEMO_POSTS, DEMO_PLACES, DEMO_SENDER, DEMO_SIGNUP, DEMO_SOURCE_NAME, DEMO_SUBJECT
from .pipeline import make_drafts, parse_newsletter, safe_url
from .store import Store
from .generation import generation_model, engine_generation
from .retrieval import SourceError, public_url
from .source_engine import SourceEngine
from .auth import SupabaseAuth
from .receiving import ReceivingError, TemporaryRelay
from .signup import FormSignup
from .subscriptions import Subscriptions
from .post_chat import ChatInput, answer_post
from .budgets import JobBudget
from .source_identity import newsletter_identity


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


class AccountInput(BaseModel):
    timezone: str = "UTC"


class ProfilePatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)


class NewsletterInput(BaseModel):
    url: str = Field(min_length=8, max_length=3000)
    auto_subscribe: bool = True


class GenerationInput(BaseModel):
    url: str = Field(min_length=8, max_length=3000)
    source_id: str | None = None
    mode: Literal["article", "digest"] = "article"
    refresh: bool = False


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    muted: bool | None = None
    signup_url: str | None = Field(default=None, max_length=2000)


class Approval(BaseModel):
    reviewed: bool
    rights: Literal["private", "public"] = "private"


class EmailNewsletterInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class VisualRetryInput(BaseModel):
    source_only: bool = False


def create_app(db_path: str | None = None, source_fetcher=None, generator=None, subscription_agent=None, illustrator=None, auth_provider=None, test_relay=None, receiving_poll=True, processing_poll=True):
    store = Store(db_path or os.environ.get("NEWSLETTER_DB", str(Path(__file__).resolve().parents[1] / "data/prototype.sqlite3")))
    engine = SourceEngine(store, source_fetcher, generator, subscription_agent, illustrator)
    if subscription_agent is None and os.environ.get("NEWSLETTER_SIGNUP_MODE", "") in ("form", "browser"):
        from .browser_signup import BrowserSignup
        engine.subscription_agent = FormSignup(engine.fetcher, browser=BrowserSignup() if os.environ.get("NEWSLETTER_SIGNUP_MODE") == "browser" else None)
    auth = auth_provider if auth_provider is not None else SupabaseAuth.from_environment()
    relay = test_relay if test_relay is not None else TemporaryRelay.from_environment()
    subscriptions = Subscriptions(store, engine.fetcher)
    if relay and (not auth or auth.relay_aliases):
        raise ValueError("Temporary receiving requires verified accounts with permanent relay activation disabled")

    def receive_mail():
        try:
            result = relay.sync(store)
        except ReceivingError:
            return
        if result and result.get("received"):
            with store.connect() as db:
                pending_unsubscribe = [row["id"] for row in db.execute("SELECT s.id FROM sources s JOIN source_mail_actions a ON a.source_id=s.id WHERE s.user_id=? AND s.subscription_status='unsubscribe_pending' AND a.one_click=1", (relay.owner_id,))]
            for source_id in pending_unsubscribe:
                subscriptions.unsubscribe(relay.owner_id, source_id)
        with store.connect() as db:
            active = db.execute("SELECT 1 FROM test_relay_aliases WHERE user_id=? AND status='active'", (relay.owner_id,)).fetchone()
            if active:
                for source in db.execute("SELECT * FROM sources WHERE user_id=? AND subscription_status='needs_inbox'", (relay.owner_id,)).fetchall():
                    fingerprint = "relay-ready-signup:" + source["id"]
                    previous = db.execute("SELECT 1 FROM jobs WHERE user_id=? AND fingerprint=?", (relay.owner_id, fingerprint)).fetchone()
                    if not previous:
                        db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'newsletter')",
                                   (uid(), relay.owner_id, source["id"], fingerprint, "Resume signup with ready relay",
                                    json.dumps({"url": source["signup_url"], "auto_subscribe": True}), now()))
                        db.execute("UPDATE sources SET subscription_status='queued' WHERE id=?", (source["id"],))
            jobs = [row["id"] for row in db.execute("SELECT id FROM jobs WHERE user_id=? AND kind='received_email' AND status='queued'", (relay.owner_id,))]
            jobs += [row["id"] for row in db.execute("SELECT id FROM jobs WHERE user_id=? AND kind='newsletter' AND fingerprint LIKE 'relay-ready-signup:%' AND status='queued'", (relay.owner_id,))]
        for job_id in jobs:
            process_job(job_id)
        return result

    async def poll_mail():
        while True:
            await asyncio.to_thread(receive_mail)
            await asyncio.sleep(30)

    async def poll_pending_work():
        while True:
            with store.connect() as db:
                pending = [row["id"] for row in db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 20")]
            for job_id in pending:
                await asyncio.to_thread(process_job, job_id)
            await asyncio.sleep(2)

    def process_job(job_id):
        with store.connect() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["status"] != "queued":
                return
            if job["kind"] != "email":
                # The source engine owns claiming and processing these job types.
                source_job = True
            else:
                source_job = False
            if source_job:
                claimed = False
            else:
                claimed = db.execute("UPDATE jobs SET status='processing' WHERE id=? AND status='queued'", (job_id,)).rowcount
        if source_job:
            engine.process(job_id)
            return
        if not claimed:
            return
        try:
            drafts = make_drafts(json.loads(job["payload"]))
            with store.connect() as db:
                for draft in drafts:
                    post_id = uid()
                    db.execute("""INSERT INTO posts
                        (id,user_id,source_id,job_id,title,hook,finding,limitations,original_url,evidence,created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (post_id, job["user_id"], job["source_id"], job_id, draft["title"],
                         draft["hook"], draft["finding"], draft["limitations"], draft["original_url"],
                         json.dumps(draft["evidence"]), now()))
                    engine.publish_ready(db, post_id)
                db.execute("UPDATE jobs SET status='completed',stage='completed',payload=NULL WHERE id=?", (job_id,))
        except Exception:
            with store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error='Extraction failed; re-import the edition',payload=NULL WHERE id=?", (job_id,))

    @asynccontextmanager
    async def lifespan(app):
        # One local process only. Recover interrupted jobs after restart.
        with store.connect() as db:
            # The user opted into an automatic PRIVATE feed. Preserve rejected
            # posts and public permission gates; promote prior valid local drafts.
            for post in db.execute("SELECT id FROM posts WHERE status='review' AND rights='private'").fetchall():
                engine.publish_ready(db, post["id"])
            db.execute("UPDATE jobs SET status='failed',error='Answer interrupted; ask again from the post' WHERE status='processing' AND kind='ask_ai'")
            db.execute("UPDATE jobs SET status='queued' WHERE status='processing' AND kind!='ask_ai'")
            db.execute("UPDATE generation_artifacts SET lease_until=0 WHERE status='processing'")
            # Cheap offline imports can recover immediately; model/network work
            # resumes in the worker so it cannot block API startup.
            pending = [r["id"] for r in db.execute("SELECT id FROM jobs WHERE status='queued' AND kind='email' LIMIT 20")]
        for job_id in pending:
            process_job(job_id)
        poller = asyncio.create_task(poll_mail()) if relay and relay.reader and receiving_poll else None
        worker = asyncio.create_task(poll_pending_work()) if processing_poll else None
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            if poller:
                poller.cancel()
                with suppress(asyncio.CancelledError):
                    await poller

    app = FastAPI(title="UpToDate prototype", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.engine = engine
    app.state.test_relay = relay
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver", "[::1]"])
    bearer = HTTPBearer(auto_error=False)

    def remember_verified_user(profile):
        # Local content is keyed by verified UUID; the main email is never used
        # as a subscription address. Pending/disabled relays cannot reach agents.
        with store.connect() as db:
            placeholder = f'{profile["id"]}@inbox.prototype.invalid'
            db.execute("INSERT OR IGNORE INTO users(id,token_hash,alias,created_at,timezone) VALUES(?,?,?,?,?)",
                       (profile["id"], "external:" + profile["id"], placeholder, now(), profile["timezone"]))
            if relay:
                try:
                    profile = relay.profile(db, profile)
                except ValueError:
                    raise HTTPException(409, "Temporary relay configuration conflicts with an existing address") from None
            alias = (profile.get("alias") if profile.get("alias_status") == "active" else None) or placeholder
            db.execute("UPDATE users SET alias=?,timezone=? WHERE id=?", (alias, profile["timezone"], profile["id"]))
        return {**profile, "alias": profile.get("alias") or ""}

    def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(401, "A local account token is required")
        if auth:
            profile = auth.identity(credentials.credentials)
            # Stable Supabase UUID owns local content. No email, password, JWT,
            # or refresh token is persisted in the local content database.
            return remember_verified_user(profile)
        token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        with store.connect() as db:
            row = db.execute("SELECT * FROM users WHERE token_hash=?", (token_hash,)).fetchone()
        if not row:
            raise HTTPException(401, "Account token is invalid or deleted")
        return dict(row)

    def account_json(user):
        return {**{key: user[key] for key in ("id", "alias", "timezone")},
                "alias": user.get("alias") or "",
                "email": user.get("email"), "display_name": user.get("display_name"),
                "alias_status": user.get("alias_status", "unconfigured"),
                "alias_scope": user.get("alias_scope", "permanent")}

    def local_day(user):
        return datetime.now(ZoneInfo(user["timezone"])).date().isoformat()

    def get_post(db, user, post_id):
        row = db.execute("SELECT * FROM posts WHERE id=? AND user_id=?", (post_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "Post not found")
        return row

    def post_json(db, row, user):
        source = db.execute("SELECT * FROM sources WHERE id=?", (row["source_id"],)).fetchone()
        analysis, visual = json.loads(row["analysis"]), json.loads(row["visual"])
        paper = analysis.get('source') or {}
        if not paper.get('abstract') and (analysis.get('content_type') == 'paper' or
                urlsplit(row['original_url']).hostname in ('arxiv.org', 'www.arxiv.org') or paper.get('doi')):
            revision = paper.get('revision_hash')
            if not revision and row['artifact_key']:
                artifact = db.execute('SELECT content_hash FROM generation_artifacts WHERE cache_key=?', (row['artifact_key'],)).fetchone()
                revision = artifact['content_hash'] if artifact else None
            query = 'SELECT title,metadata,content_hash FROM documents WHERE user_id=? AND job_id=? AND url=?'
            params = [user['id'], row['job_id'], row['original_url']]
            if revision:
                query += ' AND content_hash=?'
                params.append(revision)
            saved = db.execute(query + ' ORDER BY created_at ASC LIMIT 1', params).fetchone()
            metadata = json.loads(saved['metadata']) if saved else {}
            if metadata.get('abstract'):
                paper = {'url': row['original_url'], 'title': saved['title'], 'authors': metadata.get('author', ''),
                         'doi': metadata.get('doi', ''), **paper, 'abstract': metadata['abstract'],
                         'revision_hash': saved['content_hash']}
                analysis['source'] = paper
        if row["is_demo"] and not analysis.get("action"):
            for sample, places in zip(DEMO_POSTS, DEMO_PLACES):
                if sample["original_url"] == row["original_url"] and places:
                    analysis["action"] = {"kind": "places", "data": places}
        tags = []
        if row["generation"] == "extractive":
            tags.append({"code": "original_analysis_pending", "label": "Original analysis pending", "detail": "Imported excerpt from the newsletter; original-source analysis is not complete."})
        elif row["verification"] in ("machine_checked", "source_preserved"):
            if visual.get("status") != "ready":
                failed = visual.get("status") == "failed"
                tags.append({"code": "image_failed" if failed else "image_pending", "label": "Image unavailable" if failed else "Image pending", "detail": visual.get("detail", "Source visual is still being prepared.")})
            if not analysis.get("provenance"):
                tags.append({"code": "source_update_pending", "label": "Source update pending", "detail": "This earlier post has not yet been regenerated with original-source provenance."})
        return {
            **{k: row[k] for k in ("id", "title", "hook", "finding", "limitations", "original_url", "status", "rights", "generation", "published_date", "verification")},
            "analysis": analysis, "visual": visual, "visuals": analysis.get("visuals", [visual] if visual.get("asset_id") else []),
            "paper": analysis.get("source"), "content_action": analysis.get("action"), "tags": tags,
            "publication": analysis.get("publication", {}),
            "is_demo": bool(row["is_demo"]), "source": dict(source),
            "author": source["name"], "newsletter_name": source["name"], "newsletter_url": source["url"],
            "discovered_via": [dict(item) for item in db.execute("SELECT s.id,s.name,s.url FROM post_sources ps JOIN sources s ON s.id=ps.source_id WHERE ps.post_id=? AND ps.user_id=? AND s.user_id=?", (row["id"], user["id"], user["id"]))],
            "evidence": json.loads(row["evidence"]),
            "bookmarked": bool(db.execute("SELECT 1 FROM bookmarks WHERE user_id=? AND post_id=?", (user["id"], row["id"])).fetchone()),
            "read": bool(db.execute("SELECT 1 FROM reads WHERE user_id=? AND post_id=?", (user["id"], row["id"])).fetchone()),
        }

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "local-prototype", "generation": os.environ.get("GENERATION_PROVIDER", "meta"), "email_import": True}

    @app.get("/v1/auth/config")
    def auth_configuration():
        # Publishable client configuration only. Never expose the admin key.
        return auth.configuration() if auth else {"mode": "local"}

    @app.get("/v1/engine")
    def engine_status(user=Depends(current_user)):
        return {**generation_model().status(), "signup_agent_configured": engine.subscription_agent is not None,
                "image_generation": {"provider": os.environ.get("IMAGE_GENERATION_PROVIDER", "meta"),
                                     "model": "muse-image-1.0" if os.environ.get("IMAGE_GENERATION_PROVIDER", "meta") == "meta" else "local-workflow"},
                "receiving_alias_ready": bool(user["alias"]) and user.get("alias_status") == "active"}

    def validate_source_url(value):
        try:
            return public_url(value)
        except SourceError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/v1/newsletters", status_code=202)
    def add_newsletter(body: NewsletterInput, background: BackgroundTasks, user=Depends(current_user)):
        url = validate_source_url(body.url)
        identity = newsletter_identity(url)
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            source = db.execute("SELECT * FROM sources WHERE user_id=? AND sender=?", (user["id"], identity)).fetchone()
            if source:
                existing = db.execute("SELECT id,status FROM jobs WHERE source_id=? AND kind='newsletter' ORDER BY created_at DESC LIMIT 1", (source["id"],)).fetchone()
                result = {"source_id": source["id"], "job_id": existing["id"] if existing else None,
                          "status": source["subscription_status"], "duplicate": True, "post_generation": "automatic"}
            else:
                source_id, job_id = uid(), uid()
                db.execute("INSERT INTO sources(id,user_id,name,sender,url,signup_url,subscription_status) VALUES(?,?,?,?,?,?,'queued')",
                       (source_id, user["id"], urlsplit(url).hostname, identity, url, url))
                db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'newsletter')",
                       (job_id, user["id"], source_id, identity, "Add newsletter by link", body.model_dump_json(), now()))
        if source:
            if source["subscription_status"] == "unsubscribed" and body.auto_subscribe:
                return {**result, **subscribe_source(source["id"], background, user)}
            return result
        background.add_task(process_job, job_id)
        return {"source_id": source_id, "job_id": job_id, "status": "queued", "duplicate": False, "post_generation": "automatic"}

    @app.post("/v1/me/sources/{source_id}/subscribe", status_code=202)
    def subscribe_source(source_id: str, background: BackgroundTasks, user=Depends(current_user)):
        if user.get("alias_status") != "active" or not user.get("alias") or user["alias"].endswith(".invalid"):
            raise HTTPException(409, "Your account's receiving alias must be active before signup")
        with store.connect() as db:
            source = db.execute("SELECT * FROM sources WHERE id=? AND user_id=?", (source_id, user["id"])).fetchone()
            if not source:
                raise HTTPException(404, "Newsletter not found")
            if source["source_kind"] == "email":
                db.execute("UPDATE sources SET subscription_status='receiving',muted=0,subscription_detail='New email editions will create posts again' WHERE id=?",(source_id,))
                return {"status":"receiving","source_id":source_id}
            if source["subscription_status"] in ("submitted", "confirmation_required", "email_received", "receiving", "submitting"):
                raise HTTPException(409, "Signup was already submitted; check confirmation or incoming mail")
            url = validate_source_url(source["signup_url"] or source["url"])
            fingerprint = "subscribe-only:" + source_id + ":" + hashlib.sha256(user["alias"].encode()).hexdigest()
            existing = db.execute("SELECT id,status FROM jobs WHERE user_id=? AND fingerprint=?", (user["id"], fingerprint)).fetchone()
            if existing and existing["status"] in ("queued", "processing"):
                return {"job_id": existing["id"], "status": existing["status"], "duplicate": True}
            job_id = existing["id"] if existing else uid()
            payload = json.dumps({"url": url, "auto_subscribe": True})
            if existing:
                db.execute("UPDATE jobs SET status='queued',stage='queued',payload=?,error=NULL,result='{}' WHERE id=?", (payload, job_id))
            else:
                db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'subscription')",
                           (job_id, user["id"], source_id, fingerprint, "Subscribe using account alias", payload, now()))
            db.execute("UPDATE sources SET subscription_status='queued',muted=0,subscription_detail='Signup queued using the active account alias' WHERE id=?", (source_id,))
        background.add_task(process_job, job_id)
        return {"job_id": job_id, "status": "queued", "duplicate": False}

    @app.post("/v1/me/sources/{source_id}/unsubscribe")
    def unsubscribe_source(source_id: str, user=Depends(current_user)):
        with store.connect() as db:
            if not db.execute("SELECT 1 FROM sources WHERE id=? AND user_id=?", (source_id, user["id"])).fetchone():
                raise HTTPException(404, "Newsletter not found")
        try:
            return subscriptions.unsubscribe(user["id"], source_id)
        except SourceError as exc:
            raise HTTPException(503, str(exc)) from None

    @app.post("/v1/generation", status_code=202)
    def generate_from_url(body: GenerationInput, background: BackgroundTasks, user=Depends(current_user)):
        url = validate_source_url(body.url)
        identity = "generate-v1:" + hashlib.sha256(f"{url}:{body.mode}:{body.source_id or ''}".encode()).hexdigest()
        with store.connect() as db:
            if not body.refresh:
                existing = db.execute("SELECT id,status FROM jobs WHERE user_id=? AND fingerprint=?", (user["id"], identity)).fetchone()
                if existing:
                    return {"job_id": existing["id"], "status": existing["status"], "duplicate": True}
            else:
                identity += ":" + uid()
            if body.source_id:
                source = db.execute("SELECT id FROM sources WHERE id=? AND user_id=?", (body.source_id, user["id"])).fetchone()
                if not source:
                    raise HTTPException(404, "Source not found")
                source_id = source["id"]
            else:
                host = urlsplit(url).hostname
                sender = "web:" + host
                source = db.execute("SELECT id FROM sources WHERE user_id=? AND sender=?", (user["id"], sender)).fetchone()
                source_id = source["id"] if source else uid()
                if not source:
                    db.execute("INSERT INTO sources(id,user_id,name,sender,url) VALUES(?,?,?,?,?)", (source_id, user["id"], host, sender, url))
            job_id = uid()
            db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'generation')",
                       (job_id, user["id"], source_id, identity, "Generate from " + urlsplit(url).hostname, body.model_dump_json(), now()))
        background.add_task(process_job, job_id)
        return {"job_id": job_id, "status": "queued", "duplicate": False}

    @app.post("/v1/posts/{post_id}/regenerate", status_code=202)
    def regenerate_post(post_id: str, background: BackgroundTasks, user=Depends(current_user)):
        with store.connect() as db:
            post = get_post(db, user, post_id)
            if post["rights"] != "private" or post["status"] == "rejected" or not (engine_generation(post["generation"]) or post['generation']=='source:preserved-email'):
                raise HTTPException(409, "Only active private source-engine posts can be regenerated")
            email_payload=None
            if post['original_url'].startswith('email:'):
                saved=db.execute('SELECT * FROM documents WHERE job_id=? AND user_id=? AND url=? ORDER BY created_at DESC LIMIT 1',(post['job_id'],user['id'],post['original_url'])).fetchone()
                if not saved:raise HTTPException(409,'Original email content is unavailable')
                metadata=json.loads(saved['metadata'])
                email_payload={'fingerprint':post['original_url'].removeprefix('email:').split('#')[0],
                               'subject':metadata.get('email_subject',saved['title']),'text':metadata.get('email_text',''),
                               'sender':'','source_name':metadata.get('author',''),'links':metadata.get('_links',[]),
                               'urls':[link['url'] for link in metadata.get('_links',[])],
                               'image_urls':[figure['url'] for figure in metadata.get('_figures',[]) if figure.get('url')],
                               'email_images_pending':metadata.get('email_images_pending',False),
                               'replace_post_id':post_id,'target_url':post['original_url']}
                if not email_payload['text']:raise HTTPException(409,'Original email text is unavailable')
            analysis = json.loads(post["analysis"])
            url = analysis.get("provenance", {}).get("newsletter_issue_url") or post["original_url"]
            identity = "regenerate:" + post_id
            existing = db.execute("SELECT id,status FROM jobs WHERE user_id=? AND fingerprint=?", (user["id"], identity)).fetchone()
            if existing and existing["status"] in ("queued", "processing"):
                return {"job_id": existing["id"], "status": existing["status"], "duplicate": True}
            job_id = existing["id"] if existing else uid()
            payload = json.dumps(email_payload or {"url": url, "mode": "article", "replace_post_id": post_id})
            kind='received_email' if email_payload else 'generation'
            if existing:
                db.execute("UPDATE jobs SET status='queued',stage='queued',payload=?,kind=?,error=NULL,result='{}' WHERE id=?", (payload,kind,job_id))
            else:
                db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,?)",
                           (job_id, user["id"], post["source_id"], identity, "Regenerate from original source", payload, now(),kind))
        background.add_task(process_job, job_id)
        return {"job_id": job_id, "status": "queued", "duplicate": False}

    @app.post("/v1/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, background: BackgroundTasks, user=Depends(current_user)):
        with store.connect() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user["id"])).fetchone()
            if not job:
                raise HTTPException(404, "Job not found")
            if job["status"] != "failed" or not job["payload"]:
                raise HTTPException(409, "Only failed source jobs with a retained request can be retried")
            db.execute("UPDATE jobs SET status='queued',stage='queued',error=NULL WHERE id=?", (job_id,))
        background.add_task(process_job, job_id)
        return {"job_id": job_id, "status": "queued"}

    @app.post("/v1/dev/accounts", status_code=201)
    def create_account(body: AccountInput):
        if auth:
            raise HTTPException(404, "Development accounts are disabled; use Supabase sign-up")
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "Unknown timezone")
        token = secrets.token_urlsafe(32)
        user = {"id": uid(), "alias": f"{secrets.token_hex(12)}@inbox.prototype.invalid", "timezone": body.timezone}
        with store.connect() as db:
            db.execute("INSERT INTO users(id,token_hash,alias,created_at,timezone) VALUES(?,?,?,?,?)",
                       (user["id"], hashlib.sha256(token.encode()).hexdigest(), user["alias"], now(), body.timezone))
        return {**account_json(user), "token": token, "alias_deliverable": False}

    @app.get("/v1/me")
    def me(user=Depends(current_user), credentials=Depends(bearer)):
        if auth and auth.relay_aliases:
            user = remember_verified_user(auth.ensure_relay(credentials.credentials, user))
        return {**account_json(user), "alias_deliverable": user.get("alias_status") == "active"}

    def require_test_relay(user):
        if not relay or user["id"] != relay.owner_id:
            raise HTTPException(404, "Temporary receiving is not enabled for this account")

    @app.get("/v1/relay/test")
    def test_receiving_status(user=Depends(current_user)):
        require_test_relay(user)
        with store.connect() as db:
            counts = dict(db.execute("SELECT state,COUNT(*) FROM test_relay_receipts WHERE user_id=? GROUP BY state", (user["id"],)).fetchall())
        return {"scope": "temporary_single_account", "provider_configured": relay.reader is not None,
                "alias_status": user["alias_status"], "receipts": counts, "last_error": relay.last_error}

    @app.post("/v1/relay/test/sync", status_code=202)
    def sync_test_receiving(background: BackgroundTasks, user=Depends(current_user)):
        require_test_relay(user)
        if not relay.reader:
            raise HTTPException(503, "Save RESEND_API_KEY in the ignored backend settings file")
        background.add_task(receive_mail)
        return {"status": "queued"}

    @app.patch("/v1/me")
    def update_profile(body: ProfilePatch, user=Depends(current_user), credentials=Depends(bearer)):
        if not auth:
            raise HTTPException(409, "Profile editing requires a verified account")
        fields = body.model_dump(exclude_none=True)
        if "timezone" in fields:
            try:
                ZoneInfo(fields["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                raise HTTPException(422, "Unknown timezone")
        if "display_name" in fields:
            fields["display_name"] = fields["display_name"].strip()
        if fields:
            auth.update_profile(credentials.credentials, user["id"], fields)
        return account_json(remember_verified_user(auth.identity(credentials.credentials)))

    @app.delete("/v1/me", status_code=204)
    def delete_account(user=Depends(current_user)):
        if auth:
            auth.delete(user["id"])
        with store.connect() as db:
            db.execute("DELETE FROM users WHERE id=?", (user["id"],))
            db.execute("DELETE FROM generation_artifacts WHERE scope=?", ("user:" + user["id"],))

    @app.post("/v1/dev/demo")
    def seed_demo(user=Depends(current_user)):
        with store.connect() as db:
            existing = db.execute("SELECT id FROM jobs WHERE user_id=? AND fingerprint='roundup-oct-18'", (user["id"],)).fetchone()
            if existing:
                return {"status": "already_loaded", "job_id": existing["id"]}
            source = db.execute("SELECT id FROM sources WHERE user_id=? AND sender=?", (user["id"], DEMO_SENDER)).fetchone()
            source_id, job_id = (source["id"] if source else uid()), uid()
            if source:
                db.execute("UPDATE sources SET name=?,signup_url=CASE WHEN signup_url='' THEN ? ELSE signup_url END WHERE id=?",
                           (DEMO_SOURCE_NAME, DEMO_SIGNUP, source_id))
            else:
                db.execute("INSERT INTO sources(id,user_id,name,sender,signup_url) VALUES(?,?,?,?,?)",
                           (source_id, user["id"], DEMO_SOURCE_NAME, DEMO_SENDER, DEMO_SIGNUP))
            db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,created_at) VALUES(?,?,?,?,?,?,?)",
                       (job_id, user["id"], source_id, "roundup-oct-18", DEMO_SUBJECT, "completed", now()))
            for draft in DEMO_POSTS:
                db.execute("""INSERT INTO posts
                    (id,user_id,source_id,job_id,title,hook,finding,limitations,original_url,evidence,rights,status,is_demo,generation,created_at,published_date)
                    VALUES(?,?,?,?,?,?,?,?,?,?,'public','approved',1,'seeded-sample',?,?)""",
                    (uid(), user["id"], source_id, job_id, draft["title"], draft["hook"], draft["finding"],
                     DEMO_LIMITS, draft["original_url"], json.dumps([{"source": DEMO_SUBJECT, "quote": draft["finding"], "url": draft["original_url"]}]), now(), local_day(user)))
        return {"status": "loaded", "job_id": job_id}

    @app.post("/v1/import/email", status_code=202)
    async def import_email(background: BackgroundTasks, file: UploadFile = File(...), user=Depends(current_user)):
        raw = await file.read(2_000_001)
        await file.close()
        if len(raw) > 2_000_000:
            raise HTTPException(413, "Email must be under 2 MB")
        try:
            parsed = parse_newsletter(raw)
        except (ValueError, LookupError, TypeError):
            raise HTTPException(422, "Cannot extract a readable newsletter with a valid sender and source link")
        with store.connect() as db:
            existing = db.execute("SELECT id,status FROM jobs WHERE user_id=? AND fingerprint=?", (user["id"], parsed["fingerprint"])).fetchone()
            if existing:
                return {"job_id": existing["id"], "status": existing["status"], "duplicate": True}
            source = db.execute("SELECT id FROM sources WHERE user_id=? AND sender=?", (user["id"], parsed["sender"])).fetchone()
            source_id = source["id"] if source else uid()
            if not source:
                db.execute("INSERT INTO sources(id,user_id,name,sender) VALUES(?,?,?,?)", (source_id, user["id"], parsed["source_name"], parsed["sender"]))
            job_id = uid()
            db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at) VALUES(?,?,?,?,?,'queued',?,?)",
                       (job_id, user["id"], source_id, parsed["fingerprint"], parsed["subject"], json.dumps(parsed), now()))
        background.add_task(process_job, job_id)
        return {"job_id": job_id, "status": "queued", "duplicate": False}

    @app.get("/v1/jobs")
    def jobs(user=Depends(current_user)):
        with store.connect() as db:
            rows = db.execute("SELECT id,source_id,subject,status,error,created_at,kind,stage,result FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user["id"],)).fetchall()
            return [{**dict(row), "result": json.loads(row["result"])} for row in rows]

    @app.get("/v1/me/sources")
    def sources(user=Depends(current_user)):
        with store.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM sources WHERE user_id=? ORDER BY name", (user["id"],))]

    @app.get("/v1/inbox/candidates")
    def inbox_candidates(user=Depends(current_user)):
        with store.connect() as db:
            rows=db.execute("SELECT id,sender,sender_name,provider,subject,status FROM inbox_candidates WHERE user_id=? AND status IN ('pending','dismissed') ORDER BY updated_at DESC LIMIT 20",(user["id"],)).fetchall()
            return [dict(row) for row in rows]

    @app.post("/v1/inbox/candidates/{candidate_id}/dismiss", status_code=204)
    def dismiss_email_candidate(candidate_id: str, user=Depends(current_user)):
        with store.connect() as db:
            if not db.execute("UPDATE inbox_candidates SET status='dismissed' WHERE id=? AND user_id=? AND status!='accepted'",(candidate_id,user["id"])).rowcount:
                raise HTTPException(404,"Email suggestion not found")

    @app.post("/v1/inbox/candidates/{candidate_id}/accept", status_code=202)
    def accept_email_candidate(candidate_id: str, body: EmailNewsletterInput, background: BackgroundTasks, user=Depends(current_user)):
        name=body.name.strip()
        if not name or any(ord(char)<32 for char in name):
            raise HTTPException(422,"Enter a newsletter name")
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate=db.execute("SELECT * FROM inbox_candidates WHERE id=? AND user_id=?",(candidate_id,user["id"])).fetchone()
            if not candidate:raise HTTPException(404,"Email suggestion not found")
            source=db.execute("SELECT * FROM sources WHERE user_id=? AND email_sender=? AND email_topic=?",(user["id"],candidate["sender"],candidate['channel_key'])).fetchone()
            if candidate["status"]=='accepted':
                return {"source_id":source["id"] if source else None,"status":"already_added"}
            parsed=json.loads(candidate["payload"])
            source_id=source["id"] if source else uid()
            if not source:
                identity='email:'+hashlib.sha256((candidate['sender']+'|'+candidate['channel_key']).encode()).hexdigest()
                db.execute("INSERT INTO sources(id,user_id,name,sender,subscription_status,subscription_detail,subscription_alias,expected_sender_domain,source_kind,email_sender,email_topic) VALUES(?,?,?,?,'receiving','Email newsletter connected by you',?,?,'email',?,?)",
                           (source_id,user["id"],name,identity,user["alias"],candidate["sender"].rsplit('@',1)[-1],candidate['sender'],candidate['channel_key']))
            else:
                db.execute("UPDATE sources SET name=?,source_kind='email',subscription_status='receiving',subscription_detail='Email newsletter connected by you' WHERE id=?",(name,source_id))
            fingerprint='received-email:'+parsed['fingerprint']
            existing=db.execute("SELECT * FROM jobs WHERE user_id=? AND fingerprint=?",(user["id"],fingerprint)).fetchone()
            job_id=existing['id'] if existing else uid()
            if not existing:
                db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'received_email')",(job_id,user["id"],source_id,fingerprint,parsed['subject'],json.dumps(parsed),now()))
            elif existing['status'] in ('failed','cancelled'):
                db.execute("UPDATE jobs SET source_id=?,status='queued',stage='queued',payload=?,error=NULL,result='{}' WHERE id=? AND user_id=?",(source_id,json.dumps(parsed),job_id,user['id']))
            elif existing['status']=='processing':
                raise HTTPException(409,'This edition is being processed; try again when it finishes')
            else:
                db.execute("UPDATE jobs SET source_id=? WHERE id=? AND user_id=?",(source_id,job_id,user['id']))
                db.execute("UPDATE posts SET source_id=? WHERE job_id=? AND user_id=?",(source_id,job_id,user['id']))
                db.execute("INSERT OR IGNORE INTO post_sources(post_id,source_id,user_id) SELECT id,?,user_id FROM posts WHERE job_id=? AND user_id=?",(source_id,job_id,user['id']))
            db.execute("UPDATE inbox_candidates SET status='accepted',payload='{}' WHERE id=? AND user_id=?",(candidate_id,user["id"]))
            if parsed.get('receiving_message_id'):
                db.execute("UPDATE test_relay_receipts SET state='queued',job_id=? WHERE message_id=? AND user_id=?",(job_id,parsed['receiving_message_id'],user['id']))
        background.add_task(process_job,job_id)
        return {"source_id":source_id,"job_id":job_id,"status":"queued"}

    @app.patch("/v1/me/sources/{source_id}")
    def patch_source(source_id: str, body: SourcePatch, user=Depends(current_user)):
        with store.connect() as db:
            source = db.execute("SELECT * FROM sources WHERE id=? AND user_id=?", (source_id, user["id"])).fetchone()
            if not source:
                raise HTTPException(404, "Source not found")
            if body.name is not None:
                if not body.name.strip():
                    raise HTTPException(422, "Newsletter name cannot be empty")
                db.execute("UPDATE sources SET name=? WHERE id=?", (body.name.strip(), source_id))
            if body.signup_url is not None:
                url = safe_url(body.signup_url) if body.signup_url else ""
                if body.signup_url and not url:
                    raise HTTPException(422, "Use a public HTTP(S) signup URL")
                db.execute("UPDATE sources SET signup_url=? WHERE id=?", (url, source_id))
            if body.muted is not None:
                db.execute("UPDATE sources SET muted=? WHERE id=?", (body.muted, source_id))
            return dict(db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())

    @app.get("/v1/review")
    def review(user=Depends(current_user)):
        with store.connect() as db:
            return [post_json(db, row, user) for row in db.execute("SELECT * FROM posts WHERE user_id=? AND status='review' ORDER BY created_at", (user["id"],)).fetchall()]

    @app.post("/v1/review/{post_id}/approve")
    def approve(post_id: str, body: Approval, user=Depends(current_user)):
        if not body.reviewed:
            raise HTTPException(422, "Review the excerpt and original source before approving")
        with store.connect() as db:
            row = get_post(db, user, post_id)
            if body.rights == "public":
                if row["original_url"].startswith("email:"):
                    raise HTTPException(409, "Posts based on private email editions cannot be published publicly")
                source = db.execute("SELECT signup_url FROM sources WHERE id=?", (row["source_id"],)).fetchone()
                if not source["signup_url"]:
                    raise HTTPException(422, "Set the publication signup URL before public approval")
            db.execute("UPDATE posts SET status='approved',rights=?,published_date=COALESCE(published_date,?) WHERE id=?", (body.rights, local_day(user), post_id))
            analysis = json.loads(row["analysis"])
            analysis["publication"] = {"mode": "manual", "human_reviewed": True}
            db.execute("UPDATE posts SET analysis=? WHERE id=?", (json.dumps(analysis), post_id))
            pending = db.execute("SELECT 1 FROM posts WHERE job_id=? AND status='review'", (row["job_id"],)).fetchone()
            if not pending:
                db.execute("UPDATE jobs SET status='completed' WHERE id=?", (row["job_id"],))
            return post_json(db, get_post(db, user, post_id), user)

    @app.post("/v1/review/{post_id}/reject", status_code=204)
    def reject(post_id: str, user=Depends(current_user)):
        with store.connect() as db:
            row = get_post(db, user, post_id)
            if row["status"] == "rejected":
                raise HTTPException(409, "Post is already removed")
            db.execute("UPDATE posts SET status='rejected' WHERE id=?", (post_id,))
            if not db.execute("SELECT 1 FROM posts WHERE job_id=? AND status='review'", (row["job_id"],)).fetchone():
                db.execute("UPDATE jobs SET status='completed' WHERE id=?", (row["job_id"],))

    @app.get("/v1/feed")
    def feed(day: date | None = None, history: bool = False,
             read_state: Literal['all','unread','read'] = 'all',
             limit: int = Query(50,ge=1,le=100), before: str | None = Query(None,max_length=128),
             user=Depends(current_user)):
        edition_date = day.isoformat() if day else local_day(user)
        with store.connect() as db:
            conditions = ["p.user_id=?", "p.status='approved'", "p.published_date<=?" if history else "p.published_date=?"]
            params = [user['id'],edition_date]
            if read_state != 'read':
                conditions.append("""(s.muted=0 OR EXISTS(
                    SELECT 1 FROM post_sources ps JOIN sources other ON other.id=ps.source_id
                    WHERE ps.post_id=p.id AND ps.user_id=p.user_id AND other.user_id=p.user_id AND other.muted=0))""")
            read_clause = "EXISTS(SELECT 1 FROM reads r WHERE r.user_id=p.user_id AND r.post_id=p.id)"
            if read_state != 'all':conditions.append(read_clause if read_state=='read' else 'NOT '+read_clause)
            base_sql = ' FROM posts p JOIN sources s ON s.id=p.source_id WHERE '+' AND '.join(conditions)
            totals=db.execute('SELECT COUNT(*),COALESCE(SUM(NOT '+read_clause+'),0)'+base_sql,params).fetchone()
            page_sql=base_sql
            page_params=list(params)
            if before:
                cursor=db.execute("SELECT published_date,created_at,id FROM posts WHERE id=? AND user_id=? AND status='approved'",(before,user['id'])).fetchone()
                if not cursor:raise HTTPException(404,'Feed cursor not found')
                page_sql+=' AND (p.published_date,p.created_at,p.id)<(?,?,?)'
                page_params.extend(tuple(cursor))
            rows=db.execute('SELECT p.*'+page_sql+' ORDER BY p.published_date DESC,p.created_at DESC,p.id DESC LIMIT ?',page_params+[limit+1]).fetchall()
            next_cursor=rows[limit-1]['id'] if len(rows)>limit else None
            rows=rows[:limit]
            posts = [post_json(db, row, user) for row in rows]
        return {"date": edition_date, "posts": posts, "total": totals[0], "unread": totals[1], "finite": True,'next_cursor':next_cursor}

    @app.get("/v1/bookmarks")
    def bookmarks(user=Depends(current_user)):
        with store.connect() as db:
            rows = db.execute("SELECT p.* FROM posts p JOIN bookmarks b ON b.post_id=p.id WHERE b.user_id=? AND p.status='approved' ORDER BY p.created_at DESC", (user["id"],)).fetchall()
            return [post_json(db, row, user) for row in rows]

    @app.get("/v1/posts/{post_id}")
    def post(post_id: str, user=Depends(current_user)):
        with store.connect() as db:
            return post_json(db, get_post(db, user, post_id), user)

    @app.get("/v1/posts/{post_id}/source")
    def post_source(post_id: str, user=Depends(current_user)):
        with store.connect() as db:
            post=get_post(db,user,post_id)
            if not post['original_url'].startswith('email:'):raise HTTPException(404,'This post links to a public original source')
            document=db.execute('SELECT title,metadata,spans FROM documents WHERE job_id=? AND user_id=? AND url=? ORDER BY created_at DESC LIMIT 1',(post['job_id'],user['id'],post['original_url'])).fetchone()
            if not document:raise HTTPException(404,'Source email is unavailable')
            metadata=json.loads(document['metadata'])
            return {'title':metadata.get('email_subject',document['title']),'text':metadata.get('email_text') or '\n\n'.join(span['text'] for span in json.loads(document['spans'])),'author':metadata.get('author','')}

    @app.get("/v1/posts/{post_id}/image")
    def post_image(post_id: str, user=Depends(current_user)):
        with store.connect() as db:
            get_post(db, user, post_id)
            asset = db.execute("SELECT image,content_type FROM post_assets WHERE post_id=?", (post_id,)).fetchone()
            if not asset:
                raise HTTPException(404, "Post has no ready visual")
            return Response(asset["image"], media_type=asset["content_type"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/v1/posts/{post_id}/chat")
    def post_chat(post_id: str, body: ChatInput, user=Depends(current_user)):
        from .retrieval import SourceError
        job_id = uid()
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = dict(get_post(db, user, post_id))
            # Permit one concurrent question and ten questions/minute per account.
            recent = db.execute("SELECT status,created_at FROM jobs WHERE user_id=? AND kind='ask_ai' ORDER BY created_at DESC LIMIT 10", (user["id"],)).fetchall()
            stamp = datetime.now(timezone.utc)
            if any(item["status"] == "processing" and (stamp - datetime.fromisoformat(item["created_at"])).total_seconds() < 300 for item in recent) or (len(recent) == 10 and (stamp - datetime.fromisoformat(recent[-1]["created_at"])).total_seconds() < 60):
                raise HTTPException(429, "Wait for your previous answer before asking again")
            db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,created_at,kind) VALUES(?,?,?,?,?,'processing',?,'ask_ai')",
                       (job_id, user["id"], row["source_id"], job_id, "Ask AI", now()))
        try:
            model = generation_model()
            model.budget = JobBudget(store, job_id)
            result = answer_post(row, body, model)
            with store.connect() as db:
                get_post(db, user, post_id)
                db.execute("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
            return result
        except SourceError as exc:
            with store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (str(exc), job_id))
            raise HTTPException(503, str(exc)) from None
        finally:
            with store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error='Answer interrupted; please retry' WHERE id=? AND status='processing'", (job_id,))

    @app.get("/v1/posts/{post_id}/images/{asset_id}")
    def gallery_image(post_id: str, asset_id: str, user=Depends(current_user)):
        with store.connect() as db:
            post = get_post(db, user, post_id)
            visuals = json.loads(post["analysis"]).get("visuals", [])
            if not any(visual.get("asset_id") == asset_id for visual in visuals):
                raise HTTPException(404, "Image not found")
            asset = db.execute("SELECT image,content_type FROM gallery_assets WHERE id=? AND artifact_key=?", (asset_id, post["artifact_key"])).fetchone()
            if not asset and json.loads(post["visual"]).get("asset_id") == asset_id:
                asset = db.execute("SELECT image,content_type FROM post_assets WHERE post_id=?", (post_id,)).fetchone()
            if not asset:
                raise HTTPException(404, "Image not found")
            return Response(asset["image"], media_type=asset["content_type"], headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/v1/posts/{post_id}/image/retry", status_code=202)
    def retry_visual(post_id: str, background: BackgroundTasks, body: VisualRetryInput | None = None, user=Depends(current_user)):
        with store.connect() as db:
            post = get_post(db, user, post_id)
            if post["status"] == "rejected" or not (engine_generation(post["generation"]) or post['generation']=='source:preserved-email'):
                raise HTTPException(409, "Visual generation is only available for active source-engine posts")
            visual = json.loads(post["visual"])
            if visual.get("kind") == "source" and visual.get("status") == "ready":
                return {"status": "ready", "detail": "Existing source visual retained; image generation was not requested"}
            pending = db.execute("SELECT id FROM jobs WHERE user_id=? AND kind='visual' AND status IN ('queued','processing') AND fingerprint=?", (user["id"], "visual:" + post_id)).fetchone()
            if pending:
                return {"job_id": pending["id"], "status": "queued"}
            # A stable per-post visual job provides idempotent retries.
            existing = db.execute("SELECT id FROM jobs WHERE user_id=? AND fingerprint=?", (user["id"], "visual:" + post_id)).fetchone()
            job_id = existing["id"] if existing else uid()
            payload = json.dumps({"post_id": post_id, "source_only": bool(body and body.source_only)})
            if existing:
                db.execute("UPDATE jobs SET status='queued',payload=?,error=NULL WHERE id=?", (payload, job_id))
            else:
                db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'visual')",
                           (job_id, user["id"], post["source_id"], "visual:" + post_id, "Prepare post visual", payload, now()))
        background.add_task(process_job, job_id)
        return {"job_id": job_id, "status": "queued"}

    def published_post(db, user, post_id):
        row = get_post(db, user, post_id)
        if row["status"] != "approved":
            raise HTTPException(409, "This draft is not published")
        return row

    @app.post("/v1/posts/{post_id}/bookmark", status_code=204)
    def bookmark(post_id: str, user=Depends(current_user)):
        with store.connect() as db:
            published_post(db, user, post_id)
            db.execute("INSERT OR IGNORE INTO bookmarks VALUES(?,?)", (user["id"], post_id))

    @app.delete("/v1/posts/{post_id}/bookmark", status_code=204)
    def unbookmark(post_id: str, user=Depends(current_user)):
        with store.connect() as db:
            get_post(db, user, post_id)
            db.execute("DELETE FROM bookmarks WHERE user_id=? AND post_id=?", (user["id"], post_id))

    @app.post("/v1/posts/{post_id}/read", status_code=204)
    def mark_read(post_id: str, user=Depends(current_user)):
        with store.connect() as db:
            published_post(db, user, post_id)
            db.execute("INSERT OR IGNORE INTO reads VALUES(?,?)", (user["id"], post_id))

    @app.post("/v1/posts/{post_id}/share")
    def share(post_id: str, request: Request, user=Depends(current_user)):
        with store.connect() as db:
            row = published_post(db, user, post_id)
            if row['original_url'].startswith('email:'):raise HTTPException(409,'This original source is a private email, not a public link')
            signup = db.execute("SELECT signup_url FROM sources WHERE id=?", (row["source_id"],)).fetchone()["signup_url"]
            if row["rights"] != "public" or not signup:
                return {"url": row["original_url"], "mode": "original-source", "local_only": False}
            existing = db.execute("SELECT token FROM shares WHERE post_id=?", (post_id,)).fetchone()
            token = existing["token"] if existing else secrets.token_urlsafe(24)
            if not existing:
                db.execute("INSERT INTO shares VALUES(?,?)", (token, post_id))
        return {"url": str(request.base_url) + "s/" + token, "mode": "public-preview", "local_only": True}

    @app.get("/s/{token}", response_class=HTMLResponse)
    def shared_page(token: str):
        with store.connect() as db:
            row = db.execute("""SELECT p.*,s.name AS source_name,s.signup_url FROM shares l
                JOIN posts p ON p.id=l.post_id JOIN sources s ON s.id=p.source_id
                WHERE l.token=? AND p.rights='public' AND p.status='approved' AND s.signup_url!=''""", (token,)).fetchone()
        if not row:
            raise HTTPException(404, "Shared post is unavailable")
        e = lambda key: html.escape(str(row[key]), quote=True)
        banner = "Seeded sample edition" if row["is_demo"] else "Manually reviewed newsletter excerpt"
        return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
            <meta name="viewport" content="width=device-width,initial-scale=1"><title>{e('title')}</title>
            <style>body{{font:18px/1.6 system-ui;max-width:650px;margin:50px auto;padding:24px}}p{{white-space:pre-line}}a{{color:#245c49}}.cta{{display:inline-block;padding:12px 20px;background:#245c49;color:white;border-radius:10px}}</style></head>
            <body><small>LOCAL PROTOTYPE · {banner}</small><h1>{e('title')}</h1><p>{e('hook')}</p>
            <p>{e('finding')}</p><p><small>{e('limitations')}</small></p><p>From {e('source_name')}</p>
            <p><a href="{e('original_url')}" rel="noreferrer noopener">Read the original source</a></p>
            <hr><a class="cta" href="{e('signup_url')}" rel="noreferrer noopener">Sign up for this newsletter</a>
            </body></html>""", headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})

    return app
