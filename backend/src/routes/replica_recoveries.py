import json
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from src.config.database import get_db
from src.middlewares.auth import current_user, writable
from src.schemas.replica import ReplicaRecoveryInput
from src.services.replica_recovery_service import (
    list_replica_recoveries, get_replica_recovery, create_replica_recovery)
from src.controllers.actions import audit
from src.utils.serialize import record

router = APIRouter()


@router.get('/replica-recoveries')
def listing(db=Depends(get_db), user=Depends(current_user)):
    return list_replica_recoveries(db)


@router.post('/replica-recoveries', status_code=202)
def create(data: ReplicaRecoveryInput, db=Depends(get_db), user=Depends(writable)):
    obj = create_replica_recovery(db, data)
    audit(db, user, f'replica_recovery:enqueue:{obj.id}')
    db.commit()
    return record(obj)


@router.get('/replica-recoveries/{ident}')
def detail(ident: int, db=Depends(get_db), user=Depends(current_user)):
    return record(get_replica_recovery(db, ident))


@router.get('/replica-recoveries/{ident}/export')
def export(ident: int, db=Depends(get_db), user=Depends(current_user)):
    obj = get_replica_recovery(db, ident)
    return Response(
        json.dumps(record(obj), ensure_ascii=False, indent=2),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="replica-recovery-{ident}.json"'})
