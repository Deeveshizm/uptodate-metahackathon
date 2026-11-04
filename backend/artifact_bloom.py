"""Advisory, database-fed Bloom prechecks for generation artifact fingerprints.

Workers keep small local filters and refresh them from the shared artifact
index. A stale/partial filter must never authorize generation: the caller
still needs an atomic database claim before invoking a producer.
"""
import hashlib
import logging
import math
import os
import threading
import time

logger = logging.getLogger(__name__)


class BloomFilter:
    def __init__(self, capacity=100_000, false_positive_rate=0.01):
        if capacity < 1 or not 0 < false_positive_rate < 1:
            raise ValueError('Invalid Bloom filter capacity or false-positive rate')
        self.capacity = capacity
        size = math.ceil(-capacity * math.log(false_positive_rate) / math.log(2) ** 2)
        self.bits = bytearray((size + 7) // 8)
        self.bit_count = len(self.bits) * 8
        self.hash_count = max(1, round(self.bit_count / capacity * math.log(2)))

    def positions(self, key):
        digest = hashlib.blake2b(key.encode(), digest_size=16).digest()
        first = int.from_bytes(digest[:8], 'big')
        step = int.from_bytes(digest[8:], 'big') | 1
        return ((first + index * step) % self.bit_count for index in range(self.hash_count))

    def add(self, key):
        for position in self.positions(key):
            self.bits[position // 8] |= 1 << (position % 8)

    def might_contain(self, key):
        return all(self.bits[position // 8] & (1 << (position % 8)) for position in self.positions(key))


class ArtifactBloom:
    def __init__(self, store, *, capacity=100_000, interval=30, batch_size=4096, clock=time.monotonic):
        self.store = store
        self.filter = BloomFilter(capacity)
        self.interval, self.batch_size, self.clock = interval, batch_size, clock
        self.enabled = os.environ.get('ARTIFACT_BLOOM_ENABLED', '1') == '1'
        self.lock = threading.RLock()
        self.sync_lock = threading.Lock()
        self.ready = False
        self.next_refresh = 0
        self.cursor = (-1.0, '')
        self.watermark = -1.0
        self.stats = {'checks': 0, 'negative_checks': 0, 'positive_checks': 0,
                      'fallback_checks': 0, 'sync_queries': 0, 'sync_failures': 0}

    def refresh(self):
        if not self.sync_lock.acquire(blocking=False):
            return
        try:
            now = self.clock()
            with self.lock:
                if now < self.next_refresh:
                    return
                stamp, key = self.cursor
            try:
                with self.store.connect() as db:
                    rows = db.execute('SELECT cache_key,updated_at FROM generation_artifacts '
                        'WHERE updated_at>? OR (updated_at=? AND cache_key>?) '
                        'ORDER BY updated_at,cache_key LIMIT ?',
                        (stamp, stamp, key, self.batch_size)).fetchall()
                with self.lock:
                    self.stats['sync_queries'] += 1
                    for row in rows:
                        self.filter.add(row['cache_key'])
                    if rows:
                        last = rows[-1]
                        self.watermark = max(self.watermark, last['updated_at'])
                    if len(rows) == self.batch_size:
                        # Continue a bounded backfill on the next check. Until
                        # the initial scan completes, every check uses SQL.
                        self.cursor = (rows[-1]['updated_at'], rows[-1]['cache_key'])
                        self.next_refresh = now
                    else:
                        # Re-read a short overlap to tolerate commits whose
                        # timestamp was assigned before another transaction.
                        self.cursor = (max(-1.0, self.watermark - 60), '')
                        self.next_refresh = now + self.interval
                        self.ready = True
            except Exception:
                with self.lock:
                    self.ready = False
                    self.next_refresh = now + self.interval
                    self.stats['sync_failures'] += 1
                logger.warning('Artifact Bloom refresh unavailable; using exact database lookup')
        finally:
            self.sync_lock.release()

    def might_contain(self, key):
        if self.enabled:
            self.refresh()
        with self.lock:
            self.stats['checks'] += 1
            if not self.enabled or not self.ready:
                self.stats['fallback_checks'] += 1
                return True
            result = self.filter.might_contain(key)
            self.stats['positive_checks' if result else 'negative_checks'] += 1
            return result

    def add(self, key):
        if self.enabled:
            with self.lock:
                self.filter.add(key)

    def diagnostics(self):
        with self.lock:
            return {**self.stats, 'ready': self.ready, 'enabled': self.enabled,
                    'bytes': len(self.filter.bits), 'hashes': self.filter.hash_count}
