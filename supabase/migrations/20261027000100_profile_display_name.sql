-- Apply after the existing schema; inspect the hosted project's state first.
alter table public.profiles add column display_name text not null default ''
    check (char_length(display_name) <= 100);
grant update (display_name) on public.profiles to authenticated;
-- Alias and alias_status remain server-controlled. Existing owner RLS applies.
