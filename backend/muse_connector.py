"""Per-account private Muse API connector; credentials never grant app access."""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .connector_content import ConnectorEdition
from .retrieval import SourceError
from .source_lifecycle import category

MAX_BODY = 128_000
logger = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r'utd_muse_[A-Za-z0-9_-]{43}')
SCHEMA = """
CREATE TABLE IF NOT EXISTS muse_connector_grants (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, expires_at REAL NOT NULL,
 revoked_at REAL, last_used_at REAL
);
CREATE INDEX IF NOT EXISTS muse_connector_grants_owner ON muse_connector_grants(user_id);
CREATE TABLE IF NOT EXISTS muse_connector_deliveries (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 grant_id TEXT NOT NULL REFERENCES muse_connector_grants(id) ON DELETE CASCADE,
 newsletter_key TEXT NOT NULL, edition_id TEXT NOT NULL, content_hash TEXT NOT NULL,
 job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
 created_at REAL NOT NULL, UNIQUE(user_id,newsletter_key,edition_id)
);
CREATE INDEX IF NOT EXISTS muse_connector_deliveries_owner ON muse_connector_deliveries(user_id,created_at);
"""


def settings():
    raw=os.environ.get('PRIVATE_MUSE_ACCOUNTS','').strip()
    try:
        pairs=json.loads(raw) if raw else {os.environ.get('PRIVATE_MUSE_USER_ID',''):os.environ.get('PRIVATE_MUSE_EMAIL','')}
        if not isinstance(pairs,dict) or not 1<=len(pairs)<=10:raise ValueError()
        accounts={str(uuid.UUID(key)):email.strip().casefold() for key,email in pairs.items()}
        if len(accounts)!=len(pairs) or any(not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) for email in accounts.values()) or len(set(accounts.values()))!=len(accounts):raise ValueError()
    except (ValueError,TypeError,AttributeError):accounts={}
    enabled=os.environ.get('PRIVATE_MUSE_CONNECTOR_ENABLED','0')=='1'
    mode=os.environ.get('MUSE_CONNECTOR_ACCESS','approved_accounts')
    return accounts,enabled and (mode=='all_verified' or mode=='approved_accounts' and bool(accounts))


def all_verified_accounts():
    return os.environ.get('MUSE_CONNECTOR_ACCESS','approved_accounts')=='all_verified'


def enabled():
    if not settings()[1]:raise HTTPException(404,'Private connector is unavailable')


def allowed(user):
    enabled()
    if not available_to(user):
        raise HTTPException(403,'Private connector testing is not enabled for this account')


def available_to(user):
    accounts,active=settings()
    # Callers obtain this identity from verified Supabase authentication, or
    # revalidate it with connector_identity before authorizing a connector.
    try:identifier=str(uuid.UUID(user.get('id','')))
    except (ValueError,TypeError,AttributeError):return False
    email=user.get('email','').casefold()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):return False
    return active and (all_verified_accounts() or accounts.get(identifier)==email)


def issue_grant(store,user):
    allowed(user)
    raw='utd_muse_'+secrets.token_urlsafe(32)
    stamp=time.time();identifier=uuid.uuid4().hex
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        # Rotation invalidates the previous key; only the digest is stored.
        db.execute('UPDATE muse_connector_grants SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL',(stamp,user['id']))
        db.execute('INSERT INTO muse_connector_grants(id,user_id,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)',
            (identifier,user['id'],hashlib.sha256(raw.encode()).hexdigest(),stamp,stamp+30*86400))
    return {'id':identifier,'credential':raw,'expires_at':stamp+30*86400,
            'permissions':['editions:submit','deliveries:read','custom_newsletters:list']}


def ensure_job_allowed(db,job_id):
    row=db.execute('SELECT g.* FROM muse_connector_deliveries d JOIN muse_connector_grants g ON g.id=d.grant_id WHERE d.job_id=?',(job_id,)).fetchone()
    accounts,active=settings()
    owner_allowed=bool(row and (all_verified_accounts() or row['user_id'] in accounts))
    if not row or not active or not owner_allowed or row['revoked_at'] is not None or row['expires_at']<=time.time():
        raise SourceError('Muse connection was disconnected or expired; no further processing was authorized')


