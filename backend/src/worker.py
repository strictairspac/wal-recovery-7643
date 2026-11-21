import logging, time
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models.entities import Recovery, ReplicaRecovery
from src.services.wal_engine import recover
from src.services.replica_engine import reconcile
redis = Redis.from_url(REDIS_URL)
logging.basicConfig(level=logging.INFO)
# Redis leadership lock ensures only one worker processes the durable DB queues.
while True:
    Path('/tmp/heartbeat').touch()
    lock = redis.lock('wal-worker', timeout=15, blocking_timeout=1)
    try:
        with lock:
            with Session() as db:
                job = db.scalar(select(Recovery).where(Recovery.status == 'queued').order_by(Recovery.id).with_for_update(skip_locked=True))
                if job:
                    job.status = 'running'
                    try:
                        job.result = recover(job.snapshot['log_set']['initial'],job.snapshot['log_set']['records'],job.snapshot['case']['crash_after'])
                        job.status = 'completed'
                    except ValueError as exc:
                        job.result = {'error': str(exc), 'simulation': True}
                        job.status = 'failed'
                    db.commit()
                    logging.info('recovery %s %s', job.id, job.status)
                replica_job = db.scalar(select(ReplicaRecovery).where(ReplicaRecovery.status == 'queued').order_by(ReplicaRecovery.id).with_for_update(skip_locked=True))
                if replica_job:
                    replica_job.status = 'running'
                    try:
                        snap = replica_job.snapshot['bundle']
                        replica_job.result = reconcile(snap['initial'], snap['replica_a'],
                                                       snap['replica_b'], snap['vouchers'],
                                                       snap['crash_after'])
                        replica_job.status = 'completed'
                    except ValueError as exc:
                        replica_job.result = {'error': str(exc), 'simulation': True}
                        replica_job.status = 'failed'
                    db.commit()
                    logging.info('replica recovery %s %s', replica_job.id, replica_job.status)
    except Exception:
        logging.exception('worker retry')
    time.sleep(1)
