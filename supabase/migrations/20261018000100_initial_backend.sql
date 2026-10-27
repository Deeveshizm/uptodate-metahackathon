-- UpToDate: reviewed against feat/home-card-trim at 0667085ca7509d9bfe71913069ee625b15cad912.
-- Auth owns identity; the backend alone writes ingestion, generation, and review state.
create schema if not exists uptodate_private;
revoke all on schema uptodate_private from public, anon, authenticated;

-- Server encoding is fixed for a database; these deployments require UTF-8.
do $$ begin
    if current_setting('server_encoding') <> 'UTF8' then
        raise exception 'UpToDate requires a UTF8 database';
    end if;
end; $$;
create function uptodate_private.url_hash(value text) returns bytea
language sql immutable strict set search_path = '' as $$
    select pg_catalog.sha256(pg_catalog.convert_to(value, 'UTF8'));
$$;
revoke all on function uptodate_private.url_hash(text) from public, anon, authenticated;
grant usage on schema uptodate_private to service_role;
grant execute on function uptodate_private.url_hash(text) to service_role;

create table public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    alias text unique,
    alias_status text not null default 'unconfigured'
        check (alias_status in ('unconfigured', 'active', 'disabled')),
    timezone text not null default 'UTC',
    created_at timestamptz not null default now(),
    check (alias is null or alias ~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$'),
    check (alias_status <> 'active' or (alias is not null and lower(alias) not like '%.invalid'))
);

create table public.sources (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.profiles(id) on delete cascade,
    name text not null,
    sender text not null,
    url text not null default '',
    signup_url text not null default '',
    feed_url text not null default '',
    muted boolean not null default false,
    subscription_status text not null default 'not_requested' check (subscription_status in (
        'not_requested', 'queued', 'needs_inbox', 'needs_agent', 'submitted',
        'confirmation_required', 'subscribed', 'manual_action', 'failed', 'unsubscribed'
    )),
    subscription_detail text not null default '',
    created_at timestamptz not null default now(),
    unique (user_id, sender),
    unique (id, user_id)
);

create table public.jobs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.profiles(id) on delete cascade,
    source_id uuid not null,
    fingerprint text not null,
    subject text not null,
    kind text not null check (kind in ('email', 'newsletter', 'generation', 'visual')),
    status text not null default 'queued' check (status in ('queued', 'processing', 'review', 'completed', 'failed', 'cancelled')),
    stage text not null default '',
    error text,
    payload jsonb,
    result jsonb not null default '{}'::jsonb check (jsonb_typeof(result) = 'object'),
    attempts integer not null default 0 check (attempts >= 0),
    worker_id text,
    locked_until timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz not null default now(),
    unique (user_id, fingerprint),
    unique (id, user_id),
    unique (id, source_id, user_id),
    foreign key (source_id, user_id) references public.sources(id, user_id) on delete cascade
);

create table public.documents (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.profiles(id) on delete cascade,
    job_id uuid not null,
    url text not null,
    url_hash bytea generated always as (uptodate_private.url_hash(url)) stored,
    title text not null,
    content_hash text not null,
    spans jsonb not null check (jsonb_typeof(spans) = 'array'),
    metadata jsonb not null default '{}'::jsonb check (jsonb_typeof(metadata) = 'object'),
    created_at timestamptz not null default now(),
    unique (job_id, url_hash, content_hash),
    foreign key (job_id, user_id) references public.jobs(id, user_id) on delete cascade
);

create table public.posts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.profiles(id) on delete cascade,
    source_id uuid not null,
    job_id uuid not null,
    title text not null,
    hook text not null,
    finding text not null,
    limitations text not null,
    original_url text not null,
    original_url_hash bytea generated always as (uptodate_private.url_hash(original_url)) stored,
    evidence jsonb not null default '[]'::jsonb check (jsonb_typeof(evidence) = 'array'),
    analysis jsonb not null default '{}'::jsonb check (jsonb_typeof(analysis) = 'object'),
    visual jsonb not null default '{}'::jsonb check (jsonb_typeof(visual) = 'object'),
    verification text not null default 'manual_required' check (verification in ('manual_required', 'machine_checked')),
    rights text not null default 'private' check (rights in ('private', 'public')),
    status text not null default 'review' check (status in ('review', 'approved', 'rejected')),
    generation text not null default 'extractive',
    is_demo boolean not null default false,
    reviewed_at timestamptz,
    public_permission_at timestamptz,
    published_date date,
    created_at timestamptz not null default now(),
    unique (job_id, original_url_hash),
    unique (id, user_id),
    foreign key (source_id, user_id) references public.sources(id, user_id) on delete cascade,
    foreign key (job_id, source_id, user_id) references public.jobs(id, source_id, user_id) on delete cascade,
    check (status <> 'approved' or (published_date is not null and (
        reviewed_at is not null or (
            rights = 'private'
            and coalesce(analysis #>> '{publication,mode}' = 'automatic_private'
                         and analysis #>> '{publication,human_reviewed}' = 'false', false)
            and (generation = 'extractive' or (generation like 'ollama:%' and verification = 'machine_checked'))
        )
    ))),
    check (rights <> 'public' or public_permission_at is not null)
);

