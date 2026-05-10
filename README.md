# AI 自动作业批改系统

面向中学教师的 AI 阅卷与复核后端系统，覆盖 **评分标准生成、学生作答识别、异步批改、整卷切题、人工复核、结果沉淀** 这条完整链路。

> 教师参考答案 / 整卷参考卷
> → Rubric / RubricBundle
> → 学生单题 / 批量 / 整卷提交
> → Qwen 感知层 + DeepSeek 认知层
> → 结果落库、状态流推送、报告查看、人工复核

---

## 1. 项目定位

这不是一个单次调用模型的 demo，也不是单纯的 OCR 脚本，而是一套围绕教师批改场景构建的工程化系统：

- **Rubric 驱动评分**：先生成评分标准，再基于评分标准判分
- **感知 / 认知解耦**：将 OCR/版面识别与逻辑评分拆分为两层
- **异步任务执行**：FastAPI 负责入口，Celery Worker 负责执行
- **整卷链路可复用旧单题能力**：按父题粒度切题，再复用稳定评分基线
- **具备复核与治理闭环**：异常任务、低置信度结果、运行时遥测可持续沉淀

---

## 2. 当前支持的工作流

### 2.1 单题 / 多图参考答案生成 Rubric

- 教师上传参考答案图片或 PDF
- 感知层抽取文字与结构
- 认知层生成 `TeacherRubric`
- 持久化后可被后续学生批改任务复用

### 2.2 单份学生作答批改

- 教师上传单份学生作答
- 系统异步入队并执行批改
- 输出扣分项、反馈、证据片段、状态流

### 2.3 批量学生作答批改

- 支持多名学生单页作答批量提交
- 支持进度、取消、状态查询、历史结果查看
- Redis 不可用时可自动回退到本地后台执行

### 2.4 整卷参考答案生成 RubricBundle

- 从整卷参考答案中提取题号树、题目关系和题级 rubric
- 输出并持久化 `RubricBundle`
- 支持按题号选择子集进行后续批改

### 2.5 整卷学生作答批改

- 支持整页输入、题号锚点定位、答题区切分、父题级评分
- 切题后仍复用既有单题 contract 与评分逻辑
- 支持同步接口和异步任务接口两种入口

---

## 3. 核心架构

### 3.1 分层设计

| 层 | 主要职责 |
| --- | --- |
| API Gateway | 上传入口、任务创建、状态查询、结果聚合、静态控制台 |
| Worker | 异步任务执行、状态写回、Pub/Sub 推送 |
| Perception | Qwen OCR、答题区识别、题号锚点、版面结构解析 |
| Orchestration | 单题工作流、整卷工作流、student answer contract、切题逻辑 |
| Cognitive | DeepSeek 评分、Rubric 生成、反馈与解释 |
| DB / Storage | 任务、结果、rubric、bundle、paper report、运行时遥测 |
| Ops / Prompt | Prompt 资产管理、缓存、运行时路由、熔断与回退 |

### 3.2 整卷主链路

整卷链路的关键模块如下：

- `RubricBundleWorkflow`：整卷参考答案 → `RubricBundle`
- `QuestionAnchorDetector`：从整页识别题号锚点
- `AnswerRegionSplitter`：依据锚点与 layout 切出 `StudentAnswerRegion`
- `StudentAnswerBundle`：把 printed / handwritten / student answer 收敛到统一 contract
- `PaperGradingWorkflow`：以父题为最小认知单元执行整卷评分

### 3.3 设计约束

当前整卷实现遵循以下约束：

1. **旧单题链路是质量基线**
2. **父题是最小认知评分单元**
3. **printed reference / handwritten reference / student answer 最终收敛到同一套 contract**
4. **batch 只能是性能优化，不能改变 OCR prompt 语义、输入变量、输出结构或下游解释方式**
5. **禁止把多道父题打包到一次认知调用**

---

## 4. 主要接口

所有主接口统一挂载在 `/api/v1` 下。

### 4.1 Rubric 相关

- `POST /api/v1/rubric/generate`：单题参考答案生成 `TeacherRubric`
- `POST /api/v1/rubric/bundle/generate`：整卷参考答案生成 `RubricBundle`

### 4.2 学生批改相关

- `POST /api/v1/grade/submit`：单份学生作答异步提交
- `POST /api/v1/grade/submit-batch`：批量单页异步提交
- `POST /api/v1/grade/submit-batch-with-reference`：带参考答案的批量异步提交
- `GET /api/v1/grade/{task_id}`：任务状态查询
- `GET /api/v1/grade/{task_id}/report`：批改报告
- `GET /api/v1/grade/{task_id}/insights`：任务洞察
- `POST /api/v1/grade/{task_id}/cancel`：取消任务

### 4.3 整卷相关

- `POST /api/v1/grade/paper`：整卷同步评分
- `POST /api/v1/grade/paper/submit`：整卷异步提交
- `GET /api/v1/grade/paper/reports`：整卷报告查询
- `GET /api/v1/grade/paper/inputs`：整卷输入回看

---

## 5. 内置页面

项目内置了一组用于教师演示、调试与运维的静态页面：

