begin;
set local search_path to uptodate_runtime, pg_catalog;
create table if not exists revoked_relay_aliases (
 digest text primary key, revoked_at text not null
);
alter table revoked_relay_aliases enable row level security;
revoke all on revoked_relay_aliases from public, anon, authenticated;
commit;
