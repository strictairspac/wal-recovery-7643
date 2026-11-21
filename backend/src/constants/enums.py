ROLES = ('admin', 'operator', 'viewer')
DRILL_STATUSES = ('queued', 'running', 'completed', 'failed')
# 副本拼合隔离原因，与 services/replica_engine.py 中的常量保持一致
ISOLATION_REASONS = (
    '两份副本同位置均缺失，写入不可读',
    '两副本读数不一致且无副本损坏标记，存疑值不采信',
    '事务结束证据冲突，提交无法核实',
    '提交记录缺失且无提交凭证，事务未核实',
    '副本提交记录与提交凭证冲突',
    '事务开始记录双副本缺失，事务链不完整',
)
