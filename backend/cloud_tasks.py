"""Transactional job outbox → named Cloud Tasks; no email or content in tasks."""
import hashlib
import logging
import os
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


@contextmanager
def service_lease(store, name):
    import uuid
    token = uuid.uuid4().hex
    with store.connect() as db:
        acquired = db.execute('INSERT INTO service_leases(name,token,lease_until) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET token=excluded.token,lease_until=excluded.lease_until WHERE service_leases.lease_until<?', (name, token, time.time() + 300, time.time())).rowcount
    try:
        yield bool(acquired)
    finally:
        if acquired:
            with store.connect() as db:
                db.execute('DELETE FROM service_leases WHERE name=? AND token=?', (name, token))


class CloudDispatcher:
    def __init__(self, store, client=None):
        from google.cloud import tasks_v2
        self.store = store
        self.client = client or tasks_v2.CloudTasksClient()
        self.project = os.environ['GOOGLE_CLOUD_PROJECT']
        self.location = os.environ.get('CLOUD_TASKS_LOCATION', 'us-east4')
        self.queue = os.environ.get('CLOUD_TASKS_QUEUE', 'uptodate-generation')
        self.worker = os.environ.get('CLOUD_WORKER_URL', '').rstrip('/')
        self.identity = os.environ['CLOUD_TASKS_SERVICE_ACCOUNT']
        parsed = urlsplit(self.worker)
        if self.worker and (parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.run.app')
                            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('CLOUD_WORKER_URL must be the HTTPS Cloud Run worker origin')
        self.parent = self.client.queue_path(self.project, self.location, self.queue)

    def enqueue_pending(self, limit=20):
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import tasks_v2
        from google.protobuf.duration_pb2 import Duration
        if not self.worker:
            raise RuntimeError('Configure CLOUD_WORKER_URL before accepting jobs')
        dispatched = 0
        # One delivery per transaction: crashes between task creation and commit
        # are safe because the same deterministic task name is reused.
        for _ in range(limit):
            with self.store.connect() as db:
                item = db.execute("SELECT d.job_id,d.revision FROM job_deliveries d JOIN jobs j ON j.id=d.job_id WHERE d.enqueued_at=0 AND d.created_at<=? AND j.status='queued' ORDER BY d.created_at LIMIT 1 FOR UPDATE OF d SKIP LOCKED", (time.time(),)).fetchone()
                if not item:
                    break
                digest = hashlib.sha256((item['job_id'] + ':' + item['revision']).encode()).hexdigest()
                task = {'name': self.parent + '/tasks/' + digest,
                        'dispatch_deadline': Duration(seconds=1800),
                        'http_request': {'http_method': tasks_v2.HttpMethod.POST,
                            'url': self.worker + '/internal/jobs/' + item['job_id'],
                            'oidc_token': {'service_account_email': self.identity, 'audience': self.worker}}}
                try:
                    self.client.create_task(request={'parent': self.parent, 'task': task}, retry=None, timeout=10)
                except AlreadyExists:
                    pass
                db.execute('UPDATE job_deliveries SET enqueued_at=? WHERE job_id=? AND revision=?', (time.time(), item['job_id'], item['revision']))
                dispatched += 1
        return dispatched

    def safe_dispatch(self):
        try:
            return self.enqueue_pending()
        except Exception:
            # Outbox records survive. Scheduler maintenance retries delivery.
            logger.warning('Task dispatch deferred; durable outbox retained')
            return 0

    def enqueue_receiving(self, delivery_id=None):
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import tasks_v2
        from google.protobuf.duration_pb2 import Duration
        # Webhook deliveries each wake processing immediately, even when a
        # previous receiving task already completed within the same minute.
        digest = hashlib.sha256(('receiving:' + str(delivery_id or int(time.time() // 60))).encode()).hexdigest()
        task = {'name': self.parent + '/tasks/' + digest,
                'dispatch_deadline': Duration(seconds=300),
                'http_request': {'http_method': tasks_v2.HttpMethod.POST,
                    'url': self.worker + '/internal/receiving',
                    'oidc_token': {'service_account_email': self.identity, 'audience': self.worker}}}
        try:
            self.client.create_task(request={'parent': self.parent, 'task': task}, retry=None, timeout=10)
        except AlreadyExists:
            pass

    def execute(self, job_id, process):
        # Cloud Tasks retries delivery, but duplicate delivery cannot repeat
        # active or finished work. Interrupted paid calls require an explicit
        # user retry rather than automatically spending again.
        import uuid
        token = uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job or job['status'] != 'queued':
                return 'already_handled'
            active = db.execute('SELECT lease_until FROM job_runs WHERE job_id=?', (job_id,)).fetchone()
            if active and active['lease_until'] > time.time():
                return 'already_running'
            db.execute('INSERT INTO job_runs(job_id,token,lease_until) VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET token=excluded.token,lease_until=excluded.lease_until', (job_id, token, time.time() + 1860))
        try:
            process(job_id)
        except Exception:
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',stage='interrupted',error='Processing was interrupted; retry explicitly' WHERE id=? AND status='processing'", (job_id,))
            raise
        finally:
            with self.store.connect() as db:
                # A successful parent may have created separately queued images.
                db.execute('DELETE FROM job_runs WHERE job_id=? AND token=?', (job_id, token))
                db.execute('UPDATE job_deliveries SET created_at=? WHERE job_id=? AND enqueued_at=0', (time.time() + 60, job_id))
            self.safe_dispatch()
        return 'processed'

    def recover(self):
        with self.store.connect() as db:
            stale = db.execute("SELECT j.id FROM jobs j JOIN job_runs r ON r.job_id=j.id WHERE j.status='processing' AND r.lease_until<?", (time.time(),)).fetchall()
            for job in stale:
                db.execute("UPDATE jobs SET status='failed',stage='interrupted',error='Processing was interrupted; retry explicitly to avoid duplicate model charges' WHERE id=? AND status='processing'", (job['id'],))
            db.execute('DELETE FROM job_runs WHERE lease_until<?', (time.time(),))
        return len(stale)
