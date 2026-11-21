import os
from sqlalchemy import select
from src.config.database import Base,engine,Session
from src.models.entities import User,LogSet,CrashCase,ReplicaBundle
from src.utils.security import hash_password
RECORDS=[{'op':'begin','tx':'T1'},{'op':'write','tx':'T1','key':'sample_A','value':75},{'op':'commit','tx':'T1'},{'op':'checkpoint'},{'op':'begin','tx':'T2'},{'op':'write','tx':'T2','key':'sample_B','value':40},{'op':'begin','tx':'T3'},{'op':'write','tx':'T3','key':'sample_A','value':60},{'op':'commit','tx':'T3'},{'op':'write','tx':'T2','key':'sample_B','value':35},{'op':'commit','tx':'T2'}]
# 双副本教学场景：两份副本各有损坏，位置/事务号/点号仍可信。
# 副本A的T3写入值损坏（靠副本B拼回）；副本B的T2首条写入缺失（靠副本A拼回）；
# 断电点10，T2提交未持久化且无凭证 → B点隔离并回退检查点值50；T1/T3有凭证核实 → A出账60。
REPLICA_A=[dict(r) for r in RECORDS]
REPLICA_A[7]={'op':'write','tx':'T3','key':'sample_A','value':'DAMAGED'}
REPLICA_B=[dict(r) for r in RECORDS]
REPLICA_B[5]=None
VOUCHERS=[{'tx':'T1'},{'tx':'T3'}]
def seed():
    Base.metadata.create_all(engine)
    if os.getenv('SEED_DEMO','true').lower()!='true':return
    with Session() as db:
        for role in ('admin','operator','viewer'):
            email=role+'@haili.local'
            if not db.scalar(select(User).where(User.email==email)):db.add(User(email=email,role=role,password_hash=hash_password('Recovery2026!')))
        logs=db.scalar(select(LogSet).where(LogSet.name=='海砾采样账本-晚班'))
        if logs is None:
            logs=LogSet(name='海砾采样账本-晚班',description='A初始100份、B初始50份，T1提交后检查点，T2未提交，T3已提交',initial={'sample_A':100,'sample_B':50},records=RECORDS);db.add(logs);db.flush()
        for name,point in [('断电点10-撤销T2',10),('完整提交点11',11),('初始点0',0)]:
            if not db.scalar(select(CrashCase).where(CrashCase.name==name)):db.add(CrashCase(name=name,log_set_id=logs.id,crash_after=point))
        bundle=db.scalar(select(ReplicaBundle).where(ReplicaBundle.name=='海砾双副本-晚班断电'))
        if bundle is None:
            db.add(ReplicaBundle(name='海砾双副本-晚班断电',
                description='副本A的T3值损坏、副本B的T2写入缺失，可跨副本拼回；T2无提交凭证，B点隔离回退50，A凭T3凭证出账60',
                initial={'sample_A':100,'sample_B':50},replica_a=REPLICA_A,replica_b=REPLICA_B,
                vouchers=VOUCHERS,crash_after=10))
        db.commit()
if __name__=='__main__':seed()
