-- Additive management lifecycle; source identities remain for existing posts.
begin;
set local search_path to uptodate_runtime, pg_catalog;
alter table sources add column if not exists source_category text not null default '';
alter table sources add column if not exists unsubscribed_at text;
alter table sources add column if not exists archived_at text;
create index if not exists runtime_sources_retention on sources(subscription_status,unsubscribed_at) where archived_at is null;
commit;
