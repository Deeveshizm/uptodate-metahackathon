"""Publish public GCP pages on the app's domain, without changing email rules."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cloud_settings import load_settings

WORKER = 'uptodate-website'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env.local')
    parser.add_argument('--domain', required=True)
    parser.add_argument('--backend-origin', required=True)
    parser.add_argument('--replace-existing-web-records', action='store_true',
                        help='Back up and replace conflicting apex A/AAAA/CNAME records')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    values = load_settings(ROOT / args.env_file)
    account = values.get('CLOUDFLARE_ACCOUNT_ID', '')
    token = values.get('CLOUDFLARE_API_TOKEN', '')
    if not re.fullmatch(r'[a-f0-9]{32}', account) or not token:
        parser.error('Save the Cloudflare account ID and API token in ignored local settings')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', args.domain) or '.' not in args.domain:
        parser.error('Use a domain name without a scheme or path')
    origin = urlsplit(args.backend_origin)
    if origin.scheme != 'https' or not origin.hostname or not origin.hostname.endswith('.run.app') or origin.username or origin.password or origin.port or origin.query or origin.fragment or origin.path not in ('', '/'):
        parser.error('Use the hosted HTTPS Cloud Run API origin')
    with httpx.Client(base_url='https://api.cloudflare.com/client/v4', timeout=60,
                      trust_env=False, follow_redirects=False,
                      headers={'Authorization': 'Bearer ' + token}) as client:
        def request(method, path, **kwargs):
            try:
                response = client.request(method, path, **kwargs)
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise SystemExit('Cannot read Cloudflare response; credentials were not printed') from None
            if not response.is_success or data.get('success') is not True:
                errors = '; '.join(str(e.get('code')) + ': ' + str(e.get('message', ''))[:240]
                                   for e in data.get('errors', []) if isinstance(e, dict))
                raise SystemExit(f'Cloudflare operation failed (HTTP {response.status_code}): {errors}')
            return data.get('result')
        zones = request('GET', '/zones', params={'name': args.domain, 'account.id': account})
        if len(zones) != 1 or zones[0]['name'] != args.domain or zones[0].get('status') != 'active':
            raise SystemExit('Expected one active domain in the configured account; nothing changed')
        zone = zones[0]['id']
        domains = request('GET', f'/accounts/{account}/workers/domains')
        for domain in domains:
            if domain['hostname'] == args.domain and domain['service'] != WORKER:
                raise SystemExit('Domain already belongs to another website Worker; nothing changed')
        already_attached = any(item['hostname'] == args.domain and item['service'] == WORKER for item in domains)
        records = request('GET', f'/zones/{zone}/dns_records', params={'name': args.domain, 'per_page': 100})
        conflicts = [] if already_attached else [item for item in records
            if item['name'] == args.domain and item['type'] in ('A', 'AAAA', 'CNAME')]
        email_path = f'/zones/{zone}/email/routing/rules/catch_all'
        before = request('GET', email_path)
        if not args.apply:
            print(f'Dry run: upload {WORKER} and attach https://{args.domain}. Conflicting web records: {len(conflicts)}. Email routing remains unchanged.')
            return
        if conflicts and not args.replace_existing_web_records:
            raise SystemExit('Existing apex website records found. Review them, then use --replace-existing-web-records for the intended cutover; nothing changed.')
        metadata = {'main_module': 'website-worker.mjs', 'compatibility_date': '2026-11-01',
                    'bindings': [{'name': 'BACKEND_ORIGIN', 'type': 'plain_text', 'text': args.backend_origin},
                                 {'name': 'PUBLIC_DOMAIN', 'type': 'plain_text', 'text': args.domain}]}
        request('PUT', f'/accounts/{account}/workers/scripts/{WORKER}', files={
            'metadata': ('metadata.json', json.dumps(metadata), 'application/json'),
            'website-worker.mjs': ('website-worker.mjs', (Path(__file__).parent / 'website-worker.mjs').read_bytes(), 'application/javascript+module'),
        })
        print(f'Deployed {WORKER}', flush=True)
        if conflicts:
            backup = ROOT / '.build' / 'cloudflare-website' / (datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '.json')
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open('x', encoding='utf-8') as file:
                file.write(json.dumps({'domain': args.domain, 'zone_id': zone, 'web_records': conflicts}, indent=2) + '\n')
            backup.chmod(0o600)
            print('Saved website DNS backup: ' + str(backup.relative_to(ROOT)), flush=True)
        removed = []
        try:
            for record in conflicts:
                request('DELETE', f'/zones/{zone}/dns_records/{record["id"]}')
                removed.append(record)
            request('PUT', f'/accounts/{account}/workers/domains', json={
                'hostname': args.domain, 'service': WORKER, 'zone_id': zone,
                'environment': 'production', 'enabled': True, 'previews_enabled': False,
            })
        except (Exception, SystemExit):
            for record in removed:
                restore = {key: record[key] for key in ('type', 'name', 'content', 'ttl', 'proxied', 'comment', 'tags', 'settings') if key in record}
                request('POST', f'/zones/{zone}/dns_records', json=restore)
            if removed:
                print('Domain attachment failed; previous website DNS restored.', flush=True)
            raise
        after = request('GET', email_path)
        if any(before.get(key) != after.get(key) for key in ['enabled', 'actions', 'matchers']):
            raise SystemExit('Email routing verification changed unexpectedly; inspect the catch-all rule')
        attached = request('GET', f'/accounts/{account}/workers/domains')
        if not any(item['hostname'] == args.domain and item['service'] == WORKER for item in attached):
            raise SystemExit('Website domain attachment could not be verified')
        print(f'Attached https://{args.domain}. Existing email catch-all verified unchanged. Check public HTTPS before using the URL.')


if __name__ == '__main__':
    main()