create table public.bookmarks (
    user_id uuid not null references public.profiles(id) on delete cascade,
    post_id uuid not null,
    created_at timestamptz not null default now(),
    primary key (user_id, post_id),
    foreign key (post_id, user_id) references public.posts(id, user_id) on delete cascade
);

create table public.reads (
    user_id uuid not null references public.profiles(id) on delete cascade,
    post_id uuid not null,
    created_at timestamptz not null default now(),
    primary key (user_id, post_id),
    foreign key (post_id, user_id) references public.posts(id, user_id) on delete cascade
);

create table public.shares (
    token_hash text primary key check (token_hash ~ '^[a-f0-9]{64}$'),
    user_id uuid not null references public.profiles(id) on delete cascade,
    post_id uuid not null,
    created_at timestamptz not null default now(),
    expires_at timestamptz,
    revoked_at timestamptz,
    foreign key (post_id, user_id) references public.posts(id, user_id) on delete cascade
);

create table public.post_assets (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.profiles(id) on delete cascade,
    post_id uuid not null unique,
    kind text not null check (kind in ('source', 'generated')),
    bucket_id text not null default 'post-images' check (bucket_id = 'post-images'),
    storage_path text not null unique,
    content_type text not null default 'image/png' check (content_type = 'image/png'),
    byte_size bigint not null check (byte_size between 1 and 6000000),
    created_at timestamptz not null default now(),
    foreign key (post_id, user_id) references public.posts(id, user_id) on delete cascade,
    check (storage_path = user_id::text || '/' || id::text || '.png')
);

-- Survives parent/account deletion so a worker can remove objects through Storage's API.
create table public.asset_deletions (
    bucket_id text not null check (bucket_id = 'post-images'),
    storage_path text not null,
    attempts integer not null default 0 check (attempts >= 0),
    error text,
    created_at timestamptz not null default now(),
    primary key (bucket_id, storage_path)
);

create index sources_owner on public.sources(user_id);
create index jobs_owner_created on public.jobs(user_id, created_at desc);
create index jobs_source_owner on public.jobs(source_id, user_id);
create index jobs_claimable on public.jobs(status, locked_until, created_at) where status in ('queued', 'processing');
create index documents_owner_url on public.documents(user_id, url_hash, content_hash);
create index posts_daily_feed on public.posts(user_id, published_date, created_at, id) where status = 'approved';
create index posts_review on public.posts(user_id, created_at) where status = 'review';
create index posts_source_owner on public.posts(source_id, user_id);
create index posts_job_owner on public.posts(job_id, source_id, user_id);
create index bookmarks_post on public.bookmarks(post_id, user_id);
create index reads_post on public.reads(post_id, user_id);
create index shares_post on public.shares(post_id, user_id);
create index shares_owner on public.shares(user_id);
create index post_assets_owner on public.post_assets(user_id);

create function uptodate_private.validate_timezone() returns trigger
language plpgsql set search_path = '' as $$
begin
    if not exists (select 1 from pg_catalog.pg_timezone_names where name = new.timezone) then
        raise exception 'Unknown account timezone' using errcode = '23514';
    end if;
    return new;
end;
$$;
create trigger uptodate_profile_timezone before insert or update of timezone on public.profiles
for each row execute function uptodate_private.validate_timezone();

create function uptodate_private.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
    insert into public.profiles(id) values (new.id);
    return new;
