"""Upload the email-only Worker. Never prints credentials or modifies DNS/rules."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cloud_settings import load_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env.local')
    parser.add_argument('--backend-origin', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    values = load_settings(ROOT / args.env_file)
    account = values.get('CLOUDFLARE_ACCOUNT_ID', '')
    token = values.get('CLOUDFLARE_API_TOKEN', '')
    secret = values.get('CLOUDFLARE_INGEST_SECRET', '')
    domain = values.get('CLOUDFLARE_RECEIVING_DOMAIN', '')
    if not re.fullmatch(r'[a-f0-9]{32}', account) or not token or len(secret) < 32 or not domain:
        parser.error('Save CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, CLOUDFLARE_INGEST_SECRET and CLOUDFLARE_RECEIVING_DOMAIN in the ignored settings file')
    origin = urlsplit(args.backend_origin)
    if origin.scheme != 'https' or not origin.hostname or not origin.hostname.endswith('.run.app') or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ('', '/'):
        parser.error('Use the hosted HTTPS Cloud Run API origin')
    if not args.apply:
        print('Dry run: would deploy uptodate-email-receiver with a secret binding. Routing rules remain unchanged.')
        return
    metadata = {'main_module': 'email-worker.mjs', 'compatibility_date': '2026-10-29',
                'bindings': [{'name': 'BACKEND_ORIGIN', 'type': 'plain_text', 'text': args.backend_origin},
                             {'name': 'RECEIVING_DOMAIN', 'type': 'plain_text', 'text': domain},
                             {'name': 'INGEST_SECRET', 'type': 'secret_text', 'text': secret}]}
    files = {'metadata': ('metadata.json', json.dumps(metadata), 'application/json'),
             'email-worker.mjs': ('email-worker.mjs', (Path(__file__).parent/'email-worker.mjs').read_bytes(), 'application/javascript+module')}
    try:
        with httpx.Client(timeout=60, trust_env=False, follow_redirects=False) as client:
            response = client.put(f'https://api.cloudflare.com/client/v4/accounts/{account}/workers/scripts/uptodate-email-receiver',
                                  headers={'Authorization': 'Bearer ' + token}, files=files)
        if not response.is_success or response.json().get('success') is not True:
            raise RuntimeError(f'Worker deployment failed (HTTP {response.status_code}); check token account and Workers Scripts Edit permission')
    except (httpx.HTTPError, ValueError):
        raise RuntimeError('Cannot reach Cloudflare or read its deployment response; no email routing rules changed') from None
    print('Deployed uptodate-email-receiver. Next select it as the Email Routing catch-all Worker. No personal inbox forwarding was created.')


if __name__ == '__main__':
    main()
