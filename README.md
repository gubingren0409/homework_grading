# AI Homework Grading System

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Phase](https://img.shields.io/badge/Phase-D%20Complete-brightgreen.svg)](docs/handoffs/)

面向理科作业场景的多模态批改系统。当前代码已形成 **感知（Perception）+ 认知（Cognition）+ 异步编排（FastAPI + Celery + Redis）+ Prompt 控制面 + 复核运营 API** 的完整后端能力。

---

## 1. 当前完成度（按代码现状）

| 领域 | 状态 | 说明 |
| --- | --- | --- |
| Phase A（能力/契约/SLA） | 已完成 | 能力目录、契约目录、SLA 摘要接口均已上线 |
| Phase B（能力库扩展） | 已完成（核心） | 多 provider、路由策略、自动熔断控制已接入 |
| Phase C（数据与评测） | 已完成 | 数据集闭环指标、运行看板、回归矩阵自动化 |
| Phase D（前端支撑接口） | 已完成 | D1 学生台 / D2 复核台 / D3 运营台接口收口 |
| P0 技术债 | 已完成 | runtime telemetry 持久化 + 看板数据源硬化 |
| P1 技术债 | 已完成 | Prompt 热更新控制面 + A/B 配置与审计 |
| 外部 Skills 灰度 | 已落地基座 | Layout/Validation 网关可用，默认关闭外部调用 |

---

## 2. 架构总览

1. **API Gateway（`src/api`）**  
   负责任务提交、状态查询、SSE 推送、复核接口、运营接口与契约目录接口。

2. **异步 Worker（`src/worker`）**  
   任务入队后由 Celery 执行工作流，写入结果、快照、遥测并发布状态事件。

3. **编排层（`src/orchestration/workflow.py`）**  
   强制 Phase35 版面契约门禁（layout preprocess），输出感知/认知快照并维持区域锚点一致性。

4. **算法层（`src/perception` + `src/cognitive`）**  
   - 感知：`qwen` / `mock` provider  
   - 认知：DeepSeek 路由与降级策略（自动熔断 + token 阈值 + fallback）

5. **Prompt 控制面（`src/prompts`）**  
   L1/L2/LKG、失效广播、热更新、forced variant、A/B 灰度与审计链路。

6. **Skills 基座（`src/skills`）**  
   支持外接 Layout Parser 与 Validation Executor，带 fail-open 与记录落库。

7. **数据层（`src/db` + SQLite）**  
   任务状态、批改结果、标注资产、卫生拦截、运行遥测、Prompt 控制与审计持久化。

---

## 3. 目录概览（职责维度）

```text
homework_grader_system/
├── src/
│   ├── api/                # FastAPI 路由、依赖、SSE
│   ├── worker/             # Celery worker 与异步执行链路
│   ├── orchestration/      # 端到端流程编排（Phase35 门禁 + 快照）
│   ├── perception/         # 视觉感知引擎与 provider factory
│   ├── cognitive/          # 认知判分引擎与路由控制
│   ├── prompts/            # Prompt provider、缓存、A/B、失效机制
│   ├── skills/             # 外部 skill 适配与验证落库
│   ├── db/                 # schema 与数据访问层
│   ├── schemas/            # IR 与 API 数据模型
│   └── core/               # 配置、日志、限流与中间件
├── configs/prompts/        # Prompt 资产
├── scripts/                # 回归脚本、迁移脚本、验证脚本
├── tests/                  # API/工作流/策略/skills 回归测试
├── .github/workflows/      # CI（Phase C 矩阵、Prompt 预检）
├── docker-compose.yml
├── Dockerfile
└── README.md
```

---

## 4. 快速启动

### 4.1 本地运行

```bash
cd homework_grader_system
pip install -r requirements.txt
```

复制 `.env.example` 为 `.env`，再启动服务：

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

```bash
# Linux/macOS
celery -A src.worker.main worker --loglevel=info --concurrency=4

# Windows（推荐）
celery -A src.worker.main worker --loglevel=info --pool=solo --concurrency=1
```

### 4.2 Docker Compose

```bash
docker compose up --build
```

启动后：
- OpenAPI：`http://localhost:8000/docs`
- 健康检查：`GET /`

---

## 5. 关键配置项（含 Skills 说明）

常规核心配置：
- `QWEN_API_KEYS`
- `DEEPSEEK_API_KEYS`
- `PERCEPTION_PROVIDER`（当前支持：`qwen` / `mock`）
- `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB`
- `SQLITE_DB_PATH`
- `DEPLOYMENT_ENVIRONMENT`（`dev` / `staging` / `prod`）
- `FEATURE_FLAG_PROVIDER_SWITCH`
- `FEATURE_FLAG_PROMPT_CONTROL`
- `FEATURE_FLAG_ROUTER_CONTROL`

Skills 相关配置（见 `.env.example`）：
- `SKILL_LAYOUT_PARSER_ENABLED`
- `SKILL_LAYOUT_PARSER_PROVIDER`（`none` / `llamaparse` / `unstructured`）
- `SKILL_LAYOUT_PARSER_API_URL`
- `SKILL_VALIDATION_ENABLED`
- `SKILL_VALIDATION_PROVIDER`（`none` / `e2b`）
- `SKILL_VALIDATION_API_URL`

默认示例中：
- `SKILL_LAYOUT_PARSER_API_URL=http://127.0.0.1:8000/api/v1/skills/layout/parse`
- `SKILL_VALIDATION_API_URL=http://127.0.0.1:8000/api/v1/skills/validate`

这两个默认值指向**本服务内置 skill 网关接口**（用于本地联调与契约占位）；接入外部实际服务时，改为外部网关地址并设置对应 `*_PROVIDER` 与 `*_API_KEY`。

---

## 6. 接口总览（全局）

### 6.1 Rubric 领域
- `POST /api/v1/rubric/generate`
- `GET /api/v1/rubrics`
- `GET /api/v1/rubrics/{rubric_id}`

### 6.2 学生任务台（D1）
- `POST /api/v1/grade/submit`
- `GET /api/v1/grade/flow-guide`
- `GET /api/v1/grade/{task_id}`
- `GET /api/v1/tasks/{task_id}/stream`（SSE）
- `GET /api/v1/results`（支持 `task_id` 过滤）

### 6.3 复核与标注（D2）
- `GET /api/v1/tasks/pending-review`
- `GET /api/v1/review/pending-workbench`
- `GET /api/v1/review/flow-guide`
- `POST /api/v1/annotations/feedback`
- `GET /api/v1/annotations/assets`
- `GET /api/v1/review/annotation-assets`
- `GET /api/v1/review/annotation-assets/{asset_id}`
- `GET /api/v1/hygiene/interceptions`
- `POST /api/v1/hygiene/interceptions/{record_id}/action`
- `POST /api/v1/hygiene/interceptions/bulk-action`

### 6.4 运营与观测（D3 + Phase C + P0/P1）
- `POST /api/v1/trace/probe`
- `GET /api/v1/capabilities/catalog`
- `GET /api/v1/contracts/catalog`
- `GET /api/v1/sla/summary`
- `GET /api/v1/metrics/provider-benchmark`
- `GET /api/v1/router/policy`
- `GET /api/v1/metrics/dataset-pipeline`
- `GET /api/v1/metrics/runtime-dashboard`
- `POST /api/v1/prompt/control`
- `POST /api/v1/prompt/ab-config`
- `POST /api/v1/prompt/refresh`
- `POST /api/v1/prompt/invalidate`
- `GET /api/v1/prompt/state`
- `GET /api/v1/prompt/audit`
- `GET /api/v1/ops/config/snapshot`
- `GET /api/v1/ops/feature-flags`
- `POST /api/v1/ops/feature-flags`
- `POST /api/v1/ops/provider/switch`
- `POST /api/v1/ops/router/control`
- `GET /api/v1/ops/prompt/catalog`
- `GET /api/v1/ops/audit/logs`

### 6.5 Skills 内部网关
- `POST /api/v1/skills/layout/parse`
- `POST /api/v1/skills/validate`

---

## 7. 核心状态与契约要点

- `task.status`: `PENDING | PROCESSING | COMPLETED | FAILED`
- `task.grading_status`: `SCORED | REJECTED_UNREADABLE`
- `task.review_status`: `NOT_REQUIRED | PENDING_REVIEW | REVIEWED`

统一错误契约采用结构化字段（例如 `error_code/retryable/retry_hint/next_action`），完整模型可通过：
- `GET /api/v1/contracts/catalog`

---

## 8. 核心数据表

- `tasks`
- `grading_results`
- `rubrics`
- `hygiene_interception_log`
- `golden_annotation_assets`
- `task_runtime_telemetry`
- `prompt_control_state`
- `prompt_ab_configs`
- `prompt_ops_audit_log`
- `skill_validation_records`

DDL 位于：`src/db/schema.sql`

---

## 9. 测试与回归

全量测试：

```bash
pytest tests/ -v
```

常用聚焦：

```bash
pytest tests/test_api.py tests/test_phase38_domain_split_api.py -q
pytest tests/test_prompt_provider_foundation.py tests/test_prompt_control_db.py -q
pytest tests/test_runtime_telemetry_db.py tests/test_runtime_router.py -q
```

Phase C 回归矩阵：

```bash
python scripts/run_phasec_regression_matrix.py
```

CI 工作流：
- `.github/workflows/phasec-regression-matrix.yml`
- `.github/workflows/prompt-assets-preflight.yml`

---

## 10. 运行与提交流程说明

- 运行产物目录（`outputs/`, `data/uploads/`）默认不纳入版本提交。
- Windows 环境建议使用 `celery --pool=solo` 提高稳定性。
- 若走代理，建议配置 `NO_PROXY` 以降低模型 API 连接抖动。
