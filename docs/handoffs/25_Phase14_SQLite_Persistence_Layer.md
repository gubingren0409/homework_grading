# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 14 (SQLite Persistence Layer)
## 1. 架构约束 (Architectural Constraints)
废弃单一的扁平文件（CSV）落盘机制。引入 SQLite 关系型数据库，实现批次元数据、学生最终得分与详细 JSON 报告的高效并发持久化，为后续的数据分析与 API 检索提供底层数据支撑。
## 2. 工程拓扑上下文 (Directory Context)
目标工作目录为项目根目录下的 `src/db/` 与 `scripts/`。
你需要新建/修改以下文件：
1. `src/db/schema.sql` (新建，定义 DDL)
2. `src/db/client.py` (新建，轻量级数据库连接与操作封装)
3. `scripts/batch_grade.py` (修改，接入数据持久化算子)
## 3. 核心实现规范 (Implementation Specifications)
### 任务 A: 数据库契约与客户端
**目标文件:** `src/db/schema.sql` & `src/db/client.py`
**设计规则:**
1. **DDL (`schema.sql`):** 定义核心表：
   - `grading_results`: 包含字段 `id` (主键), `student_id` (文本), `question_id` (文本), `total_deduction` (浮点), `is_pass` (布尔), `report_json` (文本，存储完整序列化报告), `created_at` (时间戳)。
2. **客户端 (`client.py`):** - 使用异步变体 `aiosqlite`（推荐以防阻塞主事件循环）。
   - 实现 `init_db(db_path: str)`：读取 `schema.sql` 并建表（`CREATE TABLE IF NOT EXISTS`）。
   - 实现 `async def insert_grading_result(...)`：处理单条或批量的插入操作。确保使用参数化查询防范 SQL 注入风险。
### 任务 B: 批处理调度器挂载 DB
**目标文件:** `scripts/batch_grade.py`
**设计规则:**
1. 引入数据库客户端。新增命令行参数 `--db_path`（默认 `outputs/grading_database.db`）。
2. 在 `async def main()` 入口处执行 `init_db`。
3. 修改并发归集逻辑：在 `asyncio.gather(*tasks)` 返回全部学生的 `EvaluationReport` 集合后，遍历结果集，调用持久化算子将核心字段及原生的 `report.model_dump_json()` 整体写入 SQLite 的 `grading_results` 表中。
4. 原有的 CSV 逻辑可保留作为数据冗余（Redundancy）。
## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出上述三个文件的完整源码。确保数据库游标和连接的正确释放（严格使用 Context Manager）。禁止输出解释性文本。
