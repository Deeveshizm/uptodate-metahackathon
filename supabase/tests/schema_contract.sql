-- Run as the migration owner in a disposable database; all fixtures roll back.
begin;
create function pg_temp.assert_true(condition boolean, message text) returns void language plpgsql as $$
begin
    if condition is distinct from true then raise exception 'Schema assertion failed: %', message; end if;
end;
$$;
insert into auth.users(id) values ('11111111-1111-4111-8111-111111111111'), ('22222222-2222-4222-8222-222222222222');
select pg_temp.assert_true((select count(*) = 2 from public.profiles), 'Auth creates a profile per account');
select pg_temp.assert_true((select bool_and(alias is null and alias_status = 'unconfigured') from public.profiles), 'No deliverable alias is invented');

insert into public.sources(id,user_id,name,sender,signup_url) values
 ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','11111111-1111-4111-8111-111111111111','Coffee newsletter','web:coffee.example','https://coffee.example/subscribe'),
 ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','22222222-2222-4222-8222-222222222222','Other newsletter','web:other.example','');
insert into public.jobs(id,user_id,source_id,fingerprint,subject,kind,payload) values
 ('cccccccc-cccc-4ccc-8ccc-cccccccccccc','11111111-1111-4111-8111-111111111111','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','one','Coffee generation','generation','{"private":"source text"}'),
 ('dddddddd-dddd-4ddd-8ddd-dddddddddddd','22222222-2222-4222-8222-222222222222','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','two','Other generation','generation','{}');
insert into public.posts(id,user_id,source_id,job_id,title,hook,finding,limitations,original_url,status,reviewed_at,published_date) values
 ('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee','11111111-1111-4111-8111-111111111111','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','cccccccc-cccc-4ccc-8ccc-cccccccccccc','Cafe opening','Coffee hook','Coffee finding','Check original','https://coffee.example/issue','approved',now(),current_date),
 ('ffffffff-ffff-4fff-8fff-ffffffffffff','22222222-2222-4222-8222-222222222222','bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb','dddddddd-dddd-4ddd-8ddd-dddddddddddd','Other opening','Other hook','Other finding','Check original','https://other.example/issue','approved',now(),current_date);
insert into public.posts(id,user_id,source_id,job_id,title,hook,finding,limitations,original_url) values
 ('abababab-abab-4bab-8bab-abababababab','11111111-1111-4111-8111-111111111111','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','cccccccc-cccc-4ccc-8ccc-cccccccccccc','Pending draft','Hook','Finding','Check original','https://coffee.example/pending');
insert into public.documents(user_id,job_id,url,title,content_hash,spans) values
 ('11111111-1111-4111-8111-111111111111','cccccccc-cccc-4ccc-8ccc-cccccccccccc','https://coffee.example/issue','Coffee original','hash','[]');
insert into public.post_assets(id,user_id,post_id,kind,storage_path,byte_size) values
 ('12345678-1234-4234-8234-123456789012','11111111-1111-4111-8111-111111111111','eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee','generated','11111111-1111-4111-8111-111111111111/12345678-1234-4234-8234-123456789012.png',4000);
insert into storage.objects(bucket_id,name) values
 ('post-images','11111111-1111-4111-8111-111111111111/12345678-1234-4234-8234-123456789012.png'),
 ('post-images','22222222-2222-4222-8222-222222222222/unregistered.png'),
 ('post-images','11111111-1111-4111-8111-111111111111/unregistered.png');

select pg_temp.assert_true((select count(*) = 10 from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='public' and c.relkind='r' and c.relrowsecurity), 'RLS enabled on every app table');
select pg_temp.assert_true(not has_table_privilege('anon','public.posts','select'), 'Signed-out clients have no post access');
select pg_temp.assert_true(not has_table_privilege('authenticated','public.posts','insert'), 'Clients cannot fabricate generated posts');
select pg_temp.assert_true(not has_table_privilege('authenticated','public.posts','update'), 'Clients cannot bypass review');
select pg_temp.assert_true(not has_table_privilege('authenticated','public.profiles','delete'), 'Account deletion must go through backend cleanup');
select pg_temp.assert_true(not has_column_privilege('authenticated','public.profiles','alias','update'), 'Clients cannot change receiving alias');
select pg_temp.assert_true(not has_column_privilege('authenticated','public.jobs','payload','select'), 'Private job payload not exposed');
select pg_temp.assert_true(not has_table_privilege('authenticated','public.documents','select'), 'Raw originals are backend-only');
select pg_temp.assert_true(not has_table_privilege('authenticated','public.shares','select'), 'Share-token hashes are backend-only');
select pg_temp.assert_true(not has_table_privilege('authenticated','public.asset_deletions','select'), 'Cleanup queue is backend-only');
select pg_temp.assert_true((select not public and file_size_limit=6000000 from storage.buckets where id='post-images'), 'Image bucket is private and bounded');

