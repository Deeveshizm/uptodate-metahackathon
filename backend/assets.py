"""Private image storage; only owner-checked API routes return image bytes."""
import hashlib
import os
import re
import time
from urllib.parse import urlsplit

import httpx

from .retrieval import SourceError


class AssetStore:
    def __init__(self, store, transport=None):
        self.store, self.transport = store, transport
        self.remote = os.environ.get('UPTODATE_ENV') == 'cloud'
        self.url = os.environ.get('SUPABASE_URL', '').rstrip('/')
        self.key = os.environ.get('SUPABASE_SECRET_KEY', '')
        self.bucket = os.environ.get('POST_IMAGE_BUCKET', 'post-images')
        if self.remote and (urlsplit(self.url).scheme != 'https' or not self.key or
                            not re.fullmatch(r'[a-z0-9-]{1,63}', self.bucket)):
            raise ValueError('Cloud images require Supabase HTTPS storage and a backend secret')

    def _request(self, method, path, **kwargs):
        try:
            with httpx.Client(timeout=30, trust_env=False, transport=self.transport) as client:
                headers = {'apikey': self.key, 'Authorization': 'Bearer ' + self.key}
                if 'content' in kwargs:
                    headers['Content-Type'] = 'image/png'
                return client.request(method, self.url + '/storage/v1/' + path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise SourceError('Private image storage is temporarily unavailable') from None

    def upload(self, db, image):
        if not image or len(image) > 6_000_000:
            raise SourceError('Image exceeds the private storage size limit')
        path = 'runtime/' + hashlib.sha256(image).hexdigest() + '.png'
        # Register before upload so interrupted transactions leave a collectible
        # object, never an untracked permanent upload. The migration importer and
        # worker maintenance also reconcile unreferenced objects.
        with self.store.connect() as registry:
            registry.execute('INSERT INTO media_objects(storage_path,created_at) VALUES(?,?) ON CONFLICT(storage_path) DO UPDATE SET created_at=excluded.created_at', (path, time.time()))
        response = self._request('POST', 'object/' + self.bucket + '/' + path, content=image)
        if response.status_code not in (200, 201, 409):
            # Storage can report immutable-object duplicates as HTTP 400.
            try:
                duplicate = response.status_code == 400 and response.json().get('error') == 'Duplicate'
            except ValueError:
                duplicate = False
            if not duplicate:
                raise SourceError('Could not save the private post image')
        return path

    def save_gallery(self, db, asset_id, artifact_key, image):
        if self.remote:
            path = self.upload(db, image)
            db.execute('INSERT INTO gallery_assets(id,artifact_key,image,content_type,storage_path) VALUES(?,?,?, ?,?) ON CONFLICT(id) DO NOTHING',
                       (asset_id, artifact_key, b'', 'image/png', path))
        else:
            db.execute("INSERT OR IGNORE INTO gallery_assets(id,artifact_key,image,content_type) VALUES(?,?,?,'image/png')", (asset_id, artifact_key, image))

    def save_post(self, db, asset_id, post_id, image):
        if self.remote:
            path = self.upload(db, image)
            db.execute("INSERT INTO post_assets(id,post_id,image,content_type,storage_path) VALUES(?,?,?,'image/png',?) ON CONFLICT(post_id) DO UPDATE SET id=excluded.id,image=excluded.image,content_type=excluded.content_type,storage_path=excluded.storage_path", (asset_id, post_id, b'', path))
        else:
            db.execute("INSERT INTO post_assets(id,post_id,image,content_type) VALUES(?,?,?,'image/png') ON CONFLICT(post_id) DO UPDATE SET id=excluded.id,image=excluded.image,content_type=excluded.content_type", (asset_id, post_id, image))

    def read(self, row):
        path = row['storage_path'] if 'storage_path' in row.keys() else None
        if not path:
            return bytes(row['image'])
        if not self.remote or not re.fullmatch(r'runtime/[a-f0-9]{64}\.png', path):
            raise SourceError('Private post image is unavailable')
        try:
            with httpx.Client(timeout=30, trust_env=False, transport=self.transport) as client:
                with client.stream('GET', self.url + '/storage/v1/object/authenticated/' + self.bucket + '/' + path,
                                   headers={'apikey': self.key, 'Authorization': 'Bearer ' + self.key}) as response:
                    if response.status_code != 200:
                        raise SourceError('Private post image is temporarily unavailable')
                    image = bytearray()
                    for chunk in response.iter_bytes():
                        image.extend(chunk)
                        if len(image) > 6_000_000:
                            raise SourceError('Private image exceeds the size limit')
            if hashlib.sha256(image).hexdigest() != path.removeprefix('runtime/').removesuffix('.png'):
                raise SourceError('Private image failed its integrity check')
            return bytes(image)
        except httpx.HTTPError:
            raise SourceError('Private image download is temporarily unavailable') from None

    def collect(self, limit=20):
        if not self.remote:
            return 0
        removed = 0
        with self.store.connect() as db:
            # A day of grace prevents races with in-flight uploads/publications.
            paths = db.execute('SELECT storage_path FROM media_objects m WHERE created_at<? AND NOT EXISTS(SELECT 1 FROM post_assets p WHERE p.storage_path=m.storage_path) AND NOT EXISTS(SELECT 1 FROM gallery_assets g WHERE g.storage_path=m.storage_path) AND NOT EXISTS(SELECT 1 FROM profile_avatars a WHERE a.storage_path=m.storage_path) LIMIT ? FOR UPDATE OF m SKIP LOCKED', (time.time() - 86400, limit)).fetchall()
            for item in paths:
                response = self._request('DELETE', 'object/' + self.bucket, json={'prefixes': [item['storage_path']]})
                if response.status_code == 200:
                    db.execute('DELETE FROM media_objects WHERE storage_path=?', (item['storage_path'],))
                    removed += 1
        return removed
