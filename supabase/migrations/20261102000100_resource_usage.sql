-- Provider request counters contain no fetched content or credentials.
BEGIN;
SET LOCAL search_path TO uptodate_runtime,pg_catalog;
CREATE TABLE IF NOT EXISTS resource_usage (
 id TEXT PRIMARY KEY REFERENCES model_spend(id) ON DELETE CASCADE,
 provider TEXT NOT NULL, sku TEXT NOT NULL, period TEXT NOT NULL,
 units INTEGER NOT NULL CHECK(units>0), free_cap INTEGER,
 unit_price_usd DOUBLE PRECISION NOT NULL, created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS resource_usage_period ON resource_usage(provider,sku,period);
ALTER TABLE resource_usage ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON resource_usage FROM public,anon,authenticated;
COMMIT;
