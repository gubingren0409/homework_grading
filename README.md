# AI 自动作业批改系统

面向**中学理科教师**的 AI 阅卷与复核工作台。项目围绕“参考答案生成评分标准、学生作答自动识别与评分、异常样本进入人工复核、结果回传与沉淀”这条主链路构建，已经形成可本地运行、可容器部署、可持续迭代的完整后端系统。

> **教师上传参考答案与学生作答 → 生成或复用 rubric → 异步批改 → SSE 实时回传 → 报告查看 → 低置信度样本复核**

---

## 项目定位

这不是单一的 OCR 脚本，也不是只调用一次模型接口的 demo，而是一套围绕教师批改场景设计的 AI 工作流系统：

1. **面向教师，而不是学生练习端**
2. **面向批量阅卷与复核，而不是单题问答**
3. **面向可运行的工程链路，而不是概念验证**

当前最适合的使用场景是：

- 教师上传标准答案，系统自动生成或复用评分标准
- 教师批量上传学生试卷或作业图片 / PDF
- 系统自动识别手写内容、完成结构化评分并生成报告
- 低置信度、不可读、异常样本自动进入人工复核队列
- 系统沉淀 rubric、标注资产、运行时遥测与 prompt 运营数据

---

## 核心能力

### 1. Rubric 驱动评分

系统先基于教师参考答案生成评分标准，再用该标准评估学生作答，避免“只看对错”的粗糙判断，支持更细粒度的扣分说明和报告生成。

### 2. 感知层 / 认知层解耦

- **感知层**：Qwen-VL 负责图片/PDF 中手写内容和版面信息的识别与结构化输出
- **认知层**：DeepSeek 负责基于 perception IR 与 rubric 的逻辑评估、报告生成和解释

这种分层设计让模型替换、路由治理、故障降级和后续实验更容易收口。

### 3. 异步批量处理

系统采用 FastAPI + Celery + Redis 组合，将“上传入口”和“批改执行”解耦，支持：

- 单学生多页提交
- 多学生批量提交
- 队列异步执行
- Redis 不可用时的本地后台回退
- 任务取消、进度更新、ETA 估算

### 4. 实时状态回传

前端可通过 **SSE + Redis Pub/Sub** 获取实时状态流，及时看到：

- 当前任务状态
- 批量进度
- 已完成结果数
- 最终报告是否可查看

### 5. 教师复核闭环

系统不是单纯给分，而是具备完整的复核链路：

- 不可读 / 空白 / 异常样本拦截
- 人工复核状态流转
- 教师修正意见与评分写回
- 标注资产沉淀，支持后续优化与数据集建设

### 6. Prompt 与运行时治理

项目内建了较完整的模型治理与 prompt 控制能力，包括：

- Prompt 资产文件化管理
- L1 / L2 缓存
- A/B 配置与强制 variant
- Last Known Good 回退
- Runtime Router 自动模型路由
- Circuit Breaker 熔断与恢复
- 运行时遥测与 Ops 控制面

---

## 典型流程

```text
教师上传参考答案 / 学生作答
  -> FastAPI API Gateway
  -> Storage Adapter（Local / S3）
  -> 创建任务（PENDING）
  -> Celery Worker 异步执行
  -> 文件预处理（图片归一化 / PDF 转图）
  -> 感知层（Qwen-VL）
  -> 认知层（DeepSeek）
  -> 结果落库（tasks / grading_results / telemetry / audit）
  -> Redis Pub/Sub 推送状态
  -> SSE / 轮询查询
  -> 报告页 / 历史页 / 复核工作台展示
```

---

## 系统组成

### 后端服务

| 模块 | 说明 |
| --- | --- |
| `src/main.py` | FastAPI 入口、异常处理、中间件、静态页面路由 |
| `src/api/routers/` | 按领域拆分的 API：auth / rubric / grade / review / meta / ops / skills |
| `src/orchestration/workflow.py` | 感知 → 认知主业务编排 |
| `src/perception/` | 多模态感知识别层 |
| `src/cognitive/` | 评分、报告与 rubric 生成层 |
| `src/worker/main.py` | Celery Worker 批改执行引擎 |
| `src/prompts/` | Prompt Provider、缓存、A/B 与失效广播 |
| `src/core/` | 配置、追踪、熔断、运行时路由、存储适配等基础设施 |
| `src/db/` | SQLite schema 与数据访问层 |
| `src/skills/` | 外部 layout / validation skill 扩展接口 |

### 前端页面

项目内置了一组用于教师工作流和运维演示的静态页面：

- `/student-console`：单份作答提交
- `/student-console-batch`：批量提交
- `/task-progress`：任务实时进度
- `/tasks-list`：任务列表
- `/history-results`：历史结果
- `/report-view`：单份报告查看
- `/review-console`：复核工作台
- `/class-dashboard`：班级看板
- `/ops-console`：运行时控制与观测台

---

## API 分组

所有主接口统一挂载在 `/api/v1` 下，按业务拆分为以下几组：

