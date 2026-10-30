begin;
set local search_path to uptodate_runtime, pg_catalog;
create table if not exists newsletter_confirmations (
 id text primary key, message_id text not null unique,
 user_id text not null references users(id) on delete cascade,
 source_id text references sources(id) on delete set null,
 subject text not null, sender_domain text not null, action_url text not null,
 status text not null, created_at text not null
);
create index if not exists newsletter_confirmations_owner on newsletter_confirmations(user_id,status);
alter table newsletter_confirmations enable row level security;
revoke all on newsletter_confirmations from public, anon, authenticated;
commit;
