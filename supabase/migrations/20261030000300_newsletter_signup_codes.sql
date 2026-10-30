begin;
set local search_path to uptodate_runtime, pg_catalog;
alter table sources add column if not exists setup_expires_at text;
alter table newsletter_confirmations add column if not exists kind text not null default 'link';
alter table newsletter_confirmations add column if not exists verification_code text not null default '';
alter table newsletter_confirmations add column if not exists expires_at text;
commit;
