"""Atomic paid-call reservations and monthly free-resource request accounting."""
import os
import time
import uuid

from .retrieval import SourceError


class JobBudget:
    def __init__(self, store, job_id, purpose='generation'):
        if purpose not in ('generation','image_read','venue_read'):raise ValueError('Unknown spending purpose')
        self.store, self.job_id = store, job_id
        self.purpose=purpose

    @staticmethod
    def limit(name, default):
        try:
            amount = float(os.environ.get(name, default))
            if not 0 < amount <= 1000:
                raise ValueError()
            return amount
        except ValueError:
            raise SourceError(name + " must be a positive dollar amount up to 1000") from None

    def _job(self, db):
        job = db.execute("SELECT j.user_id,j.kind,s.subscription_status FROM jobs j JOIN sources s ON s.id=j.source_id AND s.user_id=j.user_id WHERE j.id=?", (self.job_id,)).fetchone()
        if not job:
            raise SourceError("Account or generation job no longer exists; no inference sent")
        if job['kind']=='connector_edition':
            from .muse_connector import ensure_job_allowed
            ensure_job_allowed(db,self.job_id)
        if self.purpose=='generation' and job["kind"] not in ("visual", "ask_ai") and job["subscription_status"] in ("unsubscribed", "unsubscribe_pending", "unsubscribing"):
            raise SourceError("Newsletter was unsubscribed; no further inference sent")
        return job

    def _check_paid_limits(self, db, job, amount, now):
        # A genuinely free request must stay available even when an unrelated
        # dollar limit has been reached. Ownership/subscription checks still run.
        if amount == 0:
            return
        per_job = self.limit("GOOGLE_PLACES_IMAGE_REQUEST_BUDGET_USD", "0.01") if self.purpose=='image_read' else self.limit("GOOGLE_PLACES_VENUE_REQUEST_BUDGET_USD", "0.05") if self.purpose=='venue_read' else self.limit("GENERATION_JOB_BUDGET_USD", "5")
        per_user = self.limit("GENERATION_USER_DAILY_BUDGET_USD", "5")
        project = self.limit("GENERATION_DAILY_BUDGET_USD", "10")
        day_start = int(now // 86400) * 86400
        job_spend = 0 if self.purpose!='generation' else db.execute("SELECT COALESCE(sum(amount_usd),0) FROM model_spend WHERE job_id=?", (self.job_id,)).fetchone()[0]
        user_spend = db.execute("SELECT COALESCE(sum(amount_usd),0) FROM model_spend WHERE user_id=? AND created_at>=?", (job["user_id"], day_start)).fetchone()[0]
        project_spend = db.execute("SELECT COALESCE(sum(amount_usd),0) FROM model_spend WHERE created_at>=?", (day_start,)).fetchone()[0]
        if job_spend + amount > per_job or user_spend + amount > per_user or project_spend + amount > project:
            raise SourceError("Generation spending limit reached; completed posts are retained. Adjust the backend budget before retrying")

    def _record(self, db, job, reservation, amount, now):
        self._check_paid_limits(db, job, amount, now)
        db.execute("INSERT INTO model_spend(id,user_id,job_id,amount_usd,status,created_at) VALUES(?,?,?,?,'reserved',?)",
                   (reservation, job["user_id"], self.job_id, amount, now))

    def reserve(self, amount):
        now, reservation = time.time(), uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._record(db, self._job(db), reservation, amount, now)
        return reservation

    def reserve_resource(self, rate):
        now, reservation = time.time(), uuid.uuid4().hex
        period = rate.period(now)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self._job(db)
            used = db.execute('SELECT COALESCE(sum(units),0) FROM resource_usage WHERE provider=? AND sku=? AND period=?',
                              (rate.provider, rate.sku, period)).fetchone()[0] + rate.offset(period)
            amount = rate.unit_price_usd if rate.free_units is not None and used >= rate.free_units else 0
            self._record(db, job, reservation, amount, now)
            db.execute('INSERT INTO resource_usage(id,provider,sku,period,units,free_cap,unit_price_usd,created_at) VALUES(?,?,?,?,1,?,?,?)',
                       (reservation, rate.provider, rate.sku, period, rate.free_units, rate.unit_price_usd, now))
        return reservation

    def settle_resource(self, reservation, rejected=False):
        with self.store.connect() as db:
            # Count attempted requests conservatively, including explicit
            # rejections. Never reset monthly usage on retries or account deletion.
            db.execute("UPDATE model_spend SET amount_usd=CASE WHEN ? THEN 0 ELSE amount_usd END,status='settled' WHERE id=?",
                       (bool(rejected), reservation))

    def settle(self, reservation, amount):
        with self.store.connect() as db:
            db.execute("UPDATE model_spend SET amount_usd=?,status='settled' WHERE id=?", (amount, reservation))
