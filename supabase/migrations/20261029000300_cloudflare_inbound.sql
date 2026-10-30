begin;
set local search_path to uptodate_runtime, pg_catalog;
create table if not exists cloudflare_inbound (
 id text primary key,
 user_id text not null references users(id) on delete cascade,
 recipient text not null,
 raw bytea not null,
 created_at text not null
);
create index if not exists cloudflare_inbound_user on cloudflare_inbound(user_id);
create index if not exists cloudflare_inbound_created on cloudflare_inbound(created_at);
alter table cloudflare_inbound enable row level security;
revoke all on cloudflare_inbound from public, anon, authenticated;
commit;