class MuseConnector:
    def __init__(self,store,auth):self.store,self.auth=store,auth

    def authentication_failed(self,reason,detail):
        # Record only a fixed reason code. Credentials and their digests must
        # never appear in logs, even when Muse sends the wrong saved key.
        logger.warning('Muse connector authentication rejected (%s)',reason)
        raise HTTPException(401,detail,headers={'WWW-Authenticate':'Bearer realm="UpToDate Muse"'})

    def authenticate(self,request):
        enabled()
        header=request.headers.get('authorization','')
        parts=header.split()
        token=parts[1] if len(parts)==2 and parts[0].casefold()=='bearer' else ''
        if not TOKEN_PATTERN.fullmatch(token):
            self.authentication_failed('missing_or_malformed_credential','An UpToDate Muse connection key is required. Use the credential saved in your existing connector’s secure authentication settings.')
        digest=hashlib.sha256(token.encode()).hexdigest()
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM muse_connector_grants WHERE token_hash=?',(digest,)).fetchone()
        if not row or not hmac.compare_digest(row['token_hash'],digest):
            self.authentication_failed('unrecognized_credential','Muse sent an unrecognized UpToDate connection key. Use the credential saved in your existing UpToDate connector’s secure authentication settings.')
        if row['revoked_at'] is not None:
            self.authentication_failed('revoked_credential','This Muse connection key was replaced or disconnected. Update the key in Muse’s existing UpToDate connector settings.')
        if row['expires_at']<=time.time():
            self.authentication_failed('expired_credential','This Muse connection key has expired. Replace it in UpToDate Profile and update Muse’s existing connector settings.')
        user=self.auth.connector_identity(row['user_id'])
        allowed(user)
        verified_at=time.time()
        with self.store.connect() as db:
            accepted=db.execute('UPDATE muse_connector_grants SET last_used_at=? WHERE id=? AND revoked_at IS NULL AND expires_at>?',
                                (verified_at,row['id'],verified_at)).rowcount
        if accepted!=1:
            self.authentication_failed('credential_changed_during_request','The Muse connection key was disconnected or expired during this request. Check the existing connector’s secure authentication settings.')
        return dict(row)|{'last_used_at':verified_at}

    def submit(self,grant,edition):
        owner=grant['user_id']
        canonical=edition.model_dump_json(exclude_none=True)
        digest=hashlib.sha256(canonical.encode()).hexdigest()
        stamp=time.time();created=datetime.now(timezone.utc).isoformat()
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT * FROM muse_connector_grants WHERE id=?',(grant['id'],)).fetchone()
            if not current or current['revoked_at'] is not None or current['expires_at']<=stamp:
                raise HTTPException(401,'Muse connection was disconnected or expired')
            previous=db.execute('SELECT * FROM muse_connector_deliveries WHERE user_id=? AND newsletter_key=? AND edition_id=?',
                (owner,edition.newsletter_key,edition.edition_id)).fetchone()
            if previous:
                if previous['content_hash']!=digest:raise HTTPException(409,'This edition ID was already submitted with different content; use a new ID for a new edition')
                return self.status(grant,previous['id'],db=db)|{'duplicate':True}
            recent=db.execute('SELECT created_at FROM muse_connector_deliveries WHERE user_id=? AND created_at>=?',(owner,stamp-86400)).fetchall()
            if len(recent)>=24 or sum(row['created_at']>=stamp-60 for row in recent)>=5:
                raise HTTPException(429,'Private connector delivery limit reached; retry later',headers={'Retry-After':'60'})
            sender='connector:muse:'+hashlib.sha256(edition.newsletter_key.encode()).hexdigest()
            binding=db.execute('SELECT j.source_id FROM muse_connector_deliveries d JOIN jobs j ON j.id=d.job_id WHERE d.user_id=? AND d.newsletter_key=? ORDER BY d.created_at LIMIT 1',(owner,edition.newsletter_key)).fetchone()
            if binding:
                if edition.source_id and edition.source_id!=binding['source_id']:
                    raise HTTPException(409,'This newsletter key belongs to a different saved newsletter')
                source=db.execute('SELECT * FROM sources WHERE id=? AND user_id=?',(binding['source_id'],owner)).fetchone()
            elif edition.source_id:
                source=db.execute('SELECT * FROM sources WHERE id=? AND user_id=?',(edition.source_id,owner)).fetchone()
                if not source or category(dict(source))!='custom':raise HTTPException(404,'Custom newsletter not found')
            else:source=db.execute('SELECT * FROM sources WHERE user_id=? AND sender=?',(owner,sender)).fetchone()
            if source and (source['archived_at'] or source['subscription_status'] in ('unsubscribed','unsubscribe_pending','unsubscribing')):
                raise HTTPException(409,'Newsletter processing is stopped; resume it in Manage before sending another edition')
            source_id=source['id'] if source else uuid.uuid4().hex
            if not source:
                db.execute("INSERT INTO sources(id,user_id,name,sender,source_kind,source_category,subscription_status,subscription_detail) VALUES(?,?,?,?,'connector','custom','receiving','Connected directly through Muse')",
                    (source_id,owner,edition.newsletter_name,sender))
            delivery_id,job_id=uuid.uuid4().hex,uuid.uuid4().hex
            fingerprint='muse-edition:'+hashlib.sha256((owner+'|'+edition.newsletter_key+'|'+edition.edition_id).encode()).hexdigest()
            payload={'edition':edition.model_dump(),'fingerprint':fingerprint.removeprefix('muse-edition:')}
            db.execute("INSERT INTO jobs(id,user_id,source_id,fingerprint,subject,status,payload,created_at,kind) VALUES(?,?,?,?,?,'queued',?,?,'connector_edition')",
                (job_id,owner,source_id,fingerprint,edition.newsletter_name,json.dumps(payload),created))
            db.execute('INSERT INTO muse_connector_deliveries(id,user_id,grant_id,newsletter_key,edition_id,content_hash,job_id,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (delivery_id,owner,grant['id'],edition.newsletter_key,edition.edition_id,digest,job_id,stamp))
            db.execute('UPDATE muse_connector_grants SET last_used_at=? WHERE id=?',(stamp,grant['id']))
        return {'delivery_id':delivery_id,'job_id':job_id,'source_id':source_id,'status':'queued','duplicate':False}

    def status(self,grant,identifier,db=None):
        if db is None:
            with self.store.connect() as connection:return self.status(grant,identifier,db=connection)
        row=db.execute('SELECT d.id,j.id AS job_id,j.source_id,j.status,j.stage,j.error,j.result FROM muse_connector_deliveries d JOIN jobs j ON j.id=d.job_id WHERE d.id=? AND d.user_id=?',
            (identifier,grant['user_id'])).fetchone()
        if not row:raise HTTPException(404,'Delivery not found')
        return {'delivery_id':row['id'],'job_id':row['job_id'],'source_id':row['source_id'],
            'status':row['status'],'stage':row['stage'],'detail':row['error'], 'result':json.loads(row['result'])}

    def newsletter_status(self,grant,key):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}',key):raise HTTPException(422,'Invalid newsletter key')
        sender='connector:muse:'+hashlib.sha256(key.encode()).hexdigest()
        with self.store.connect() as db:
            row=db.execute('SELECT s.* FROM muse_connector_deliveries d JOIN jobs j ON j.id=d.job_id JOIN sources s ON s.id=j.source_id WHERE d.user_id=? AND d.newsletter_key=? ORDER BY d.created_at LIMIT 1',(grant['user_id'],key)).fetchone()
            if not row:row=db.execute('SELECT * FROM sources WHERE user_id=? AND sender=?',(grant['user_id'],sender)).fetchone()
        active=not row or (not row['archived_at'] and row['subscription_status'] not in ('unsubscribed','unsubscribe_pending','unsubscribing'))
        return {'newsletter_key':key,'registered':bool(row),'source_id':row['id'] if row else None,
            'should_process':bool(active),'status':row['subscription_status'] if row else 'not_created'}


