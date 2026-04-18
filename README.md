# AI 自动作业批改系统

面向**中学理科教师**的 AI 阅卷与复核工作台后端。当前仓库已经具备一条真实可运行的后端主链路：

> **教师上传参考答案/学生作答 -> 创建任务 -> 异步批改 -> 感知识别 -> 认知评分 -> 结果落库 -> SSE/轮询回传 -> 报告/复核页展示**

它已经明显超过“接口 demo”，但还没有收束成“成熟可交付 SaaS”。更准确地说，它处在：

> **后端能力完整度较高，教师工作流前端已完成第一轮收口，当前主要缺口转向部署治理、交付包装与更深层结构拆分**

---

## 1. 我对当前项目的判断

### 最合理的当前定位

> **教师侧 AI 阅卷与复核工作台**

适合先解决这些问题：

1. 教师上传标准答案，自动生成或复用 rubric。
2. 教师批量上传学生作答，系统自动评分。
3. 系统自动筛出低置信度、异常、不可读样本进入复核。
4. 教师查看证据化报告，并写回反馈。
5. 系统沉淀 rubric、标注资产、运行遥测和 prompt 运营数据。

### 不建议现在这样定义

1. 学生直接使用的 AI 学习 App
2. 完整学校教务平台
3. 通用 OCR / 文档理解平台
4. 全学科通用教育大模型产品

这些方向未来都能扩，但**和当前代码的最强能力不完全匹配**。

---

## 2. 仓库真实结构

这个目录里其实同时存在两套东西：

| 区域 | 作用 | 判断 |
| --- | --- | --- |
| 根目录 `main.py`、`vlm_client.py`、`image_processor.py` 等 | 早期原型/实验脚本 | **历史遗留，不是当前主系统入口** |
| `homework_grader_system/` | FastAPI + Celery + Redis + SQLite 的主系统 | **当前应视为唯一主工程** |

如果你要重新接管项目，**请直接把 `homework_grader_system/` 当作主仓库看**，不要先从根目录原型脚本入手。

---

## 3. 当前系统主链路

```text
教师上传参考答案 / 学生作答
  -> FastAPI API Gateway
  -> 文件存储（LocalStorage / S3 适配层）
  -> create_task(status=PENDING)
  -> Celery worker / 本地后台回退
  -> 文件预处理（图片归一化 / PDF 转图）
  -> 感知层（Qwen-VL）
  -> 认知层（DeepSeek）
  -> 结果落库（tasks / grading_results / telemetry / audit）
  -> Redis Pub/Sub 推送状态
  -> SSE / 轮询查询
  -> 报告 DTO
  -> 报告页 / 历史页 / 复核页
```

---

## 4. 代码结构与职责

| 目录/文件 | 职责 | 审计判断 |
| --- | --- | --- |
| `src/main.py` | FastAPI 入口、异常处理、静态页面路由 | 清晰，适合作为维护入口 |
| `src/api/routes.py` | 核心 API 集中入口 | 功能全，但已膨胀到 **3120 行 / 54 个端点** |
| `src/api/sse.py` | SSE 状态流、Redis Pub/Sub 订阅 | 实现清楚，工程化程度不错 |
| `src/worker/main.py` | Celery worker、批处理编排、落库、Pub/Sub、DLQ | 核心价值区，也是主要维护风险点 |
| `src/orchestration/workflow.py` | 感知 -> 认知的业务编排层 | 当前最清楚、最值得先读的业务文件之一 |
| `src/perception/` | Qwen 感知层、布局识别 | 已从 OCR 升级为结构化 IR 输出 |
| `src/cognitive/` | DeepSeek 评分、降级、JSON 修复、遥测 | 能力强，但复杂度高 |
| `src/prompts/` | Prompt 资产、缓存、A/B、LKG、失效广播 | 平台化能力很强，是重要资产 |
| `src/db/` | SQLite schema + DAO | 数据覆盖完整，但 `client.py` 已达 **2018 行** |
| `src/skills/` | 外部 layout/validation skill 基座 | 可扩展钩子已具备，但默认仍偏可选能力 |
| `src/api/static/` | 现有前端页面 | 已能串通链路，但仍是工具台形态 |

