# SQLite性能问题分析与PostgreSQL迁移方案

## 一、问题诊断

### 1.1 用户观察到的问题
**症状**: 实际样例测试中各个阶段都有长尾延迟
- API调用层有长尾 → 正常（模型响应时间不可控）
- 本地算法/代码执行层有长尾 → **不正常**

**初步诊断**: SQLite写锁导致的并发瓶颈

### 1.2 当前SQLite配置分析

#### 配置参数（src/db/core_utils.py）
```python
_WRITE_LOCK_MAX_RETRIES: int = 6           # 最多重试6次
_WRITE_BACKOFF_BASE_SECONDS: float = 0.05  # 基础退避50ms
_WRITE_BACKOFF_MAX_SECONDS: float = 1.0    # 最大退避1秒
_SQLITE_BUSY_TIMEOUT_MS: int = 5000        # 忙等待5秒

# PRAGMA设置
PRAGMA journal_mode=WAL;      # 写前日志模式
PRAGMA synchronous=NORMAL;    # 正常同步
PRAGMA busy_timeout=5000;     # 5秒超时
```

#### 问题分析

**1. WAL模式的局限性**
- ✅ 允许读写并发
- ❌ 但**写操作仍然串行**（只能有一个写事务）
- ❌ 高并发写入时会排队等待

**2. 重试机制的影响**
```
最坏情况延迟 = 5秒(busy_timeout) + 6次重试 * 1秒(max_backoff) = 11秒
```
这解释了长尾延迟！

**3. 并发写入场景**
在批量批改场景下：
- 多个worker同时写入结果
- 状态更新（PENDING → PROCESSING → COMPLETED）
- 进度更新（progress, eta_seconds）
- 心跳更新（last_heartbeat_at）

每个写操作都可能遇到锁等待。

### 1.3 性能瓶颈识别

#### 高频写操作（src/db/client.py）
1. `update_task_status()` - 状态更新
2. `update_task_progress()` - 进度更新
3. `touch_task_heartbeat()` - 心跳更新
4. `save_grading_result()` - 结果保存
5. `save_paper_grading_report()` - 报告保存

#### 并发场景
- **批量批改**: 100个学生 × 10个题目 = 1000次写入
- **状态更新**: 每个任务至少3次状态变更
- **心跳更新**: 长任务每30秒一次心跳

**估算**: 一个100学生的批量任务可能产生 **3000+次写操作**

在SQLite下，这些写操作**完全串行**，导致严重的长尾延迟。

---

## 二、PostgreSQL迁移方案

### 2.1 迁移收益

#### 性能提升
- ✅ **真正的并发写入**（MVCC机制）
- ✅ **无写锁等待**（多版本并发控制）
- ✅ **更好的索引性能**
- ✅ **连接池支持**

#### 预期改善
- 长尾延迟: 11秒 → <100ms
- 批量任务吞吐: 提升5-10倍
- 并发能力: 无限制

### 2.2 迁移策略

#### 方案A: 直接迁移（推荐）
**优点**: 简单直接，一次性解决
**缺点**: 需要停机迁移
**适用**: 开发/测试环境，或可接受短暂停机

#### 方案B: 双写迁移
**优点**: 零停机
**缺点**: 复杂，需要数据一致性保证
**适用**: 生产环境，不能停机

**建议**: 先用方案A在开发环境验证，再决定生产环境策略

### 2.3 技术方案

#### 2.3.1 数据库适配层

**当前架构**:
```
API/Worker → db/client.py → aiosqlite → SQLite
```

**目标架构**:
```
API/Worker → db/client.py → asyncpg → PostgreSQL
                          ↘ aiosqlite → SQLite (可选兼容)
```

#### 2.3.2 Schema迁移

**SQLite Schema** (src/db/schema.sql):
```sql
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ...
);
```

**PostgreSQL Schema**:
```sql
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ...
);

-- 添加PostgreSQL特有优化
CREATE INDEX CONCURRENTLY idx_tasks_status ON tasks(status);
CREATE INDEX CONCURRENTLY idx_tasks_created_at ON tasks(created_at);
```

