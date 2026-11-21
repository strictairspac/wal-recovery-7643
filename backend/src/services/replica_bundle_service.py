from sqlalchemy import select
from src.models.entities import ReplicaBundle
from src.services.replica_engine import validate_inputs
from src.utils.serialize import record


def list_bundles(db):
    return [record(x) for x in db.scalars(select(ReplicaBundle).order_by(ReplicaBundle.id))]


def _bundle_payload(data):
    values = data.model_dump()
    # Pydantic 已把可读记录转为 dict、缺失位置转为 None，直接交给引擎做业务校验
    validate_inputs(values['initial'], values['replica_a'], values['replica_b'],
                    values['vouchers'], values['crash_after'])
    return values


def create_bundle(db, data):
    obj = ReplicaBundle(**_bundle_payload(data))
    db.add(obj)
    db.flush()
    return record(obj)
