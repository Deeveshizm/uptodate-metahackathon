-- Only for a disposable local PostgreSQL cluster. Never run on a Supabase project.
create role anon nologin;
create role authenticated nologin;
create role service_role nologin bypassrls;
create schema auth;
create schema storage;
create table auth.users (id uuid primary key);
create function auth.uid() returns uuid language sql stable as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;
create table storage.buckets (
    id text primary key, name text not null, public boolean not null default false,
    file_size_limit bigint, allowed_mime_types text[]
);
create table storage.objects (
    id uuid primary key default gen_random_uuid(), bucket_id text not null references storage.buckets(id),
    name text not null, unique (bucket_id, name)
);
alter table storage.objects enable row level security;
create function storage.foldername(name text) returns text[] language sql immutable as $$
    select string_to_array(regexp_replace(name, '/[^/]+$', ''), '/');
$$;
grant usage on schema public, auth, storage to anon, authenticated, service_role;
grant select on storage.objects to authenticated;
-- Exercise Supabase installations that still grant tables broadly by default.
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