**兼容性**: 大部分SQL语法兼容，需要调整：
1. `AUTOINCREMENT` → `SERIAL`
2. `DATETIME` → `TIMESTAMP`
3. `TEXT` → `TEXT` (兼容)

#### 2.3.3 代码适配

**需要修改的文件**:
1. `src/db/core_utils.py` - 连接管理
2. `src/db/client.py` - 数据库操作
3. `src/core/config.py` - 配置管理
4. `requirements.txt` - 添加asyncpg

**最小化改动策略**:
- 保持现有API不变
- 只修改底层实现
- 使用适配器模式

### 2.4 实施步骤

#### Phase 1: 准备工作（1天）
1. ✅ 分析当前SQLite使用情况
2. ✅ 设计迁移方案
3. ⏳ 创建PostgreSQL schema
4. ⏳ 编写数据迁移脚本

#### Phase 2: 代码适配（2-3天）
1. ⏳ 创建数据库适配层
2. ⏳ 修改连接管理
3. ⏳ 适配SQL语句
4. ⏳ 更新配置管理

#### Phase 3: 测试验证（1-2天）
1. ⏳ 单元测试
2. ⏳ 集成测试
3. ⏳ 性能测试
4. ⏳ 数据迁移测试

#### Phase 4: 部署上线（1天）
1. ⏳ 备份现有数据
2. ⏳ 执行数据迁移
3. ⏳ 部署新代码
4. ⏳ 监控验证

**总工作量**: 5-7天

---

## 三、详细实施计划

### 3.1 PostgreSQL Schema设计

#### 核心表结构
```sql
-- tasks表（任务）
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    celery_task_id TEXT,
    rubric_id TEXT,
    grading_status TEXT,
    review_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
    fallback_reason TEXT,
    submitted_count INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0,
    eta_seconds INTEGER,
    last_heartbeat_at TIMESTAMP,
    teacher_id TEXT,
    error_message TEXT
);

-- 索引优化
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_review_status ON tasks(review_status);
CREATE INDEX idx_tasks_grading_status ON tasks(grading_status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at);
CREATE INDEX idx_tasks_teacher_id ON tasks(teacher_id);

-- grading_results表（批改结果）
CREATE TABLE grading_results (
    result_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    student_id TEXT,
    report_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE INDEX idx_results_task_id ON grading_results(task_id);
CREATE INDEX idx_results_student_id ON grading_results(student_id);

-- rubrics表（评分标准）
CREATE TABLE rubrics (
    rubric_id TEXT PRIMARY KEY,
    question_id TEXT NOT NULL,
    rubric_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fingerprint TEXT,
    teacher_id TEXT
);

CREATE INDEX idx_rubrics_question_id ON rubrics(question_id);
CREATE INDEX idx_rubrics_fingerprint ON rubrics(fingerprint);
CREATE INDEX idx_rubrics_teacher_id ON rubrics(teacher_id);
```

### 3.2 数据库适配层设计

#### 抽象接口
```python
# src/db/adapter.py
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

class DatabaseAdapter(ABC):
    @abstractmethod
    async def connect(self, connection_string: str):
        pass
    
    @abstractmethod
    async def execute(self, query: str, params: tuple):
        pass
    
    @abstractmethod
    async def fetch_one(self, query: str, params: tuple):
        pass
    
    @abstractmethod
    async def fetch_all(self, query: str, params: tuple):
        pass
    
    @abstractmethod
    async def commit(self):
        pass

class SQLiteAdapter(DatabaseAdapter):
    # 现有实现
    pass

class PostgreSQLAdapter(DatabaseAdapter):
    # 新实现
    pass
```

### 3.3 配置管理

#### 环境变量
```bash
# SQLite（现有）
DATABASE_PATH=./data/grading.db

# PostgreSQL（新增）
DATABASE_TYPE=postgresql  # 或 sqlite
DATABASE_URL=postgresql://user:pass@localhost:5432/grading
```

