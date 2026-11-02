"""Read-only backend usage report. Never prints credentials or newsletter data."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from backend.resource_usage import GOOGLE_PLACES_RATES
from scripts.cloud_settings import configure


def report(store):
    now = datetime.now(timezone.utc).timestamp()
    day = int(now // 86400) * 86400
    with store.connect() as db:
        total = db.execute('SELECT COALESCE(sum(amount_usd),0) FROM model_spend WHERE created_at>=?', (day,)).fetchone()[0]
        result = {'estimated_billable_usd_today': total, 'resource_usage': []}
        for rate in GOOGLE_PLACES_RATES.values():
            period = rate.period(now)
            used = db.execute('SELECT COALESCE(sum(units),0) FROM resource_usage WHERE provider=? AND sku=? AND period=?',
                              (rate.provider, rate.sku, period)).fetchone()[0]
            external = rate.offset(period)
            cost = db.execute('SELECT COALESCE(sum(s.amount_usd),0) FROM resource_usage r JOIN model_spend s ON s.id=r.id WHERE r.provider=? AND r.sku=? AND r.period=?',
                              (rate.provider, rate.sku, period)).fetchone()[0]
            result['resource_usage'].append({'provider': rate.provider, 'sku': rate.sku, 'period': period,
                'tracked_requests': used, 'external_usage_offset': external, 'free_monthly_cap': rate.free_units,
                'free_remaining': max(0, rate.free_units-used-external) if rate.free_units is not None else None,
                'estimated_billable_usd': cost})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('.env.local'))
    args = parser.parse_args()
    values = configure(args.env_file)
    from backend.store import Store
    path = values.get('DATABASE_URL') or values.get('NEWSLETTER_DB', 'data/prototype.sqlite3')
    if path.startswith(('postgres://', 'postgresql://')):
        os.environ['UPTODATE_ENV'] = 'cloud'
    print(json.dumps(report(Store(path)), indent=2))


if __name__ == '__main__':
    main()
