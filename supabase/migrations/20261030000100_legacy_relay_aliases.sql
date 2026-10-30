begin;
set local search_path to uptodate_runtime, pg_catalog;
create table if not exists legacy_relay_aliases (
 address text primary key references test_relay_aliases(address) on delete cascade,
 user_id text references users(id) on delete set null,
 created_at text not null
);
create index if not exists legacy_relay_aliases_owner on legacy_relay_aliases(user_id);
alter table legacy_relay_aliases enable row level security;
revoke all on legacy_relay_aliases from public, anon, authenticated;
commit;