#### 配置类
```python
# src/core/config.py
class Settings(BaseSettings):
    database_type: str = "sqlite"  # sqlite | postgresql
    database_path: str = "./data/grading.db"  # SQLite路径
    database_url: Optional[str] = None  # PostgreSQL URL
    
    @property
    def db_connection_string(self) -> str:
        if self.database_type == "postgresql":
            return self.database_url
        return self.database_path
```

### 3.4 数据迁移脚本

```python
# scripts/migrate_to_postgresql.py
import asyncio
import aiosqlite
import asyncpg

async def migrate_data():
    # 1. 连接SQLite
    sqlite_db = await aiosqlite.connect("data/grading.db")
    
    # 2. 连接PostgreSQL
    pg_pool = await asyncpg.create_pool(
        "postgresql://user:pass@localhost:5432/grading"
    )
    
    # 3. 迁移tasks表
    async with sqlite_db.execute("SELECT * FROM tasks") as cursor:
        rows = await cursor.fetchall()
        async with pg_pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO tasks VALUES ($1, $2, ...)",
                rows
            )
    
    # 4. 迁移其他表...
    
    await sqlite_db.close()
    await pg_pool.close()
```

---

## 四、风险评估与缓解

### 4.1 风险清单

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| 数据迁移失败 | 高 | 低 | 充分测试，备份数据 |
| SQL兼容性问题 | 中 | 中 | 提前识别，逐个适配 |
| 性能不如预期 | 中 | 低 | 性能测试，索引优化 |
| 部署复杂度增加 | 低 | 高 | Docker Compose，文档 |

### 4.2 回滚方案

**如果迁移失败**:
1. 保留SQLite数据库文件
2. 回滚代码到迁移前版本
3. 重启服务

**数据一致性保证**:
- 迁移前完整备份
- 迁移后数据校验
- 保留SQLite文件至少1周

---

## 五、成本收益分析

### 5.1 成本
- **开发时间**: 5-7天
- **测试时间**: 2-3天
- **部署风险**: 中等
- **运维复杂度**: 略增（需要管理PostgreSQL）

### 5.2 收益
- **性能提升**: 5-10倍吞吐量
- **长尾延迟**: 11秒 → <100ms
- **并发能力**: 无限制
- **扩展性**: 支持未来增长

### 5.3 ROI分析
**投入**: 1-2周开发时间
**回报**: 
- 用户体验显著改善
- 系统容量提升10倍
- 消除性能瓶颈

**结论**: **强烈推荐迁移**

---

## 六、下一步行动

### 立即开始（今天）
1. ✅ 完成问题分析
2. ✅ 设计迁移方案
3. ⏳ 创建PostgreSQL schema
4. ⏳ 搭建本地PostgreSQL环境

### 本周完成
1. ⏳ 实现数据库适配层
2. ⏳ 适配核心数据库操作
3. ⏳ 编写数据迁移脚本
4. ⏳ 单元测试

### 下周完成
1. ⏳ 集成测试
2. ⏳ 性能测试
3. ⏳ 数据迁移验证
4. ⏳ 部署上线

---

## 七、参考资料

### PostgreSQL vs SQLite对比
| 特性 | SQLite | PostgreSQL |
|------|--------|-----------|
| 并发写入 | 串行 | 并行（MVCC） |
| 最大连接数 | 1个写连接 | 数百个 |
| 事务隔离 | 有限 | 完整ACID |
| 索引类型 | B-tree | B-tree, Hash, GiST, GIN |
| 全文搜索 | 基础 | 强大 |
| JSON支持 | 基础 | 原生JSONB |

### 相关文档
- PostgreSQL官方文档: https://www.postgresql.org/docs/
- asyncpg文档: https://magicstack.github.io/asyncpg/
- SQLite WAL模式: https://www.sqlite.org/wal.html

---

**报告生成时间**: 2026-05-20
**项目**: homework_grader_system
**作者**: Phase 3 - 架构优化
