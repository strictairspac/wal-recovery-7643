# 初始化结构
SQLAlchemy metadata.create_all首次建users、log_sets、crash_cases、recoveries、audits。log_sets.name、crash_cases.name唯一，关联使用外键，入队冻结日志和崩溃点快照。暂无在线schema升级器。

## 副本拼合模块（cross-replica-voucher-v1）
新增两表，由 metadata.create_all 在首次启动/种子时自动建立，无在线升级器：
- `replica_bundles`：录入两份可能损坏的日志副本（replica_a/replica_b，JSON 数组，缺失位置为null）、断电前提交凭证 vouchers、初始账本与崩溃点；name 唯一。
- `replica_recoveries`：异步恢复队列，外键关联 replica_bundles，snapshot 冻结入队时的完整凭证包，result 存 reconcile 输出（出账账本、隔离点与原因、核对轨迹、SHA256）。
旧库升级：删除命名卷后 `docker compose up -d --build` 重建，或在现有库执行 `Base.metadata.create_all(engine)` 仅补建新表（不动既有表）。
