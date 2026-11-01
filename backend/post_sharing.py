"""Explicit, unlisted post snapshots. Source-only sharing remains a legacy path."""
import html
import json
import os
from pathlib import Path
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .pipeline import safe_url

TOKEN = re.compile(r'[A-Za-z0-9_-]{32}')
ASSET = re.compile(r'[a-f0-9]{16,64}')
HEADERS = {'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff', 'X-Robots-Tag': 'noindex, nofollow, noarchive'}
WEB = Path(__file__).with_name('web')


def public_link(value):
    value = safe_url(value) if isinstance(value, str) else ''
    if not value: return ''
    if any(re.search(r'token|secret|signature|password|email|auth|api.?key', key, re.I)
           for key, _ in parse_qsl(urlsplit(value).query)):
        return ''
    return value


def action_link(value):
    if isinstance(value, str) and re.fullmatch(r'tel:\+?[0-9][0-9 ()\.\-]{2,30}', value): return value
    return public_link(value)


def snapshot(post):
    analysis = post.get('analysis') or {}
    visual = post.get('visual') or {}
    gallery = post.get('visuals') or ([visual] if visual.get('asset_id') else [])
    images, seen = [], set()
    for item in gallery:
        if not isinstance(item, dict): continue
        identifier = item.get('asset_id', '')
        if not isinstance(identifier, str) or not ASSET.fullmatch(identifier) or identifier in seen: continue
        seen.add(identifier)
        images.append({key: item.get(key) for key in ('asset_id', 'kind', 'caption', 'attribution', 'provider')})
        if len(images) == 8: break
    paper = post.get('paper') or {}
    action = post.get('content_action') or {}
    recipe = action.get('data') if action.get('kind') == 'ingredients' else {}
    sections = [{'heading': str(item.get('heading', '')), 'text': str(item.get('text', ''))}
                for item in analysis.get('sections', []) if isinstance(item, dict)]
    if not sections and analysis.get('content_type') == 'paper':
        sections = [{'heading': label, 'text': analysis[key]} for key, label in
                    (('problem', 'Research question'), ('approach', 'Method'), ('impact', 'Why it matters'))
                    if isinstance(analysis.get(key), str) and analysis[key].strip()]
    return {**{key: post.get(key) for key in ('title', 'hook', 'finding', 'limitations', 'published_date', 'is_demo')},
        'source_name': (post.get('source') or {}).get('name', ''),
        'original_url': public_link(post.get('original_url')),
        'sections': sections,
        'cooking_steps': [item for item in analysis.get('cooking_steps', []) if isinstance(item, str)],
        'ingredients': [item for item in (recipe or {}).get('ingredients', []) if isinstance(item, str)] if isinstance(recipe, dict) else [],
        'abstract': paper.get('abstract') if isinstance(paper.get('abstract'), str) else '',
        'venue_facts': {key: value for key, value in (analysis.get('venue_facts') or {}).items()
                        if key in ('rating', 'hours') and isinstance(value, str)},
        'has_live_venue': bool(analysis.get('live_venue')),
        'links': [{'label': str(item.get('label') or item.get('kind') or 'Source'), 'url': action_link(item.get('url')),
                   'kind': item.get('kind')} for item in post.get('link_actions', []) if isinstance(item, dict) and action_link(item.get('url'))],
        'images': images}


def paragraphs(value):
    if not isinstance(value, str) or not value.strip(): return ''
    result = []
    for paragraph in re.split(r'\n\s*\n', value.strip()):
        lines = paragraph.splitlines()
        if lines and all(re.match(r'^\s*(?:[-*•]|\d+[.)])\s+', line) for line in lines):
            numbered = bool(re.match(r'^\s*\d', lines[0]))
            tag = 'ol' if numbered else 'ul'
            items = ''.join('<li>' + html.escape(re.sub(r'^\s*(?:[-*•]|\d+[.)])\s+', '', line)) + '</li>' for line in lines)
            result.append('<' + tag + '>' + items + '</' + tag + '>')
        else:
            result.append('<p>' + html.escape(paragraph) + '</p>')
    return ''.join(result)


