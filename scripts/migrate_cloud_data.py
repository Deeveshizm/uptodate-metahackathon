"""Copy an immutable SQLite backup into the server-only hosted runtime schema.

No Auth/profile table is changed. Dry run is the default. Re-runs preserve IDs;
the app, bookmarks, relay aliases and existing subscriptions retain ownership.
"""
import argparse
from pathlib import Path
import os
import sqlite3
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg

from scripts.cloud_settings import configure

ROOT = Path(__file__).resolve().parents[1]
TABLES = ['users','sources','jobs','generation_artifacts','posts','bookmarks','reads','shares',
          'documents','test_relay_aliases','test_relay_receipts','source_mail_actions','inbox_candidates',
          'venue_photo_searches','post_sources','model_spend','post_assets','gallery_assets']


def migration_url(value):
    parts=urlsplit(value)
    if parts.hostname and parts.hostname.endswith('.pooler.supabase.com') and parts.port==6543:
        # DDL uses the same pooler's session endpoint, never a guessed hostname.
        return urlunsplit(parts._replace(netloc=parts.netloc.rsplit(':',1)[0]+':5432'))
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',default='.env.local')
    parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--schema-only',action='store_true')
    args=parser.parse_args()
    values=configure(args.env_file)
    url=values.get('DATABASE_URL','')
    if not url.startswith(('postgres://','postgresql://')):
        parser.error('Save DATABASE_URL in the ignored settings file first')
    if not args.apply:
        print('Dry run: would apply the private runtime schema and copy the supplied snapshot. No hosted data changed.')
        return
    with psycopg.connect(values.get('DATABASE_MIGRATION_URL') or migration_url(url),sslmode='require',prepare_threshold=None,autocommit=True) as target:
        exists=target.execute("SELECT to_regclass('uptodate_runtime.schema_version')").fetchone()[0]
        if not exists:
            target.execute((ROOT/'supabase/migrations/20261028000100_cloud_runtime.sql').read_text())
        else:
            version=target.execute('SELECT version FROM uptodate_runtime.schema_version WHERE id=1').fetchone()
            if not version or version[0]!=1:raise RuntimeError('Unexpected runtime schema version')
        target.execute((ROOT/'supabase/migrations/20261029000100_source_lifecycle.sql').read_text())
        target.execute((ROOT/'supabase/migrations/20261029000200_profile_avatars.sql').read_text())
        target.execute((ROOT/'supabase/migrations/20261029000300_cloudflare_inbound.sql').read_text())
        target.execute((ROOT/'supabase/migrations/20261029000400_newsletter_confirmations.sql').read_text())
        target.execute((ROOT/'supabase/migrations/20261030000100_legacy_relay_aliases.sql').read_text())
        target.execute((ROOT/'supabase/migrations/20261030000200_revoked_relay_aliases.sql').read_text())
    headers={'apikey':values['SUPABASE_SECRET_KEY'],'Authorization':'Bearer '+values['SUPABASE_SECRET_KEY']}
    bucket=values.get('POST_IMAGE_BUCKET','post-images')
    origin=values['SUPABASE_URL'].rstrip('/')
    with httpx.Client(timeout=30,trust_env=False) as client:
        response=client.get(origin+'/storage/v1/bucket/'+bucket,headers=headers)
        if response.status_code in (400,404) and response.json().get('error') in ('not_found','Not Found'):
            response=client.post(origin+'/storage/v1/bucket',headers=headers,json={'id':bucket,'name':bucket,'public':False,'file_size_limit':6000000,'allowed_mime_types':['image/png']})
            response.raise_for_status()
        elif response.status_code==200:
            if response.json().get('public') is not False:raise RuntimeError('Post-image bucket must be private')
        else:
            raise RuntimeError('Could not verify the private image bucket')
    if args.schema_only:
        print('Private runtime schema and image bucket verified. Existing Auth/profiles untouched.')
        return
    if not args.snapshot or not args.snapshot.is_file():parser.error('Supply --snapshot with an immutable SQLite backup')
    os.environ['UPTODATE_ENV']='cloud'
    from backend.postgres_store import PostgresStore
    from backend.assets import AssetStore
    store=PostgresStore(url)
    assets=AssetStore(store)
    source=sqlite3.connect('file:'+str(args.snapshot.resolve())+'?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    try:
        for table in TABLES:
            count=0
            primary=[row[1] for row in sorted(source.execute('PRAGMA table_info('+table+')'),key=lambda row:row[5]) if row[5]]
            for row in source.execute('SELECT * FROM '+table):
                data=dict(row)
                if table=='jobs' and data['status']=='processing':
                    data.update(status='failed',stage='interrupted',error='Local job was interrupted by migration; retry explicitly')
                with store.connect() as db:
                    if table=='post_assets':
                        assets.save_post(db,data['id'],data['post_id'],data['image'])
                    elif table=='gallery_assets':
                        assets.save_gallery(db,data['id'],data['artifact_key'],data['image'])
                    else:
                        columns=list(data)
                        placeholders=','.join('?' for _ in columns)
                        # Column identifiers come exclusively from our own source schema.
                        identifiers=','.join('"'+name.replace('"','""')+'"' for name in columns)
                        db.execute(f'INSERT INTO {table}({identifiers}) VALUES({placeholders}) ON CONFLICT DO NOTHING',tuple(data.values()))
                    where=' AND '.join('"'+name+'"=?' for name in primary)
                    saved=db.execute('SELECT * FROM '+table+' WHERE '+where,tuple(data[name] for name in primary)).fetchone()
                    if not saved:
                        raise RuntimeError('Cloud identity verification failed for '+table)
                    if table in ('post_assets','gallery_assets'):
                        if assets.read(saved)!=data['image']:
                            raise RuntimeError('Cloud image verification failed')
                    elif any(saved[key]!=value for key,value in data.items()):
                        raise RuntimeError('Cloud field verification failed for '+table)
                count+=1
            print(f'Copied {table}: {count} rows',flush=True)
        with store.connect() as db:
            for table in TABLES:
                count=source.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                copied=db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                if copied<count:raise RuntimeError('Cloud row verification failed for '+table)
        print('Cloud runtime copy complete. No passwords, session tokens, or hosted Auth records changed.')
    finally:
        source.close()


if __name__=='__main__':
    main()
