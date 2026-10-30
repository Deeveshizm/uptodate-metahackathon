begin;
set local search_path to uptodate_runtime, pg_catalog;
create table if not exists profile_avatars (
 user_id text primary key references users(id) on delete cascade,
 revision text not null,
 image bytea not null,
 content_type text not null,
 storage_path text
);
alter table profile_avatars enable row level security;
revoke all on profile_avatars from public, anon, authenticated;
commit;