---

## 5. 当前完成情况

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| 单学生多页提交 | **已完成** | 图片/PDF 均可，多页汇总评分 |
| 多学生单页批处理 | **已完成** | 单任务内并发处理多个学生文件 |
| one-shot 批量入口 | **已完成** | 可一次提交参考答案与学生作答 |
| rubric 生成与复用 | **已完成** | 支持内容指纹去重 |
| 队列与本地回退 | **已完成** | Redis/Celery 不可用时能回退本地执行 |
| SSE 实时回传 | **已完成** | 依赖 Redis Pub/Sub |
| 报告 API | **已完成** | 任务、历史、报告读取链路齐全 |
| 复核后端 | **已完成** | 待复核、标注资产、教师反馈写回已打通 |
| Prompt 控制面 | **已完成** | L1/L2 缓存、A/B、forced variant、LKG、invalidate |
| 运行时路由与熔断 | **已完成** | runtime router + circuit breaker 已接入 |
| 运营/观测 API | **已完成** | runtime dashboard、queue diagnostics 等已具备 |
| 前端产品化 | **第一轮已完成** | 教师任务创建、进度跟踪、班级看板、单份报告、复核工作台已串成完整工作流，当前仍以静态页形态交付 |
| 部署治理 | **进行中** | Docker Compose 可运行，Nginx/HTTPS/云部署未收口 |

---

## 6. 系统最强的部分

### 6.1 后端闭环已经做出来了

它不是“调一下模型接口”的项目，而是已经把这些环节全部串起来：

1. 任务状态
2. 异步队列
3. 本地回退
4. 报告落库
5. SSE 状态推送
6. 复核链路
7. prompt 控制
8. runtime telemetry
9. 数据卫生拦截
10. 标注资产沉淀

### 6.2 平台化雏形已经成立

`src/prompts/provider.py`、`src/core/runtime_router.py`、`src/skills/`、`src/db/schema.sql` 说明这套系统已经有“**可配置、可治理、可实验**”的平台底子。

### 6.3 数据模型不是临时拼的

数据库不仅有 `tasks` / `grading_results`，还有：

1. `task_runtime_telemetry`
2. `prompt_control_state`
3. `prompt_ab_configs`
4. `prompt_ops_audit_log`
5. `hygiene_interception_log`
6. `golden_annotation_assets`
7. `skill_validation_records`
8. `rubrics`
9. `rubric_generate_audit`

这意味着系统已经开始从“功能脚本”进入“运营系统”。

---

## 7. 当前最伤维护者的地方

### 7.1 主工程与原型代码并存

仓库根目录保留了一批早期脚本，而真实系统在 `homework_grader_system/` 子目录。  
这会直接增加接手者的认知噪音。

### 7.2 巨型文件已经出现

当前最典型的三个“认知黑洞”：

1. `src/api/routes.py`
2. `src/worker/main.py`
3. `src/db/client.py`

它们不是写坏了，而是因为 phase 式持续叠加导致**职责越来越多但还没完成模块化回收**。

### 7.3 前端已能支撑教师工作流，但交付壳仍偏轻

前端已经不再只是上传工具页，而是能覆盖：  
任务创建 -> 进度跟踪 -> 班级汇总 -> 单份报告 -> 异常复核。  
但它仍然是静态 HTML 交付形态，离成熟 SaaS 前端还有一层“统一设计系统 / 登录权限 / 部署外壳 / 试点包装”。

### 7.4 文档历史包袱偏重

`docs/handoffs/` 对追历史有价值，但不适合作为当前入口。  
如果维护者先读这些阶段文档，很容易重新陷入 phase 细节，反而看不清现状。

---

## 8. 实际测试基线

本轮围绕教师工作流与 worker 主链路做的针对性回归基线为：

- **52 passed**

已覆盖的关键变更包括：

1. worker 事件循环兼容性修复；
2. 教师任务创建页内嵌进度摘要与结果跳转；
3. 独立进度页布局修复；
4. 结果页原图预览；
5. 批处理 `result_count` 按单份递增；
6. `eta_seconds` 从固定常量改为动态估算。

