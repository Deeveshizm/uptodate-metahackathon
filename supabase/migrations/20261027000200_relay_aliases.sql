-- One stable, random receiving address per verified account. No forwarding.
-- Receiving-domain configuration is operator-only; the default is disabled.
create table uptodate_private.relay_settings (
    singleton boolean primary key default true check (singleton),
    receiving_domain text,
    enabled boolean not null default false,
    receiving_verified_at timestamptz,
    check (receiving_domain is null or (
        char_length(receiving_domain) <= 253
        and receiving_domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
        and receiving_domain !~ '\.(invalid|localhost|local)$'
    )),
    check (not enabled or receiving_domain is not null),
    check (receiving_verified_at is null or (enabled and receiving_domain is not null))
);
insert into uptodate_private.relay_settings(singleton) values (true);

-- Retain revoked addresses after account deletion so an address is never reused.
create table uptodate_private.relay_aliases (
    address text primary key,
    user_id uuid unique references auth.users(id) on delete set null,
    created_at timestamptz not null default now()
);
revoke all on uptodate_private.relay_settings, uptodate_private.relay_aliases from public, anon, authenticated;
grant all on uptodate_private.relay_settings, uptodate_private.relay_aliases to service_role;

alter table public.profiles drop constraint profiles_alias_status_check;
alter table public.profiles add constraint profiles_alias_status_check
    check (alias_status in ('unconfigured', 'provisioning', 'active', 'disabled'));

create function uptodate_private.protect_relay_domain() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
    if new.receiving_domain is distinct from old.receiving_domain
       and exists (select 1 from uptodate_private.relay_aliases) then
        raise exception 'Relay addresses already exist; changing the domain requires an explicit migration';
    end if;
    return new;
end;
$$;
revoke all on function uptodate_private.protect_relay_domain() from public, anon, authenticated;
create trigger protect_relay_domain before update on uptodate_private.relay_settings
    for each row execute function uptodate_private.protect_relay_domain();

create function public.ensure_relay_alias() returns setof public.profiles
language plpgsql security definer set search_path = '' as $$
declare
    owner_id uuid := auth.uid();
    account public.profiles%rowtype;
    settings uptodate_private.relay_settings%rowtype;
    address text;
begin
    if owner_id is null or not exists (
        select 1 from auth.users u where u.id = owner_id
            and u.email_confirmed_at is not null and not coalesce(u.is_anonymous, false)
    ) then
        raise exception 'A verified account is required' using errcode = '42501';
    end if;
    -- Serializes simultaneous first requests for this account.
    select * into account from public.profiles where id = owner_id for update;
    if not found then raise exception 'Account profile is missing'; end if;
    select * into settings from uptodate_private.relay_settings where singleton;
    if not settings.enabled or account.alias_status = 'disabled' then
        return next account;
        return;
    end if;
    if account.alias is null then
        -- No main email, display name, account UUID or other identity in the alias.
        loop
            address := 'n_' || replace(pg_catalog.gen_random_uuid()::text, '-', '') || '@' || settings.receiving_domain;
            begin
                insert into uptodate_private.relay_aliases(address, user_id) values (address, owner_id);
                exit;
            exception when unique_violation then
                select r.address into address from uptodate_private.relay_aliases r where r.user_id = owner_id;
                if found then exit; end if;
            end;
        end loop;
        update public.profiles set alias = address,
            alias_status = case when settings.receiving_verified_at is null then 'provisioning' else 'active' end
            where id = owner_id returning * into account;
    elsif account.alias_status = 'provisioning' and settings.receiving_verified_at is not null then
        update public.profiles set alias_status = 'active' where id = owner_id returning * into account;
    end if;
    return next account;
end;
$$;
revoke all on function public.ensure_relay_alias() from public, anon;
grant execute on function public.ensure_relay_alias() to authenticated;

-- The operator enables the domain after configuring the receiving provider.
-- Alias assignment supports both a provider-managed domain and our own domain.
-- A delivery test
-- must pass before setting receiving_verified_at and activating subscriptions.
notify pgrst, 'reload schema';