def render(data, token, install_url):
    e = lambda value: html.escape(str(value or ''), quote=True)
    body = '<h1>' + e(data.get('title')) + '</h1>'
    if data.get('hook'): body += '<p class="intro">' + e(data['hook']) + '</p>'
    if data.get('images'):
        body += '<div class="gallery" aria-label="Post images">'
        for image in data['images']:
            image_url = '/p/' + token + '/images/' + image['asset_id']
            body += '<figure data-image="' + image_url + '" data-google="' + str(image.get('provider') == 'google_places').lower() + '">'
            body += '<div class="photo"><img hidden alt="' + e(image.get('caption') or data.get('title')) + '"><span class="image-status">Loading image…</span></div>'
            if image.get('kind') == 'generated': body += '<figcaption>AI illustration</figcaption>'
            elif image.get('attribution'): body += '<figcaption>' + e(image['attribution']) + '</figcaption>'
            body += '<div class="photo-credit"></div></figure>'
        body += '</div>'
        if len(data['images']) > 1: body += '<p class="gallery-hint">Swipe to see more images</p>'
    body += paragraphs(data.get('finding'))
    if data.get('ingredients'):
        body += '<h2>Ingredients</h2><ul>' + ''.join('<li>' + e(item) + '</li>' for item in data['ingredients']) + '</ul>'
    if data.get('cooking_steps'):
        body += '<h2>Instructions</h2><ol>' + ''.join('<li>' + e(item) + '</li>' for item in data['cooking_steps']) + '</ol>'
    for section in data.get('sections', []): body += '<h2>' + e(section['heading']) + '</h2>' + paragraphs(section['text'])
    if data.get('abstract'): body += '<details open><summary>Abstract</summary>' + paragraphs(data['abstract']) + '</details>'
    if data.get('limitations'): body += paragraphs(data['limitations'])
    facts = data.get('venue_facts') or {}
    if facts:
        body += '<dl class="facts">' + ''.join('<dt>' + key.title() + '</dt><dd>' + e(value) + '</dd>' for key, value in facts.items()) + '</dl>'
    if data.get('has_live_venue'): body += '<section class="live-venue" data-venue="/p/' + token + '/venue" hidden></section>'
    links = []
    if data.get('original_url'): links.append(('Original source', data['original_url']))
    links += [(item['label'], item['url']) for item in data.get('links', []) if item.get('url')]
    used = set()
    body += '<nav class="source-links" aria-label="Post sources">'
    for label, url in links:
        if url in used: continue
        used.add(url)
        body += '<a href="' + e(url) + '" rel="noreferrer noopener">' + e(label) + ' ↗</a>'
    body += '</nav><p class="byline">From ' + e(data.get('source_name')) + '</p>'
    page = (WEB / 'shared-post.html').read_text(encoding='utf-8')
    return page.replace('__TITLE__', e(data.get('title'))).replace('__DESCRIPTION__', e((data.get('hook') or data.get('finding') or '')[:180])).replace('__BODY__', body).replace('__INSTALL__', e(install_url))