set local role authenticated;
set local request.jwt.claim.sub = '11111111-1111-4111-8111-111111111111';
select pg_temp.assert_true((select count(*)=1 from public.profiles), 'Only own profile visible');
select pg_temp.assert_true((select count(*)=1 from public.sources), 'Only own sources visible');
select pg_temp.assert_true((select count(*)=2 from public.posts), 'Only own posts visible');
select pg_temp.assert_true((select count(id)=1 from public.jobs), 'Only own job visible');
select pg_temp.assert_true((select count(*)=1 from storage.objects), 'Only owned, registered image is readable');
update public.profiles set timezone='America/New_York' where id='11111111-1111-4111-8111-111111111111';
update public.sources set muted=true where id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
select pg_temp.assert_true((select muted from public.sources limit 1), 'Profile can hide a source');
insert into public.bookmarks(user_id,post_id) values ('11111111-1111-4111-8111-111111111111','eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee');
insert into public.reads(user_id,post_id) values ('11111111-1111-4111-8111-111111111111','eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee');
select pg_temp.assert_true((select count(*)=1 from public.bookmarks), 'Approved post can be saved');
select pg_temp.assert_true((select count(*)=1 from public.reads), 'Approved post can be marked read');
do $$ begin
    begin
        insert into public.bookmarks(user_id,post_id) values ('11111111-1111-4111-8111-111111111111','ffffffff-ffff-4fff-8fff-ffffffffffff');
        raise exception 'Cross-account bookmark was accepted';
    exception when insufficient_privilege then null; end;
    begin
        insert into public.bookmarks(user_id,post_id) values ('11111111-1111-4111-8111-111111111111','abababab-abab-4bab-8bab-abababababab');
        raise exception 'Pending draft bookmark was accepted';
    exception when insufficient_privilege then null; end;
    begin
        update public.profiles set timezone='Invented/Timezone';
        raise exception 'Invalid timezone was accepted';
    exception when check_violation then null; end;
    begin
        update public.posts set status='approved';
        raise exception 'Client approval bypass was accepted';
    exception when insufficient_privilege then null; end;
end; $$;

reset role;
set local role service_role;
insert into public.documents(user_id,job_id,url,title,content_hash,spans) values
 ('11111111-1111-4111-8111-111111111111','cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  'https://coffee.example/' || repeat('a',3000),'Long source URL','long-url-hash','[]');
reset role;
do $$ begin
    begin
        update public.posts set rights='public' where id='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
        raise exception 'Public rights accepted without permission record';
    exception when check_violation then null; end;
    begin
        update public.posts set status='approved',published_date=current_date where id='abababab-abab-4bab-8bab-abababababab';
        raise exception 'Approval accepted without review timestamp';
    exception when check_violation then null; end;
    begin
        update public.posts set source_id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' where id='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
        raise exception 'Cross-account source relation was accepted';
    exception when foreign_key_violation then null; end;
end; $$;

-- Automatic private feed placement is not human review or public permission.
update public.posts set status='approved',published_date=current_date,
    generation='ollama:test',verification='machine_checked',
    analysis='{"publication":{"mode":"automatic_private","human_reviewed":false}}'::jsonb
    where id='abababab-abab-4bab-8bab-abababababab';
select pg_temp.assert_true((select reviewed_at is null and rights='private' and status='approved'
    from public.posts where id='abababab-abab-4bab-8bab-abababababab'), 'Private feed does not fabricate human approval');
do $$ begin
    begin
        update public.posts set rights='public',public_permission_at=now()
            where id='abababab-abab-4bab-8bab-abababababab';
        raise exception 'Automatic private placement bypassed public human review';
    exception when check_violation then null; end;
end; $$;

-- Deleting a profile cascades its private records, but preserves durable object cleanup.
delete from auth.users where id='11111111-1111-4111-8111-111111111111';
select pg_temp.assert_true((select count(*)=1 from public.profiles), 'Other account survives deletion');
select pg_temp.assert_true((select count(*)=1 from public.sources), 'Own sources removed');
select pg_temp.assert_true((select count(*)=1 from public.posts), 'Own posts removed');
select pg_temp.assert_true((select count(*)=1 from public.jobs), 'Own jobs removed');
select pg_temp.assert_true((select count(*)=0 from public.documents), 'Own evidence removed');
select pg_temp.assert_true((select count(*)=0 from public.bookmarks), 'Own saved state removed');
select pg_temp.assert_true((select count(*)=0 from public.reads), 'Own read state removed');
select pg_temp.assert_true((select count(*)=1 from public.asset_deletions), 'Image cleanup survives account deletion');
set local role authenticated;
set local request.jwt.claim.sub = '11111111-1111-4111-8111-111111111111';
select pg_temp.assert_true((select count(*)=0 from storage.objects), 'Deleted account image immediately becomes unreadable');
reset role;
rollback;
