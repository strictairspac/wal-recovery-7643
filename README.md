# 海砾 · 采样账本 WAL 恢复演练
海砾计量数据保障组（8人）用可重放日志替代恢复演练时手工拼接的SQL片段和账本截图。

## 场景与模块
上游为事务日志样例，下游为值班复核人。原流程人工标记提交、表格算恢复余额、截图写结论；痛点是崩溃点不清、撤销顺序容易错、恢复证据缺失。模块为事务日志集 / 故障注入点 / 恢复队列 / 恢复证据，实体LogSet/CrashCase/Recovery贯穿全栈；断电双副本场景另设凭证包 / 拼合恢复队列，实体ReplicaBundle/ReplicaRecovery，见下文“双副本拼合”。

**Python WAL教学模型，不注入真实PostgreSQL故障，不执行数据库物理恢复。** PostgreSQL真实保存业务输入、冻结快照、任务和结果，Redis实际用于worker互斥，后台Python实际演算恢复历史。

## 启动
```bash
cp .env.example .env
docker compose up -d --build
# http://localhost:18246
# http://localhost:19246/docs
docker compose exec -T backend python -m scripts.seed_demo
docker compose down --remove-orphans
```
SEED_DEMO=true时启动自动执行种子；自然键查重，不覆盖用户修改。初始化失败显式阻止启动，不隐藏结构错误。删除数据需显式down -v。

|账号|密码|权限|典型动作|
|---|---|---|---|
|admin@haili.local|Recovery2026!|读写/导出/审计|查询审计记录|
|operator@haili.local|Recovery2026!|新增日志/场景/运行/导出|设置断电点10|
|viewer@haili.local|Recovery2026!|只读/导出|核对恢复账本|

## 一天的复核剧本
09:00操作员登录；09:05查看海砾采样账本-晚班（A100、B50）；09:10复制日志集保存shift-20261117；09:15在第10条日志后注入断电点；09:20提交恢复；09:25核对检查点LSN4、已提交T3、未提交T2两条写入逆序撤销，最终A60/B50；09:30下载JSON；09:40观察员核对且写操作403；10:00管理员查看/api/audits。

## 算法、格式和失败态
日志为JSON数组，操作为begin/write/commit/abort/checkpoint；write包含tx/key/value，其他事务操作包含tx，checkpoint不带tx。键必须在初始整数账本存在。事务ID不可复用；严格写锁直到commit/abort释放，同键重叠活动事务拒绝；检查点仅在无活动事务时允许。最多200条日志/30键/整数绝对值不超过10亿。

crash_after=N表示前N条日志已持久化。最近静止检查点保存全部数据页，检查点后数据页假定均未写回（no-force）。恢复重放日志写入和已abort的撤销，再按LSN倒序撤销未结束事务。独立oracle从检查点账本仅应用已提交事务写入，结果须相同。输出重做/撤销轨迹、事务集合、各阶段账本、SHA256和模型版本。空输入、未知键、事务顺序非法、检查点有活动事务、写锁冲突、崩溃点超限均拒绝。模型不涉及页LSN、CLR、模糊检查点或真实磁盘故障；不是完整ARIES实现。

## 双副本拼合（cross-replica-voucher-v1）
断电后两份采样日志副本都可能损坏：**位置LSN、事务号tx、点号key仍可信，值或整条记录可能损坏/缺失**（缺失位置用null占位，两副本等长按位置对齐），断电前留存的**提交凭证vouchers**（`[{"tx":"T3"}]`）是提交事实的权威依据。
- 逐位置拼合：一副本整条缺失取另一副本；单副本值损坏取可读副本；两副本读数冲突（双可读且不一致）则该点隔离，存疑值仅入工作账本。
- 提交核对：副本commit须有凭证支撑，否则涉及点先隔离（`提交记录缺失且无提交凭证`）；commit记录双副本损坏时凭证可补全提交；副本abort与凭证冲突按证据冲突隔离。
- 隔离点不参与出账，出账回退最近检查点可信值，其余点继续出账；后来经凭证核实的提交可用可信写入覆盖存疑读数并解除隔离。
- **检查点拒绝在有未解除隔离点时建立**，存疑值绝不会随检查点出账，违规任务标记failed。
- 独立oracle只从检查点账本应用“凭证核实且写入可信”的事务，与出账账本必须一致；输出重建日志、拼合/撤销轨迹、隔离点与原因、SHA256。
实体为ReplicaBundle/ReplicaRecovery，接口`/replica-bundles`、`/replica-recoveries`（含`/export`下载，权限与原模块一致），worker同一Redis锁顺序消费两条队列。前端页面05双副本拼合、06拼合证据；原有WAL模块与下载不受影响。

## 工程与开发
前端原生HTML/CSS/JS+Nginx1.27；后端Python3.11+FastAPI+SQLAlchemy+PyJWT；PostgreSQL16和Redis7；5服务均healthcheck。持久任务队列在PG，单worker通过Redis锁互斥。20条待执行配额，审计记录写入操作，JWT8小时、PBKDF2随机盐密码。后端路由不含/api，Nginx/api/去前缀代理。

目录frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}与backend/src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}。本地开发安装backend/requirements.txt并设DATABASE_URL/REDIS_URL/JWT_SECRET，再运行uvicorn src.main:app；前端需代理/api。migrations记录初始化结构，暂无在线升级器。

环境COMPOSE_PROJECT_NAME控制命名，FRONTEND_PORT/BACKEND_PORT/DB_PORT为18246/19246/17746，DB_PASSWORD控制演示数据库密码，JWT_SECRET签名密钥，SEED_DEMO控制种子。命名卷db-data/redis-data/artifacts；共享artifacts预留，导出由冻结快照即时生成。Google Fonts需外网，业务离线可运行。默认凭据仅本地演示。

共享枚举roles与任务states定义于backend/src/constants/enums.py、frontend/src/constants/enums.js，状态使用models/entities.py/services/recovery_service.py/worker.py/components/common/status.js；日志操作类型在schemas/log_set.py和pages/logs.js中声明/说明，副本隔离原因枚举见constants与services/replica_engine.py。MIT许可。真实验证见批次test-artifacts/gb-4107/TESTING.md。