已用真实数据再次验证：

1. `data\3.20_physics\question_13\students` 可正常完成批量任务；
2. 状态接口中的 `result_count` 会在处理中递增，不再卡在 `0/N` 直到结束；
3. 报告接口可返回原始作答图片预览链接；
4. 当前 worker 在线，教师端主链路可继续使用。

---

## 9. 当前前端实际情况

现有页面：

1. `/student-console`
2. `/student-console-batch`
3. `/review-console`
4. `/ops-console`
5. `/tasks-list`
6. `/history-results`
7. `/report-view`

以及一个历史保留页文件：`review_console.html`。

这些页面的真实意义是：

- **优点**：它们能把后端主链路跑通，适合联调、验流程、演示能力。
- **不足**：它们仍是“工程控制台”，不是教师长期愿意用的产品前端。

---

## 10. 推荐维护阅读顺序

如果你要重新掌握项目，建议按这个顺序读：

1. `README.md`
2. `EXECUTIVE_SUMMARY.md`
3. `AUDIT_REPORT.md`
4. `src/main.py`
5. `src/api/routes.py`
6. `src/worker/main.py`
7. `src/orchestration/workflow.py`
8. `src/cognitive/engines/deepseek_engine.py`
9. `src/perception/engines/qwen_engine.py`
10. `src/prompts/provider.py`
11. `src/db/schema.sql`
12. `docs/product_strategy_cn.md`

---

## 11. 本地运行

### 安装依赖

```bash
cd homework_grader_system
pip install -r requirements.txt
```

### 启动 API

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 15
```

### 启动 Worker

```bash
# Linux/macOS
celery -A src.worker.main worker --loglevel=info --concurrency=4

# Windows
celery -A src.worker.main worker --loglevel=info --pool=solo --concurrency=1
```

### Docker Compose

```bash
docker compose up --build
```

默认服务（4 个）：

1. `nginx` — 反向代理与 HTTPS 终止（对外暴露 80/443）
2. `grader-api` — FastAPI 主入口（仅容器内部暴露）
3. `grader-worker` — Celery 批改引擎
4. `redis` — 消息队列与状态中转

启动后访问 `http://localhost/` 即可看到产品首页，通过 `/login` 登录教师工作台。

> **Windows 本地开发注意**：Docker Worker 与 SQLite 存在 WSL2 文件锁兼容问题。建议仅 Redis 使用 Docker，API 和 Worker 在本地直接运行。详见 `docs/deployment_guide_cn.md` 8.7 节。

---

## 12. 测试方式

建议这样跑测试：

```bash
pytest -q
```

---

## 13. 当前最值得做的三件事

1. **部署与交付外壳收口**  
   把当前可运行系统继续推进到“可试点交付”：反向代理、HTTPS、最小账号体系、运维脚本、部署模板。

2. **技术减认知负担**  
   优先拆解 `routes.py` / `worker/main.py` / `db/client.py` 的阅读与维护入口。

3. **试点包装与市场落地**  
   明确卖点是“批量阅卷 + 复核提效 + 讲评依据生成”，并把它落实到首页文案、演示话术、试点交付材料。

---

## 14. 文档导航

1. `EXECUTIVE_SUMMARY.md`：适合快速看结论
2. `AUDIT_REPORT.md`：适合看详细技术判断
3. `docs/product_strategy_cn.md`：适合看市场、交互、前端建议
4. `docs/deployment_guide_cn.md`：适合看单机/试点部署路径
5. `docs/production_readiness_cn.md`：适合看生产前检查清单与单点风险
6. `docs/postgresql_migration_plan_cn.md`：适合看数据库升级路线
7. `docs/demo_script_cn.md`：适合看 5-10 分钟演示脚本
8. `docs/go_to_market_cn.md`：适合看对外口径、试点沟通与 FAQ
9. `INDEX.md`：适合重新建立阅读路径

如果你现在的目标是**重新获得对项目的掌控感**，建议先读这些文档，再回到代码。