| 路由组 | 作用 |
| --- | --- |
| `auth` | 教师登录与身份探测 |
| `rubric` | rubric 生成、查询与复用 |
| `grade` | 批改任务提交、状态查询、结果获取、批量处理 |
| `review` | 复核任务、标注资产、卫生拦截处理 |
| `meta` | 运行时 dashboard、SLA、数据管线、能力目录 |
| `ops` | 模型切换、prompt 控制、A/B、熔断演练、队列诊断 |
| `skills` | 外部 layout / validation skill 网关 |

---

## 数据与状态沉淀

默认数据库为 SQLite，核心数据表覆盖了任务流转、结果、治理和数据资产：

- `tasks`
- `grading_results`
- `rubrics`
- `rubric_generate_audit`
- `task_runtime_telemetry`
- `prompt_control_state`
- `prompt_ab_configs`
- `prompt_ops_audit_log`
- `hygiene_interception_log`
- `golden_annotation_assets`
- `teacher_review_decisions`
- `skill_validation_records`

这意味着系统不仅能“出结果”，还能积累后续优化所需要的运行与标注数据。

---

## 技术栈

| 层次 | 技术 |
| --- | --- |
| Web API | FastAPI, Uvicorn |
| 异步任务 | Celery, Redis |
| 数据存储 | SQLite, aiosqlite |
| 多模态 / LLM | Qwen-VL, DeepSeek, OpenAI-compatible SDK |
| 图像 / PDF | Pillow, PyMuPDF |
| 实时状态 | sse-starlette, Redis Pub/Sub |
| 鉴权 / 限流 | PyJWT, SlowAPI |
| 对象存储扩展 | boto3 |
| 测试 | pytest, pytest-asyncio, fakeredis, moto |
| 部署 | Docker, Docker Compose, Nginx |

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，至少补齐以下配置：

```env
QWEN_API_KEYS=sk-xxx
DEEPSEEK_API_KEYS=sk-xxx
REDIS_HOST=localhost
REDIS_PORT=6379
SQLITE_DB_PATH=outputs/grading_database.db
AUTH_ENABLED=false
```

`.env.example` 中还包含：

- 批处理并发参数
- SSE 心跳参数
- Prompt token 预算
- Runtime Router 开关
- Skills 网关配置
- Nginx 端口配置

### 3. 启动 API

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 15
```

### 4. 启动 Worker

```bash
# Linux / macOS
celery -A src.worker.main worker --loglevel=info --concurrency=4

# Windows
celery -A src.worker.main worker --loglevel=info --pool=solo --concurrency=1
```

### 5. 使用 Docker Compose

```bash
docker compose up --build
```

默认会启动 4 个服务：

1. `nginx`：反向代理
2. `grader-api`：FastAPI 主入口
3. `grader-worker`：Celery Worker
4. `redis`：消息队列与状态中转

---

## Docker 部署形态

`docker-compose.yml` 提供了一套单机可运行的部署方式：

- `nginx` 负责统一入口与反向代理
- `grader-api` 仅在容器内部暴露 8000
- `grader-worker` 执行实际批改任务
- `redis` 同时承担 Celery broker、缓存与 Pub/Sub
- `outputs/` 与 `data/` 通过 volume 挂载持久化

`Dockerfile` 基于 `python:3.11-slim` 构建，默认启动命令为：

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 1
```

---

## 测试

```bash
pytest -q
```

测试覆盖了：

- API 集成
- workflow 编排
- perception / cognitive 工厂与 mock
- prompt provider
- runtime router
- SSE / circuit breaker / serialization 等阶段特性

---

## 目录结构

```text
homework_grader_system/
├─ src/
│  ├─ api/
│  ├─ cognitive/
│  ├─ core/
│  ├─ db/
│  ├─ orchestration/
│  ├─ perception/
│  ├─ prompts/
│  ├─ schemas/
│  ├─ skills/
│  └─ worker/
├─ configs/
│  └─ prompts/
├─ docs/
├─ tests/
├─ docker-compose.yml
├─ Dockerfile
├─ requirements.txt
└─ .env.example
```

---

## 文档导航

| 文档 | 用途 |
| --- | --- |
| `README.md` | 项目总览、能力结构、启动方式 |
| `EXECUTIVE_SUMMARY.md` | 快速了解项目全貌 |
| `AUDIT_REPORT.md` | 详细技术审计 |
| `INDEX.md` | 文档阅读入口 |
| `docs/product_strategy_cn.md` | 产品定位与市场口径 |
| `docs/deployment_guide_cn.md` | 部署与试点落地 |
| `docs/production_readiness_cn.md` | 上线前检查项 |
| `docs/postgresql_migration_plan_cn.md` | 数据库升级路线 |
| `docs/demo_script_cn.md` | 演示话术与展示脚本 |
| `docs/go_to_market_cn.md` | 对外沟通与试点策略 |

---

## 适合谁使用 / 接手

这份仓库适合以下几类人快速上手：

- 想搭建 AI 阅卷工作流的工程团队
- 正在做教师侧作业批改 / 复核产品的开发者
- 想研究多模态感知 + 评分编排的学生团队
- 需要一个可运行的 AI 教育项目基础盘来继续产品化、前端化或试点落地的人

---

## License

MIT
