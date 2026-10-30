"""Inspect or enable this app's catch-all Worker rule; never forwards to an inbox."""
import argparse
from pathlib import Path
import re
import sys

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cloud_settings import load_settings

WORKER = 'uptodate-email-receiver'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env.local')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    values = load_settings(ROOT / args.env_file)
    account = values.get('CLOUDFLARE_ACCOUNT_ID', '')
    token = values.get('CLOUDFLARE_API_TOKEN', '')
    domain = values.get('CLOUDFLARE_RECEIVING_DOMAIN', '')
    if not re.fullmatch(r'[a-f0-9]{32}', account) or not token or not domain:
        parser.error('Save the Cloudflare Account ID, API token and receiving domain first')
    with httpx.Client(base_url='https://api.cloudflare.com/client/v4', timeout=30, trust_env=False,
                      follow_redirects=False, headers={'Authorization': 'Bearer ' + token}) as client:
        def request(method, path, **kwargs):
            try:
                response = client.request(method, path, **kwargs)
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise SystemExit('Cannot read Cloudflare response; credentials were not printed.') from None
            if response.status_code != 200 or data.get('success') is not True:
                codes = [str(e.get('code')) for e in data.get('errors', []) if isinstance(e, dict)]
                raise SystemExit(f'Cloudflare operation failed: HTTP {response.status_code}; codes '+','.join(codes))
            return data.get('result')
        zones = request('GET', '/zones', params={'name': domain, 'account.id': account})
        if len(zones) != 1 or zones[0]['name'] != domain or zones[0].get('status') != 'active':
            raise SystemExit('Exactly one active domain in the configured account is required; nothing changed.')
        zone = zones[0]['id']
        bindings = request('GET', f'/accounts/{account}/workers/scripts/{WORKER}/settings').get('bindings', [])
        if not any(b.get('name') == 'RECEIVING_DOMAIN' and b.get('text') == domain for b in bindings):
            raise SystemExit('Worker domain does not match; nothing changed.')
        settings = request('GET', f'/zones/{zone}/email/routing')
        print('Domain:', domain)
        print('Email Routing enabled:', settings.get('enabled'))
        print('Email Routing status:', settings.get('status'))
        path = f'/zones/{zone}/email/routing/rules/catch_all'
        current = request('GET', path)
        print('Catch-all enabled:', current.get('enabled'))
        print('Catch-all action types:', ','.join(a.get('type', '') for a in current.get('actions', [])))
        desired = {'enabled': True, 'name': 'UpToDate account aliases', 'matchers': [{'type': 'all'}],
                   'actions': [{'type': 'worker', 'value': [WORKER]}]}
        correct = current.get('enabled') is True and current.get('actions') == desired['actions']
        print('Desired route:', '*@' + domain + ' -> Worker ' + WORKER)
        if correct:
            print('Catch-all already matches; nothing changed.')
        elif not args.apply:
            print('Dry run only; use --apply to set this one catch-all rule.')
        else:
            if settings.get('enabled') is not True:
                raise SystemExit('Enable Email Routing for this domain in Cloudflare first; nothing changed.')
            request('PUT', path, json=desired)
            result = request('GET', path)
            if result.get('enabled') is not True or result.get('actions') != desired['actions']:
                raise SystemExit('Routing verification failed; inspect the catch-all rule in Cloudflare.')
            print('Verified catch-all routes to the app Worker. No personal forwarding destination configured.')


if __name__ == '__main__':
    main()
