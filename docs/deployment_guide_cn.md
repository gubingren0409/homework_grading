# 部署指南（中文）

本文对应 **Phase 7 / P7-01**，目标是把当前仓库从“开发者自己知道怎么跑”整理成“新机器按步骤可部署”。

---

## 1. 当前推荐部署形态

当前最现实、最稳定的交付形态是：

1. **单机 Docker Compose**
2. **1 个 API 容器 + 1 个 Worker 容器 + 1 个 Redis 容器**
3. **本地卷挂载 `data/` 与 `outputs/`**
4. **SQLite 作为当前默认业务库**

这套形态适合：

- 小规模教师试用
- 演示环境
- 内部验证环境
- 单机部署场景

它**不适合**：

- 大规模并发批改
- 多实例 API / Worker 水平扩容
- 严格生产 SLA
- 强一致、多用户重度写入场景

---

## 2. 容器拓扑

当前 `docker-compose.yml` 包含 4 个服务：

| 服务 | 作用 | 关键说明 |
| --- | --- | --- |
| `nginx` | 反向代理 + HTTPS 终止 | 对外暴露 80/443，透传 SSE |
| `redis` | Celery broker + SSE/PubSub 状态中转 | 当前是单点 |
| `grader-api` | FastAPI 主入口 | 仅在容器网络内暴露 8000 |
| `grader-worker` | Celery worker | 负责真实批改执行 |

数据卷与目录：

| 路径 | 用途 |
| --- | --- |
| `./data` -> `/app/data` | 输入数据、参考数据、演示数据 |
| `./outputs` -> `/app/outputs` | SQLite、运行输出、报告、导出文件 |
| `redis_data` | Redis 持久化 |

---

## 3. 部署前准备

### 3.1 系统要求

建议部署机至少具备：

- 4 核 CPU
- 8GB 内存
- 稳定外网访问能力（用于模型调用）
- Docker / Docker Compose

### 3.2 必备目录

确保项目根目录下至少存在：

- `.env`
- `data/`
- `outputs/`

如果是首次部署：

Linux / macOS：

```bash
mkdir -p data outputs
cp .env.example .env
```

PowerShell：

```powershell
New-Item -ItemType Directory -Force data, outputs
Copy-Item .env.example .env
```

### 3.3 必填环境变量

至少要配置：

- `QWEN_API_KEYS`
- `DEEPSEEK_API_KEYS`
- `LLM_EGRESS_ENABLED=true`
- `REDIS_HOST`
- `REDIS_PORT`
- `SQLITE_DB_PATH`

当前 `.env.example` 已覆盖大部分运行参数，建议从它复制。

---

## 4. 启动方式

### 4.1 本地直接启动

适合开发与调试：

```bash
pip install -r requirements.txt
uvicorn src.main:app --host 0.0.0.0 --port 8000
celery -A src.worker.main worker --loglevel=info --concurrency=4
```

Windows 下 worker 仍建议：

```bash
celery -A src.worker.main worker --loglevel=info --pool=solo --concurrency=1
```

### 4.2 Docker Compose 启动

适合演示与单机交付：

```bash
docker compose up --build
```

后台启动：

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
docker compose logs grader-api
docker compose logs grader-worker
docker compose logs redis
```

停止：

```bash
docker compose down
```

---

## 5. 启动后检查

按顺序检查：

1. `docker compose ps` 中 **4 个服务（nginx / redis / grader-api / grader-worker）** 都应启动且状态为 healthy 或 running。
2. 访问 `http://<host>/`（通过 Nginx），应看到产品落地页。
3. 访问 `http://<host>/login`，确认登录页可用。
4. 使用试用账号登录（默认：teacher-demo / demo123），跳转到工作台。
5. 检查核心页面均可访问：
   - `/student-console`（教师任务创建向导）
   - `/task-progress`（任务进度）
   - `/class-dashboard`（班级看板）
   - `/review-console`（复核工作台）
   - `/ops-console`（运维面板）
6. 提交一个小任务（1-2 份学生作答），确认：
   - 返回 `task_id`
   - 进度页显示处理中
   - Worker 日志中有任务执行记录
   - 任务完成后可在班级看板中查看结果

### 5.1 Nginx 相关检查