end;
$$;
create trigger uptodate_auth_user_created after insert on auth.users
for each row execute function uptodate_private.handle_new_user();
insert into public.profiles(id) select id from auth.users;

create function uptodate_private.queue_asset_deletion() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
    insert into public.asset_deletions(bucket_id, storage_path)
    values (old.bucket_id, old.storage_path) on conflict do nothing;
    return old;
end;
$$;
create trigger uptodate_asset_deleted after delete on public.post_assets
for each row execute function uptodate_private.queue_asset_deletion();
create trigger uptodate_asset_replaced after update of storage_path, bucket_id on public.post_assets
for each row when (old.storage_path is distinct from new.storage_path or old.bucket_id is distinct from new.bucket_id)
execute function uptodate_private.queue_asset_deletion();
revoke all on function uptodate_private.validate_timezone(), uptodate_private.handle_new_user(),
    uptodate_private.queue_asset_deletion() from public, anon, authenticated;

-- Supabase may give new public tables broad default grants; narrow only our tables.
revoke all on public.profiles, public.sources, public.jobs, public.documents, public.posts,
    public.bookmarks, public.reads, public.shares, public.post_assets, public.asset_deletions
    from public, anon, authenticated;
grant all on public.profiles, public.sources, public.jobs, public.documents, public.posts,
    public.bookmarks, public.reads, public.shares, public.post_assets, public.asset_deletions to service_role;
grant select on public.profiles, public.sources, public.posts, public.bookmarks, public.reads, public.post_assets to authenticated;
grant update (timezone) on public.profiles to authenticated;
grant update (signup_url, muted) on public.sources to authenticated;
grant select (id, user_id, source_id, fingerprint, subject, kind, status, stage, error, result,
    attempts, started_at, finished_at, created_at) on public.jobs to authenticated;
grant insert, delete on public.bookmarks to authenticated;
grant insert on public.reads to authenticated;

alter table public.profiles enable row level security;
alter table public.sources enable row level security;
alter table public.jobs enable row level security;
alter table public.documents enable row level security;
alter table public.posts enable row level security;
alter table public.bookmarks enable row level security;
alter table public.reads enable row level security;
alter table public.shares enable row level security;
alter table public.post_assets enable row level security;
alter table public.asset_deletions enable row level security;

create policy profiles_read_own on public.profiles for select to authenticated using (id = (select auth.uid()));
create policy profiles_update_own on public.profiles for update to authenticated
    using (id = (select auth.uid())) with check (id = (select auth.uid()));
create policy sources_read_own on public.sources for select to authenticated using (user_id = (select auth.uid()));
create policy sources_update_own on public.sources for update to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy jobs_read_own on public.jobs for select to authenticated using (user_id = (select auth.uid()));
create policy posts_read_own on public.posts for select to authenticated using (user_id = (select auth.uid()));
create policy assets_read_own on public.post_assets for select to authenticated using (user_id = (select auth.uid()));
create policy bookmarks_read_own on public.bookmarks for select to authenticated using (user_id = (select auth.uid()));
create policy bookmarks_delete_own on public.bookmarks for delete to authenticated using (user_id = (select auth.uid()));
create policy bookmarks_insert_approved_own on public.bookmarks for insert to authenticated
    with check (user_id = (select auth.uid()) and exists (
        select 1 from public.posts p where p.id = post_id and p.user_id = (select auth.uid()) and p.status = 'approved'
    ));
create policy reads_read_own on public.reads for select to authenticated using (user_id = (select auth.uid()));
create policy reads_insert_approved_own on public.reads for insert to authenticated
    with check (user_id = (select auth.uid()) and exists (
        select 1 from public.posts p where p.id = post_id and p.user_id = (select auth.uid()) and p.status = 'approved'
    ));

insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
values ('post-images', 'post-images', false, 6000000, array['image/png'])
on conflict (id) do nothing;
do $$
begin
    if exists (select 1 from storage.buckets where id = 'post-images' and public) then
        raise exception 'Existing post-images bucket is public; inspect before applying this migration';
    end if;
end;
$$;

-- Only the backend uploads, replaces, or deletes generated/source art.
create policy uptodate_read_post_images on storage.objects for select to authenticated
using (bucket_id = 'post-images' and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (select 1 from public.post_assets a
                where a.user_id = (select auth.uid()) and a.bucket_id = storage.objects.bucket_id
                    and a.storage_path = storage.objects.name));
