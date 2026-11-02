"""Monthly provider allowances, independent of per-account request counts.

These are backend estimates, not a provider invoice. Offsets can account for
usage through other applications sharing the provider billing account.
"""
from dataclasses import dataclass
from datetime import datetime
import json
import os
from zoneinfo import ZoneInfo

from .retrieval import SourceError


@dataclass(frozen=True)
class ResourceRate:
    provider: str
    sku: str
    free_units: int | None
    unit_price_usd: float
    timezone: str

    def period(self, timestamp):
        return datetime.fromtimestamp(timestamp, ZoneInfo(self.timezone)).strftime('%Y-%m')

    def offset(self, period):
        try:
            values = json.loads(os.environ.get('RESOURCE_FREE_USAGE_OFFSETS', '{}'))
            provider = values.get(self.provider, {})
            month = provider.get(period, {})
            value = month.get(self.sku, 0)
            if not isinstance(values, dict) or not isinstance(provider, dict) or not isinstance(month, dict) or type(value) is not int or value < 0:
                raise ValueError()
            return value
        except (ValueError, TypeError, AttributeError):
            raise SourceError('RESOURCE_FREE_USAGE_OFFSETS must map providers, YYYY-MM periods and SKUs to nonnegative request counts') from None


# Global pay-as-you-go pricing, verified against Google's official pricing list.
# Each SKU has its own allowance shared across users, API and worker replicas.
GOOGLE_PLACES_RATES = {
    name: ResourceRate('google_places', name, free, price, 'America/Los_Angeles')
    for name, free, price in (
        ('place_details_ids_only', None, 0),
        ('place_details_pro', 5000, 0.017),
        ('place_details_enterprise', 1000, 0.020),
        ('place_details_enterprise_atmosphere', 1000, 0.025),
        ('text_search_pro', 5000, 0.032),
        ('text_search_enterprise', 1000, 0.035),
        ('text_search_enterprise_atmosphere', 1000, 0.040),
        ('place_photos', 1000, 0.007),
    )
}
