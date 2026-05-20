# PostgreSQL迁移快速启动指南

## 5分钟快速开始

### 前提条件
- Docker和Docker Compose已安装
- Python 3.12+
- 项目依赖已安装

---

## 步骤1: 启动PostgreSQL (1分钟)

```bash
# 启动PostgreSQL容器
docker-compose -f docker-compose.postgresql.yml up -d postgres

# 等待PostgreSQL就绪（约10秒）
docker-compose -f docker-compose.postgresql.yml ps

# 验证PostgreSQL运行
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT version();"
```

**预期输出**:
```
PostgreSQL 16.x on x86_64-pc-linux-musl, compiled by gcc...
```

---

## 步骤2: 配置环境 (1分钟)

```bash
# 复制配置模板
cp .env.postgresql.example .env

# 编辑.env文件
# 设置以下变量：
DATABASE_TYPE=postgresql
POSTGRESQL_URL=postgresql://grader:grader_password_change_in_production@localhost:5432/grading
```

**或者直接设置环境变量**:
```bash
export DATABASE_TYPE=postgresql
export POSTGRESQL_URL=postgresql://grader:grader_password_change_in_production@localhost:5432/grading
```

---

## 步骤3: 安装依赖 (1分钟)

```bash
# 安装PostgreSQL驱动
pip install asyncpg psycopg2-binary

# 或者重新安装所有依赖
pip install -r requirements.txt
```

---

## 步骤4: 初始化数据库 (30秒)

```bash
# 使用Python测试初始化
python -c "
import asyncio
from src.db.init_adapter import init_db_with_adapter, check_db_connection

async def test():
    await init_db_with_adapter()
    connected = await check_db_connection()
    print(f'Database initialized: {connected}')

asyncio.run(test())
"
```

**预期输出**:
```
Database initialized: True
```

---

## 步骤5: 迁移数据（可选，如果有现有SQLite数据）(1分钟)

```bash
# 运行迁移脚本
python scripts/migrate_to_postgresql.py
```

**交互式确认**:
```
================================================================================
SQLite to PostgreSQL Migration
================================================================================
Source (SQLite): outputs/grading_database.db
Target (PostgreSQL): postgresql://grader:***@localhost:5432/grading

This will migrate all data from SQLite to PostgreSQL. Continue? (yes/no): yes
```

---

## 步骤6: 验证 (30秒)

```bash
# 运行集成测试
python -m pytest tests/test_db_integration.py -v

# 检查数据库连接
python -c "
import asyncio
from src.db.init_adapter import get_database_info

async def test():
    info = await get_database_info()
    print(f'Database type: {info[\"type\"]}')
    print(f'Database version: {info[\"version\"]}')

asyncio.run(test())
"
```

**预期输出**:
```
Database type: postgresql
Database version: PostgreSQL 16.x...
```

---

## 故障排除

### 问题1: PostgreSQL连接失败

**症状**:
```
psycopg2.OperationalError: could not connect to server
```

**解决**:
```bash
# 检查PostgreSQL是否运行
docker-compose -f docker-compose.postgresql.yml ps

# 查看日志
docker-compose -f docker-compose.postgresql.yml logs postgres

# 重启PostgreSQL
docker-compose -f docker-compose.postgresql.yml restart postgres
```

### 问题2: 权限错误

**症状**:
```
permission denied for table tasks
```

**解决**:
```bash
# 重新初始化数据库
docker-compose -f docker-compose.postgresql.yml down -v
docker-compose -f docker-compose.postgresql.yml up -d postgres
```

### 问题3: 端口冲突

**症状**:
```
Error starting userland proxy: listen tcp4 0.0.0.0:5432: bind: address already in use
```

**解决**:
```bash
# 修改docker-compose.postgresql.yml中的端口
ports:
  - "5433:5432"  # 使用5433而不是5432

# 更新连接字符串
POSTGRESQL_URL=postgresql://grader:password@localhost:5433/grading
```

---

## 回滚到SQLite

如果需要回滚：

```bash
# 1. 修改.env
DATABASE_TYPE=sqlite
SQLITE_DB_PATH=outputs/grading_database.db

# 2. 重启应用
# SQLite数据库文件已保留，可立即使用
```

---

## 性能验证

### 并发写入测试

```bash
# 运行性能测试（待实施）
python scripts/benchmark_database.py
```

### 监控查询

```bash
# 查看活动连接
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"

# 查看慢查询
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' ORDER BY duration DESC;"
```

---

## 下一步

1. ✅ PostgreSQL运行正常
2. ✅ 数据库初始化成功
3. ✅ 连接测试通过
4. ⏳ 运行完整测试套件
5. ⏳ 性能基准测试
6. ⏳ 生产部署

---

## 有用的命令

### PostgreSQL管理

```bash
# 进入PostgreSQL shell
docker-compose -f docker-compose.postgresql.yml exec postgres psql -U grader -d grading

# 列出所有表
\dt

# 查看表结构
\d tasks

# 查看表数据
SELECT * FROM tasks LIMIT 10;

# 退出
\q
```

### Docker管理

```bash
# 查看日志
docker-compose -f docker-compose.postgresql.yml logs -f postgres

# 停止PostgreSQL
docker-compose -f docker-compose.postgresql.yml stop postgres

# 启动PostgreSQL
docker-compose -f docker-compose.postgresql.yml start postgres

# 完全删除（包括数据）
docker-compose -f docker-compose.postgresql.yml down -v
```

---

## 支持

遇到问题？

1. 查看日志: `docker-compose -f docker-compose.postgresql.yml logs postgres`
2. 查看文档: `docs/POSTGRESQL_MIGRATION_GUIDE.md`
3. 运行测试: `pytest tests/test_db_integration.py -v`

---

**总用时**: ~5分钟
**难度**: 简单
**风险**: 低（可随时回滚）
