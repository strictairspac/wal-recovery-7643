from sqlalchemy import select
from fastapi import HTTPException
from src.models.entities import ReplicaRecovery, ReplicaBundle
from src.utils.serialize import record


def list_replica_recoveries(db):
    return [record(x) for x in
            db.scalars(select(ReplicaRecovery).order_by(ReplicaRecovery.id.desc()).limit(100))]


def get_replica_recovery(db, ident):
    obj = db.get(ReplicaRecovery, ident)
    if obj is None:
        raise HTTPException(404, '副本恢复记录不存在')
    return obj


def create_replica_recovery(db, data):
    bundle = db.get(ReplicaBundle, data.bundle_id)
    if bundle is None:
        raise HTTPException(404, '副本凭证包不存在')
    pending = len(db.scalars(
        select(ReplicaRecovery.id).where(ReplicaRecovery.status.in_(['queued', 'running']))).all())
    if pending >= 20:
        raise HTTPException(429, '待处理配额20已满')
    obj = ReplicaRecovery(bundle_id=bundle.id, label=data.label, status='queued',
                          snapshot={'bundle': record(bundle)})
    db.add(obj)
    db.flush()
    return obj