def public_origin(request:Request):
    # Cloud Run terminates TLS before forwarding to Uvicorn over HTTP. Use the
    # deployment mode, rather than untrusted forwarded headers, for public URLs.
    base=request.base_url
    if os.environ.get('UPTODATE_ENV')=='cloud':base=base.replace(scheme='https')
    return str(base).rstrip('/')


def install(app,store,auth,current_user,schedule_job):
    service=MuseConnector(store,auth)
    prefix='/connectors/muse'

    @app.get('/v1/me/connectors/muse',include_in_schema=False)
    def connection_status(request:Request,user=Depends(current_user)):
        allowed(user)
        with store.connect() as db:
            grant=db.execute('SELECT id,expires_at,last_used_at FROM muse_connector_grants WHERE user_id=? AND revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC LIMIT 1',(user['id'],time.time())).fetchone()
            delivery=db.execute('SELECT max(created_at) AS latest FROM muse_connector_deliveries WHERE user_id=? AND grant_id=?',(user['id'],grant['id'] if grant else '')).fetchone()
        origin=public_origin(request)
        # Keep the legacy key-ready field for installed beta clients. New
        # clients require verified connector access before claiming connected.
        return JSONResponse({'connected':bool(grant),'verified':bool(grant and grant['last_used_at'] is not None),
            'verified_at':grant['last_used_at'] if grant else None,'expires_at':grant['expires_at'] if grant else None,
            'last_delivery_at':delivery['latest'],'docs_url':origin+'/connectors/muse',
            'setup_url':origin+'/connectors/muse/connect',
            'schema_url':origin+'/connectors/muse/openapi.json'},headers={'Cache-Control':'no-store'})

    @app.post('/v1/me/connectors/muse',include_in_schema=False)
    def connect(user=Depends(current_user)):
        return JSONResponse(issue_grant(store,user),headers={'Cache-Control':'no-store'})

    @app.delete('/v1/me/connectors/muse',include_in_schema=False)
    def disconnect(user=Depends(current_user)):
        allowed(user)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE muse_connector_grants SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL',(time.time(),user['id']))
            db.execute("UPDATE jobs SET status='cancelled',stage='cancelled',payload=NULL WHERE user_id=? AND kind='connector_edition' AND status='queued'",(user['id'],))
        return JSONResponse({'status':'disconnected'},headers={'Cache-Control':'no-store'})

    @app.post(prefix+'/editions',status_code=202,include_in_schema=False)
    async def deliver(request:Request,background:BackgroundTasks):
        grant=await run_in_threadpool(service.authenticate,request)
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>MAX_BODY:raise HTTPException(413,'Edition exceeds the 128 KB private connector limit')
        try:edition=ConnectorEdition.model_validate_json(body)
        except ValidationError as error:
            raise HTTPException(422,error.errors(include_input=False,include_context=False)) from None
        result=await run_in_threadpool(service.submit,grant,edition)
        if not result['duplicate']:schedule_job(background,result['job_id'])
        return JSONResponse(result,status_code=202,headers={'Cache-Control':'no-store'})

    @app.get(prefix+'/deliveries/{delivery_id}',include_in_schema=False)
    def delivery_status(delivery_id:str,request:Request):
        return JSONResponse(service.status(service.authenticate(request),delivery_id),headers={'Cache-Control':'no-store'})

    @app.get(prefix+'/newsletters',include_in_schema=False)
    def newsletters(request:Request):
        grant=service.authenticate(request)
        with store.connect() as db:
            rows=db.execute('SELECT * FROM sources WHERE user_id=? AND archived_at IS NULL',(grant['user_id'],)).fetchall()
        return JSONResponse({'newsletters':[{'id':row['id'],'name':row['name'],'status':row['subscription_status']} for row in rows if category(dict(row))=='custom']},headers={'Cache-Control':'no-store'})

    @app.get(prefix+'/newsletters/{newsletter_key}/status',include_in_schema=False)
    def newsletter_status(newsletter_key:str,request:Request):
        return JSONResponse(service.newsletter_status(service.authenticate(request),newsletter_key),headers={'Cache-Control':'no-store'})

    @app.get(prefix+'/openapi.json',include_in_schema=False)
    def schema(request:Request):
        enabled()
        schema=ConnectorEdition.model_json_schema(ref_template='#/components/schemas/{model}')
        definitions=schema.pop('$defs',{})
        result={'openapi':'3.1.0','info':{'title':'UpToDate private Muse connector','version':'1.0.0',
            'description':'Deliver editions to the connected private account. A verified UpToDate account and its own Muse bearer credential are required. Reuse the existing configured connector and its securely stored credential for every newsletter. Never substitute an app sign-in token or a placeholder key.'},
            'servers':[{'url':public_origin(request)}],
            'security':[{'ConnectionCredential':[]}],
            'components':{'securitySchemes':{'ConnectionCredential':{'type':'http','scheme':'bearer'}},
                          'schemas':{**definitions,'ConnectorEdition':schema}},
            'paths':{prefix+'/editions':{'post':{'operationId':'deliverNewsletterEdition',
                'summary':'Submit one newsletter edition to the connected private feed',
                'description':'Use a stable newsletter_key and a unique edition_id per scheduled run. Reuse both IDs and the identical payload on network retries. Send only requested newsletter content, original source links, and available images. Do not send conversation history. Group a paper, repository and project page within one story. Optional source_id attaches to an existing custom newsletter. Missing fields use the existing UpToDate pipeline.',
                'requestBody':{'required':True,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/ConnectorEdition'}}}},
                'responses':{'202':{'description':'Accepted or previously accepted; poll delivery status'},'401':{'description':'Credential invalid, expired or disconnected'},'409':{'description':'Conflicting edition ID or stopped newsletter'},'429':{'description':'Private delivery rate limit'}}}},
                prefix+'/deliveries/{delivery_id}':{'get':{'operationId':'getNewsletterDelivery', 'summary':'Check the connected account’s edition processing status',
                    'parameters':[{'name':'delivery_id','in':'path','required':True,'schema':{'type':'string'}}], 'responses':{'200':{'description':'Delivery status'}}}},
                prefix+'/newsletters':{'get':{'operationId':'listCustomNewsletters','summary':'List the connected account’s custom newsletters for optional source_id selection',
                    'description':'Use this authenticated read to verify the existing saved connector before creating another newsletter. An empty list is success. On a 401, report the response detail and check the credential stored securely in that same connector; do not create another connector or replace an UpToDate key automatically.',
                    'responses':{'200':{'description':'Custom newsletter IDs and names'},'401':{'description':'Connection credential missing, unrecognized, replaced, or expired; read detail for the appropriate recovery step'}}}}}}
        result['paths'][prefix+'/newsletters/{newsletter_key}/status']={'get':{
            'operationId':'getNewsletterProcessingStatus','summary':'Read whether the owner has stopped this custom newsletter',
            'description':'Read-only preflight for the stable newsletter key. should_process=false means processing was stopped or the entry was removed; submitting a new edition for it will be rejected. An unknown key represents a new newsletter.',
            'parameters':[{'name':'newsletter_key','in':'path','required':True,'schema':{'type':'string'}}],
            'responses':{'200':{'description':'Current per-newsletter processing preference'}}}}
        return JSONResponse(result)

    @app.get(prefix,include_in_schema=False,response_class=HTMLResponse)
    def instructions(request:Request):
        enabled()
        html=INSTRUCTIONS
        if all_verified_accounts():
            html=html.replace('This beta is enabled only for approved, verified test accounts.','This beta is available to verified UpToDate accounts.')
        return HTMLResponse(html,headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",'Referrer-Policy':'no-referrer'})

    @app.get(prefix+'/connect',include_in_schema=False,response_class=HTMLResponse)
    def connection_page():
        enabled()
        origin=auth.url if auth else ''
        nonce=secrets.token_urlsafe(20)
        return HTMLResponse(CONNECT_PAGE.replace('__NONCE__',nonce),headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; connect-src 'self' {origin}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"})


INSTRUCTIONS='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>UpToDate · Muse</title><style>body{font:17px/1.6 system-ui;max-width:680px;margin:40px auto;padding:24px;color-scheme:light dark}a{color:#3987ff}code{overflow-wrap:anywhere}li{margin:12px 0}</style><h1>UpToDate private Muse connection</h1><p>This beta is enabled only for approved, verified test accounts. It delivers directly into the private feed, without email.</p><ol><li><a href="/connectors/muse/connect">Create your private connection credential</a>, or use Profile → Connections → Muse in the updated app. This key is shown once, expires in 30 days, and can be disconnected or replaced.</li><li>Ask Muse to create a custom connector using <a href="/connectors/muse/openapi.json">this OpenAPI specification</a>. Add the connection credential through Muse’s secure credential setup as a Bearer token. Do not put it in a newsletter prompt. After saving the key in Muse, test the connection using <code>listCustomNewsletters</code>. This read-only check creates no newsletter or post. Return to UpToDate Profile for the active connection confirmation.</li><li>Ask Muse to create your newsletter on the frequency you choose. Before researching each edition, check <code>getNewsletterProcessingStatus</code> for its stable newsletter key. If should_process is false, skip research and delivery; do not switch keys or resubscribe. Otherwise deliver through <code>deliverNewsletterEdition</code>. Include content, original source links and usable photos when available. Keep a paper and its repository within the same story.</li></ol><p>Required fields: newsletter_key, newsletter_name, edition_id, and stories. Each story needs a title and content or source_url. Optional links and images are accepted. Identical retries return the previous delivery; changed content needs a new edition_id.</p><p>Read tools: getNewsletterProcessingStatus, getNewsletterDelivery, listCustomNewsletters. Write tool: deliverNewsletterEdition adds editions to the private feed and starts bounded backend processing.</p><p>Processing is asynchronous. Check getNewsletterDelivery after acceptance; it returns status and post IDs. Existing budgets, verification and image fallbacks apply. Stop processing in Manage rejects future editions immediately. This does not cancel the recurring task inside Muse; its schedule must be cancelled there.</p><p>This private API-key test does not implement OAuth token refresh. Public OAuth account connection remains separate work.</p></html>'''

CONNECT_PAGE='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Connect Muse · UpToDate</title><style>body{font:17px/1.5 system-ui;max-width:480px;margin:40px auto;padding:24px;color-scheme:light dark}input,button{box-sizing:border-box;font:inherit;width:100%;padding:14px 18px;border:1px solid #8886;border-radius:28px;margin:7px 0}button{background:#087cff;color:white;cursor:pointer}small{opacity:.7}#key{font:13px monospace}p{overflow-wrap:anywhere}[hidden]{display:none}</style><h1>Connect Muse</h1><p>Create a private connection for your verified UpToDate account. This beta allows newsletter delivery and delivery-status checks.</p><label>Email<input id="email" type="email" autocomplete="username"></label><label>Password<input id="password" type="password" autocomplete="current-password"></label><button id="connect">Create or replace connection</button><button id="disconnect">Disconnect Muse</button><p id="status" role="status"></p><section id="credential" hidden><label>Private connection key<input id="key" readonly></label><button id="copy">Copy connection key</button><small>Shown only in this session. Add it through Muse’s secure credential setup. Replacing a connection invalidates the previous key. Expires in 30 days.</small></section><script nonce="__NONCE__">const $=id=>document.getElementById(id);let session=null;async function signIn(){const cfg=await fetch('/v1/auth/config',{cache:'no-store'}).then(r=>r.json());const r=await fetch(cfg.url+'/auth/v1/token?grant_type=password',{method:'POST',headers:{'Content-Type':'application/json',apikey:cfg.publishable_key},body:JSON.stringify({email:$('email').value,password:$('password').value})});$('password').value='';const value=await r.json();if(!r.ok)throw Error(value.msg||value.error_description||'Unable to sign in');session=value.access_token;return session}async function action(method){$('status').textContent='Connecting…';$('credential').hidden=true;$('key').value='';try{const token=session||await signIn();const r=await fetch('/v1/me/connectors/muse',{method,headers:{Authorization:'Bearer '+token}});const v=await r.json();if(!r.ok)throw Error(typeof v.detail==='string'?v.detail:'Connection could not be created');if(method==='DELETE')$('status').textContent='Muse is disconnected.';else{$('key').value=v.credential;$('credential').hidden=false;$('status').textContent='Private connection ready.'}}catch(e){session=null;$('status').textContent=e.message}}$('connect').onclick=()=>action('POST');$('disconnect').onclick=()=>action('DELETE');$('copy').onclick=async()=>{try{await navigator.clipboard.writeText($('key').value);$('status').textContent='Connection key copied. Add it through Muse’s secure credential setup.'}catch{$('key').select();$('status').textContent='Select and copy the connection key.'}};</script></html>'''