1. 确认 Nginx 日志无 502/504 错误：`docker compose logs nginx`
2. SSE 实时进度推送正常（进度页无需手动刷新即可更新）
3. 大文件上传无超时（Nginx 已配置 100MB 上传限制和 1800s 超时）

---

## 6. 升级流程

推荐短流程：

1. 备份 `outputs/`，尤其是 SQLite 文件
2. 拉取新代码
3. 对比 `.env.example` 与现网 `.env` 的差异
4. 执行：

```bash
docker compose down
docker compose up -d --build
```

5. 检查：
   - `docker compose ps`
   - API 健康接口
   - 关键页面可访问
   - 提交一条最小样本任务

---

## 7. 回滚流程

当前最现实的回滚方式是**版本回滚 + 数据保留**：

1. 切回上一个稳定代码版本
2. 保留 `.env`
3. 保留 `outputs/`
4. 重新 `docker compose up -d --build`

若本次升级涉及数据库结构变化：

1. 先回滚代码
2. 若出现兼容问题，再回滚 SQLite 备份

当前系统还没有完整的数据库迁移框架，因此**升级前备份 SQLite 文件是硬要求**。

---

## 8. 常见排障入口

### 8.1 页面能开，但任务不动

优先检查：

1. `redis` 是否健康
2. `grader-worker` 是否在运行
3. Worker 日志是否收到任务
4. `.env` 中 `REDIS_HOST` 是否与 compose 网络一致

### 8.2 任务创建成功，但批改失败

优先检查：

1. `.env` 中模型 Key 是否有效
2. `LLM_EGRESS_ENABLED` 是否被关闭
3. 外网是否能访问 Qwen / DeepSeek
4. Worker 日志中是否出现上游报错

### 8.3 SSE 没有更新

优先检查：

1. Redis 是否可用
2. API 日志里是否有 SSE 连接错误
3. 是否可退回 `/api/v1/grade/{task_id}` 轮询

### 8.4 SQLite 写入异常或锁竞争

优先检查：

1. 是否在同一台机器上高并发跑了太多批任务
2. `outputs/` 所在磁盘是否可写
3. 是否有人直接占用了数据库文件

### 8.5 Nginx 502/504 错误

优先检查：

1. `grader-api` 是否正常运行：`docker compose logs grader-api`
2. Nginx 上游配置是否指向正确的服务名和端口（默认 `grader-api:8000`）
3. SSE 端点出现 504：检查 `proxy_read_timeout` 是否设为 1800s（已在 `proxy_common.conf` 中配置）

### 8.6 Windows 开发环境 Redis 端口冲突（双 Redis 问题）

在 Windows 上同时安装了本地 Redis 和 Docker Redis 时，可能出现端口 6379 冲突：

1. 先停止本地 Redis 服务：`Stop-Service Redis` 或 `net stop Redis`
2. 再启动 Docker Redis：`docker compose up redis -d`
3. 确认只有 Docker Redis 在运行：`docker compose ps redis`
4. 如果 Celery worker 连不上 Redis，检查 `.env` 中 `REDIS_HOST=localhost`（本地开发）或 `REDIS_HOST=redis`（Docker 网络内）

### 8.7 Docker Worker 与 SQLite 不兼容（WSL2 文件锁问题）

Docker 容器运行在 WSL2 中，SQLite 文件锁在 Windows ↔ WSL2 bind mount 间不可靠：

1. **症状**：Worker 容器日志出现 `disk I/O error` 或 `database is locked`
2. **临时方案**：在本地直接运行 Worker（不用 Docker），仅 Redis 使用 Docker
3. **长期方案**：迁移到 PostgreSQL（见 `docs/postgresql_migration_plan_cn.md`）

---

## 9. 当前部署建议

如果你现在要对外演示或做首批试点，我建议：

1. **单机 Compose 部署**
2. **通过 Nginx 反向代理暴露 API**
3. **HTTPS 放在 Nginx 层**
4. **每天备份一次 `outputs/`**
5. **把 Redis 和 SQLite 都视为当前单点**

这已经足够支撑：

- 首批老师试用
- 小范围试点
- Demo / 路演 / 交流演示

但还不应直接当成大规模正式生产架构。