class SharedPosts:
    def __init__(self, store, get_post, post_json, image_response, venue_details):
        self.store, self.get_post, self.post_json = store, get_post, post_json
        self.image_response, self.venue_details = image_response, venue_details

    def create(self, user, post_id, request):
        origin = os.environ.get('PUBLIC_SITE_ORIGIN', '').rstrip('/')
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise HTTPException(503, 'Public sharing origin is not configured correctly')
        else:
            from .muse_connector import public_origin
            origin = public_origin(request)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            post = self.get_post(db, user, post_id)
            if post['status'] != 'approved': raise HTTPException(409, 'This post is still being prepared')
            data = snapshot(self.post_json(db, post, user))
            old = db.execute('SELECT token FROM post_share_pages WHERE post_id=? AND user_id=?', (post_id, user['id'])).fetchone()
            token = old['token'] if old else secrets.token_urlsafe(24)
            encoded = json.dumps(data)
            if old: db.execute('UPDATE post_share_pages SET snapshot=? WHERE token=?', (encoded, token))
            else: db.execute('INSERT INTO post_share_pages(token,post_id,user_id,snapshot,created_at) VALUES(?,?,?,?,?)',
                            (token, post_id, user['id'], encoded, datetime.now(timezone.utc).isoformat()))
        return {'url': origin + '/p/' + token, 'mode': 'shared-post', 'local_only': urlsplit(origin).hostname in ('localhost', '127.0.0.1', 'testserver')}

    def read(self, token):
        if not TOKEN.fullmatch(token): raise HTTPException(404, 'Shared post is unavailable')
        with self.store.connect() as db:
            row = db.execute("SELECT s.snapshot,p.* FROM post_share_pages s JOIN posts p ON p.id=s.post_id AND p.user_id=s.user_id WHERE s.token=? AND p.status='approved'", (token,)).fetchone()
        if not row: raise HTTPException(404, 'Shared post is unavailable')
        return row, json.loads(row['snapshot'])

    def install(self, app, current_user):
        @app.api_route('/p/{token}', methods=['GET', 'HEAD'], include_in_schema=False)
        def page(token: str):
            _, data = self.read(token)
            install = public_link(os.environ.get('APP_INSTALL_URL', '')) or '/'
            return HTMLResponse(render(data, token, install), headers={**HEADERS,
                'Content-Security-Policy': "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})

        @app.api_route('/p/{token}/images/{asset_id}', methods=['GET', 'HEAD'], include_in_schema=False)
        def image(token: str, asset_id: str, request: Request):
            post, data = self.read(token)
            if not ASSET.fullmatch(asset_id) or not any(item['asset_id'] == asset_id for item in data.get('images', [])):
                raise HTTPException(404, 'Image is unavailable')
            with self.store.connect() as db:
                asset = db.execute('SELECT * FROM gallery_assets WHERE id=? AND artifact_key=?', (asset_id, post['artifact_key'])).fetchone()
                if not asset: asset = db.execute('SELECT * FROM post_assets WHERE id=? AND post_id=?', (asset_id, post['id'])).fetchone()
            if not asset: raise HTTPException(404, 'Image is unavailable')
            response = self.image_response(asset, post, request)
            response.headers.update(HEADERS)
            return response

        @app.get('/p/{token}/venue', include_in_schema=False)
        def venue(token: str):
            post, data = self.read(token)
            if not data.get('has_live_venue'): return JSONResponse({'fields': {}, 'sources': {}}, headers=HEADERS)
            result = self.venue_details(post['id'], user={'id': post['user_id']})
            value = json.loads(result.body)
            # Fetch display details live; never persist Google venue content in
            # a shared snapshot or expose the private source/account envelope.
            fields = {key: item for key, item in value.get('fields', {}).items()
                      if key in ('website', 'phone', 'location', 'rating', 'hours') and isinstance(item, str)}
            return JSONResponse({'fields': fields, 'source_url': public_link(value.get('source_url'))}, headers=HEADERS)

        @app.delete('/v1/posts/{post_id}/share', status_code=204)
        def revoke(post_id: str, user=Depends(current_user)):
            with self.store.connect() as db:
                self.get_post(db, user, post_id)
                db.execute('DELETE FROM post_share_pages WHERE post_id=? AND user_id=?', (post_id, user['id']))

        @app.api_route('/share-assets/{asset}', methods=['GET', 'HEAD'], include_in_schema=False)
        def asset(asset: str):
            media = {'share.css': 'text/css', 'share.js': 'application/javascript', 'icon.svg': 'image/svg+xml'}
            if asset not in media: raise HTTPException(404, 'Not found')
            return FileResponse(WEB / asset, media_type=media[asset], headers={**HEADERS, 'Cache-Control': 'public,max-age=300'})
