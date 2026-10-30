"""Give existing Resend accounts a Cloudflare primary; keep legacy delivery and content."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.cloud_settings import configure
from backend.relay_aliases import migrate_resend_accounts, purge_orphaned_aliases
from backend.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default=str(ROOT/'.env.local'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    values = configure(args.env_file)
    if values.get('CLOUDFLARE_RECEIVING_ENABLED') != '1':
        parser.error('Enable and verify Cloudflare receiving first')
    if not values.get('RESEND_API_KEY') or not values.get('RESEND_RECEIVING_DOMAIN'):
        parser.error('Keep the legacy Resend receiving provider configured during migration')
    from backend.cloudflare_receiving import receiving_domain
    domain = receiving_domain()
    if not domain or not values.get('DATABASE_URL'):
        parser.error('Configure the receiving domain and hosted database first')
    store=Store(values['DATABASE_URL'])
    result=migrate_resend_accounts(store, domain, values['RESEND_RECEIVING_DOMAIN'], apply=args.apply)
    if args.apply:result['unowned_aliases_removed']=purge_orphaned_aliases(store)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