- `/student-console`
- `/student-console-batch`
- `/whole-paper-console`
- `/whole-paper-report`
- `/review-console`
- `/ops-console`
- `/tasks-list`
- `/task-progress`
- `/class-dashboard`
- `/history-results`
- `/report-view`

---

## 6. 目录结构

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
├─ scripts/
├─ tests/
├─ docker-compose.yml
├─ Dockerfile
├─ requirements.txt
└─ .env.example
```

---

## 7. 技术栈

| 类别 | 技术 |
| --- | --- |
| Web API | FastAPI, Uvicorn |
| 异步任务 | Celery, Redis |
| 数据存储 | SQLite, aiosqlite |
| 模型接入 | Qwen-VL, DeepSeek, OpenAI-compatible SDK |
| 图像 / PDF | Pillow, PyMuPDF |
| 实时状态 | SSE, Redis Pub/Sub |
| 鉴权 / 限流 | PyJWT, SlowAPI |
| 对象存储扩展 | boto3 |
| 测试 | pytest, pytest-asyncio, fakeredis, moto |
| 部署 | Docker, Docker Compose, Nginx |

---

## 8. 快速开始

### 8.1 安装依赖

```bash
pip install -r requirements.txt
```

### 8.2 配置环境变量

复制 `.env.example` 为 `.env`，至少补齐以下配置：

```env
QWEN_API_KEYS=sk-xxx
DEEPSEEK_API_KEYS=sk-xxx
REDIS_HOST=localhost
REDIS_PORT=6379
SQLITE_DB_PATH=outputs/grading_database.db
AUTH_ENABLED=false
```

如果要启用整卷切题，建议同时打开 layout skill：

```env
SKILL_LAYOUT_PARSER_ENABLED=true
SKILL_LAYOUT_PARSER_PROVIDER=mineru
SKILL_LAYOUT_PARSER_API_URL=http://127.0.0.1:30000
SKILL_LAYOUT_PARSER_TIMEOUT_SECONDS=20
```

`.env.example` 还包含以下关键配置：

- Qwen / DeepSeek key 池与模型名
- 并发与批处理参数
- SSE 超时与心跳
- Runtime Router / Circuit Breaker
- Skills 网关参数
- Nginx 端口与鉴权开关

### 8.3 启动 API

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 15
```

### 8.4 启动 Worker

```bash
# Linux / macOS
celery -A src.worker.main worker --loglevel=info --concurrency=4

# Windows
celery -A src.worker.main worker --loglevel=info --pool=solo --concurrency=1
```

### 8.5 使用 Docker Compose

```bash
docker compose up --build
```

默认会启动：

1. `nginx`
2. `grader-api`
3. `grader-worker`
4. `redis`

---

## 9. 数据沉淀

系统默认使用 SQLite，已覆盖以下几类核心对象：

- 任务与状态：`tasks`
- 单题结果：`grading_results`
- 整卷任务与题级结果：`paper_tasks`、`paper_question_results`
- Rubric / Bundle：`rubrics`、`rubric_bundles`
- Prompt 与运行治理：`prompt_control_state`、`prompt_ab_configs`、`prompt_ops_audit_log`
- 运行时遥测：`task_runtime_telemetry`
- 复核与标注：`teacher_review_decisions`、`golden_annotation_assets`

这意味着系统不只是“给出一次结果”，还会沉淀后续优化所需的任务、运行和复核数据。

---

## 10. 当前状态与边界

### 10.1 已经具备的能力

- 单题 rubric 生成与学生批改链路可用
- 批量异步任务、状态流、取消、结果查询可用
- 整卷 `RubricBundle` 生成可用
- 整卷切题、父题级评分、worker 持久化链路已接通
- `whole-paper-console` 与 `whole-paper-report` 页面已接入

### 10.2 当前建议的使用定位

整卷链路更适合 **内测、联调、灰度验证**，而不是直接视作完全稳定的正式交付版本。

### 10.3 当前主要技术风险

- 解答题 student tag / worked-solution block 召回仍不稳定
- 填空题 OCR 噪声词可能直接进入 `slot_answers`
- 整卷真实整页样本的稳定性验证仍需要继续补强
- batch 路径目前更多是性能框架，仍需持续用真实样本回归

---

## 11. 测试

```bash
pytest -q
```

当前测试覆盖以下方面：

- API 集成
- Worker / DB 适配
- Prompt Provider
- Runtime Router / Circuit Breaker
- Question Tree / Segmentation / Student Answer Bundle
- Whole-paper Workflow / Bundle Workflow
- Qwen / DeepSeek engine 的关键 contract

---

## 12. 文档导航

| 文档 | 用途 |
| --- | --- |
| `README.md` | 项目总览、能力边界、启动方式 |
| `EXECUTIVE_SUMMARY.md` | 快速了解项目全貌 |
| `AUDIT_REPORT.md` | 历史技术审计与结构风险说明 |
| `INDEX.md` | 文档导航入口 |
| `docs/` | 产品、部署、试点、复盘等专题文档 |

---

## 13. 说明

- `.env` 默认已被 `.gitignore` 忽略，不应提交到版本库
- Windows 上运行 Celery Worker 时请使用 `--pool=solo`
- 若 Redis 不可用，部分入口会回退到本地后台执行，但正式环境仍建议提供 Redis
