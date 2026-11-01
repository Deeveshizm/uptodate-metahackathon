"""Small public product page served by the existing application deployment."""
import html
import os
import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse


WEB = Path(__file__).with_name('web')
HEADERS = {
    'Cache-Control': 'public,max-age=300',
    'Content-Security-Policy': "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
}


def install(app):
    def enabled():
        if os.environ.get('PUBLIC_POLICIES_ENABLED', '0') != '1':
            raise HTTPException(404, 'Not found')

    @app.api_route('/', methods=['GET', 'HEAD'], include_in_schema=False)
    def home():
        enabled()
        email = os.environ.get('PUBLIC_SUPPORT_EMAIL', '').strip()
        operator = os.environ.get('PUBLIC_DEVELOPER_NAME', '').strip()
        if not operator or len(operator) > 120 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email):
            raise HTTPException(503, 'Public contact information is not configured')
        content = (WEB / 'index.html').read_text(encoding='utf-8')
        content = content.replace('__CONTACT__', html.escape(email, quote=True))
        content = content.replace('__OPERATOR__', html.escape(operator))
        return HTMLResponse(content, headers=HEADERS)

    @app.api_route('/website/{asset}', methods=['GET', 'HEAD'], include_in_schema=False)
    def asset(asset: str):
        enabled()
        media = {'style.css': 'text/css', 'icon.svg': 'image/svg+xml'}
        if asset not in media:
            raise HTTPException(404, 'Not found')
        return FileResponse(WEB / asset, media_type=media[asset], headers=HEADERS)
