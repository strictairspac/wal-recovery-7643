from fastapi import APIRouter, Depends, HTTPException
from src.config.database import get_db
from src.middlewares.auth import current_user, writable
from src.schemas.replica import ReplicaBundleInput
from src.services.replica_bundle_service import list_bundles, create_bundle
from src.controllers.actions import audit

router = APIRouter()


@router.get('/replica-bundles')
def listing(db=Depends(get_db), user=Depends(current_user)):
    return list_bundles(db)


@router.post('/replica-bundles', status_code=201)
def create(data: ReplicaBundleInput, db=Depends(get_db), user=Depends(writable)):
    try:
        out = create_bundle(db, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    audit(db, user, 'replica_bundle:create')
    db.commit()
    return out
