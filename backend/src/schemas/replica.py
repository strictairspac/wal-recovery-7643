from pydantic import BaseModel, Field, field_validator

# 单条副本记录：op/tx/key 为可信骨架，value 允许缺失或损坏（引擎隔离处理）；
# 数组元素允许为 null，表示该副本在该日志位置不可读（位置本身仍可信）。
class ReplicaRecordInput(BaseModel):
    op: str = Field(min_length=1, max_length=20)
    tx: str | None = Field(default=None, max_length=30, pattern=r'^[A-Za-z0-9_-]+$')
    key: str | None = Field(default=None, max_length=50, pattern=r'^[A-Za-z0-9_-]+$')
    value: int | str | None = Field(default=None)

    @field_validator('value')
    @classmethod
    def _value_bounded(cls, v):
        if isinstance(v, str) and len(v) > 50:
            raise ValueError('损坏值标记长度不超过50')
        return v

class VoucherInput(BaseModel):
    tx: str = Field(min_length=1, max_length=30, pattern=r'^[A-Za-z0-9_-]+$')

class ReplicaBundleInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    initial: dict[str, int] = Field(min_length=1, max_length=30)
    replica_a: list[ReplicaRecordInput | None] = Field(min_length=1, max_length=200)
    replica_b: list[ReplicaRecordInput | None] = Field(min_length=1, max_length=200)
    vouchers: list[VoucherInput] = Field(default=list, max_length=200)
    crash_after: int = Field(ge=0, le=200)
    description: str = Field(default='', max_length=500)

class ReplicaRecoveryInput(BaseModel):
    bundle_id: int = Field(gt=0)
    label: str = Field(min_length=2, max_length=120)
