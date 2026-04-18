# 详细技术审计报告

## 1. 审计范围与方法

本轮审计不是只看说明文档，而是结合实际代码和测试基线做判断。已审计内容包括：

1. `src/main.py`
2. `src/api/routes.py`
3. `src/api/sse.py`
4. `src/worker/main.py`
5. `src/orchestration/workflow.py`
6. `src/perception/engines/qwen_engine.py`
7. `src/cognitive/engines/deepseek_engine.py`
8. `src/prompts/provider.py`
9. `src/core/*`
10. `src/db/schema.sql` 与 `src/db/client.py`
11. `src/schemas/*`
12. `src/skills/*`
13. `src/api/static/*`
14. `configs/prompts/*`
15. `docker-compose.yml`、`Dockerfile`
16. `tests/`
17. 仓库根目录的历史原型脚本

审计目标：

1. 这套系统现在到底实现到了哪一步？
2. 哪些部分已经稳定？
3. 哪些地方最伤维护者？
4. 它更像什么产品，而不是什么产品？

---

## 2. 仓库结构的真实问题：主系统与原型并存

从目录视角看，这个仓库并不完全干净：

| 区域 | 内容 | 影响 |
| --- | --- | --- |
| 根目录 | `main.py`、`vlm_client.py`、`image_processor.py` 等 | 早期原型仍在，容易误导阅读顺序 |
| `homework_grader_system/` | FastAPI + Celery + Redis + SQLite 主系统 | 当前真实主工程 |

### 审计结论

这不是简单的“目录有点乱”，而是一个实打实的认知风险：

> **维护者如果先看错入口，就会对系统复杂度和现状形成错误判断。**

建议后续把“主工程在 `homework_grader_system/`”这件事在所有外部说明里写得更明确。

---

## 3. 主系统总体架构判断

### 3.1 这是多层系统，不是单接口项目

核心链路如下：

```text
FastAPI API Gateway
  -> 存储适配层
  -> Redis / Celery 异步执行
  -> Workflow 编排
  -> Qwen 感知
  -> DeepSeek 认知
  -> Prompt Provider / Runtime Router / Skills
  -> SQLite 持久化
  -> SSE / 报告 API / 复核 API / 运维 API
```

### 3.2 审计判断

当前代码最强的地方不是某一个模型调用，而是**把一整条批改工作流系统化了**。

---

## 4. 模块级审计

## 4.1 `src/main.py`

### 作用

1. FastAPI 应用入口
2. 中间件注册
3. 异常处理
4. 静态页面路由
5. prompt provider 生命周期

### 判断

这是当前最适合维护者切入的文件之一：短、清楚、边界明确。

### 风险

1. 仍在使用 `@app.on_event("startup"/"shutdown")`
2. 需要后续迁移到 FastAPI lifespan

---

## 4.2 `src/api/routes.py`

### 现状

1. 文件体量约 **3120 行**
2. 约 **54 个 API 端点**
3. 覆盖：
   - rubric
   - 提交
   - 批处理
   - 任务状态
   - 报告
   - 复核
   - annotation
   - hygiene
   - prompt control
   - metrics / ops

### 优点

1. 功能确实完整
2. 当前 API 面已经能支撑真实工作台后端
3. 并不是“很多空接口”，多数端点都对应真实数据与链路

### 风险

1. 业务路由、运营路由、平台路由混在一个文件
2. 维护者很难从文件结构快速建立模块边界
3. 继续堆功能会显著放大认知负担

### 结论

`routes.py` 不是坏代码，但已经到了**必须文档化入口、适时拆分**的阶段。

---

## 4.3 `src/worker/main.py`

### 现状

这个文件承担了太多核心职责：

1. Celery 应用配置
2. 任务执行
3. 进度投影
4. Pub/Sub 状态广播
5. 批处理并发
6. auto-rubric
7. hygiene 记录
8. runtime telemetry 写入
9. 外部 validation skill 触发
10. DLQ

### 优点

1. 批量流程是真的打通了
2. 进度更新和状态投影是有设计感的
3. one-shot 批量入口不是假壳，worker 端有完整接收逻辑
4. 单学生与批处理两条路径都能落库并发布状态

### 风险

1. 这是当前最容易“没人敢动”的文件
2. sync/async bridge 复杂，跨线程兼容性脆弱
3. 业务编排、错误处理、状态广播、审计写入耦合较深

### 本轮确认到的真实缺口

`run_async()` 在 Windows/Python 3.12 的非主线程环境下存在 event loop 获取失败问题，已经由测试直接暴露：

```text
RuntimeError: There is no current event loop in thread 'ThreadPoolExecutor-0_0'
```

### 结论

这是后端最关键的资产区，也是最需要后续“降认知复杂度”的区域。

---

## 4.4 `src/orchestration/workflow.py`

### 特点

1. 体量小
2. 数据流清楚
3. 负责“整页感知聚合 -> 认知评分”

### 判断

这是当前**最健康、最适合成为业务主线说明书**的代码文件之一。

### 设计取向

它没有把复杂度过早放在切片、空间重建、版面图谱上，而是优先稳定完成“多页聚合 -> 评分”。

这个选择是合理的。

---

## 4.5 感知层：`src/perception/engines/qwen_engine.py`

### 已实现能力

1. key pool
2. circuit breaker
3. physical API semaphore
4. prompt provider 接入
5. JSON 提取与解码
6. 坐标 sanitize
7. layout extract 与 perception extract 双路径

### 关键价值

感知层已经不是简单 OCR，而是在输出统一结构化 IR：

- `PerceptionNode`
- `PerceptionOutput`
- `LayoutIR`

这会直接决定未来能否做：

1. 证据片段展示
2. 图像高亮定位
3. 空间化复核
4. 班级高频错误证据抽取

### 风险

1. 当前仍高度依赖上游模型返回 JSON 的稳定性
2. host 端做了不少“补偿式纠偏”，后续要持续维护

---

## 4.6 认知层：`src/cognitive/engines/deepseek_engine.py`

### 已实现能力

1. runtime router 决策
2. prompt provider 接入
3. stream / non-stream 切换
4. degrade 到 `deepseek-chat`
5. JSON 修复
6. runtime telemetry 采集

### 强项

它已经解决了很多真实线上问题：

1. 网络波动
2. 模型流式不稳定
3. 非法 JSON
4. token 预算
5. 自动降级

### 风险

复杂度已经明显高于普通模型调用模块。  
如果没有外围文档和测试保护，它会逐步变成“谁都不想碰”的文件。

### 额外观察

当前 prompt 已经显式加入：

1. 数值容差
2. 舍入规则
3. OCR 容忍
4. 跳步接受

这说明项目已经从“流程能跑”迈进了“判分规则可控”阶段。

---

## 4.7 Prompt 控制面：`src/prompts/provider.py`

### 能力清单

1. L1 内存缓存
2. L2 Redis 缓存
3. singleflight
4. forced variant
5. LKG fallback
6. A/B config
7. invalidate / refresh
8. token budget guard

### 判断

这是仓库平台化程度最高的模块之一。  
如果未来继续做“教师阅卷工作台 + 模型策略运营”，它会是长期资产。

### 风险

1. 对只想“快速做个阅卷器”的人来说会显得偏重
2. 需要明确说明它是平台层，而不是业务层

---

## 4.8 数据层：`src/db/schema.sql` / `src/db/client.py`

### 优点

数据库设计覆盖面明显超出最小 demo：

1. 任务
2. 批改结果
3. runtime telemetry
4. prompt control state
5. prompt audit
6. skill validation
7. rubric
8. rubric generate audit
9. hygiene interception
10. golden annotation assets

### 风险

1. SQLite 仍是单文件数据库
2. 并发写和运维能力有上限
3. `client.py` 体量过大，维护成本持续上升

### 结论

开发期与单机部署阶段，SQLite 是合理选择；  
如果进入多教师、多班级、长期在线场景，**PostgreSQL 迁移是迟早要做的。**

---

## 4.9 前端：`src/api/static/`

### 现状

已有页面 8 个左右，主要包括：

1. 学生提交台
2. 批处理台
3. 任务列表
4. 历史结果页
5. 报告展示页
6. 复核工作台
7. 运营控制台

### 优点

1. 不只是空壳页面，是真的能串接口
2. 适合联调、演示、验链路
3. 报告页已经有教师模式等雏形

### 不足

1. UI 仍是静态 HTML 工具台
2. 缺少统一设计语言与状态管理
3. 缺少班级汇总、题目统计、复核优先队列
4. 缺少更产品化的报告信息架构

### 结论

前端目前完成的是“系统能被使用”，还没有完成“教师会愿意长期使用”。

---

## 4.10 DevOps：Docker / Compose

### 已完成

1. `Dockerfile`
2. `docker-compose.yml`
3. API、worker、redis 三服务
4. healthcheck

### 未完成

1. Nginx 反向代理
2. HTTPS
3. 域名接入
4. 云部署模板
5. 生产级备份/恢复说明

### 结论

它已经达到“开发/演示可部署”，但还没达到“标准对外交付”。

---

## 5. 测试面审计

### 5.1 当前实际基线

使用：

```bash
set PYTHONPATH=.
pytest -q tests
```

得到结果：

- **166 passed**
- **1 failed**
- **3 skipped**

### 5.2 失败点

失败用例：

- `tests/test_phase30_serialization.py::test_worker_receives_dict_not_string`

根因：

- `src/worker/main.py` 的 `run_async()` 在非主线程下调用 `get_event_loop()` 失败。

### 5.3 额外观察

1. 测试覆盖面其实不差，包含 API、边界、序列化、Prompt、SSE、DLQ、runtime router、storage、skills 等。
2. 但直接裸跑 `pytest -q` 还会因为 `src` 导入路径问题在收集阶段报错，这说明测试入口说明仍不够稳妥。

---

## 6. 当前完成情况总表

| 方向 | 完成情况 |
| --- | --- |
| AI 批改主链路 | **已完成** |
| 异步执行与状态流 | **已完成** |
| rubric 生成与复用 | **已完成** |
| one-shot 批量编排 | **已完成** |
| 报告 DTO / 报告 API | **已完成** |
| 任务页 / 历史页 / 报告页 | **首版完成** |
| 复核后端与标注资产 | **已完成** |
| 复核前端体验 | **MVP** |
| 运营观测后端 | **已完成** |
| 运营前端 | **MVP** |
| 上线治理 | **未完成** |

---

## 7. 对维护者最重要的判断

### 7.1 这套代码的问题不是“没结构”

真实问题是：

> **结构存在，但结构被 phase 增量开发不断覆盖，导致总览入口不足。**

### 7.2 你现在最需要补的不是更多功能，而是“系统解释层”

包括：

1. 当前主线说明
2. 模块职责边界
3. 维护入口顺序
4. 产品边界口径

### 7.3 这套系统最可能成功的方向，不是“大而全”

最符合当前代码现实的方向是：

> **教师批量阅卷、争议样本复核、讲评依据生成**

这条线既利用了你已经做好的后端深度，也能避免产品边界继续失控。

---

## 8. 建议优先级

### P1：降认知负担

1. 为 routes / worker / db 明确子域边界
2. 把“主工程”和“历史原型”文档上切开
3. 固化维护阅读路径

### P2：前端产品化收口

1. 任务创建向导
2. 班级汇总看板
3. 学生报告升级
4. 复核优先队列

### P3：部署与交付收口

1. 生产部署模板
2. HTTPS / Nginx
3. 数据库迁移规划
4. 备份与恢复

---

## 9. 最终结论

这不是一个“思路不错但还没开始做系统”的项目。  
恰恰相反，它已经是一个**后端能力较强、平台意识明显、数据面完整度较高**的系统。

它当前真正缺的不是“再做更多”，而是：

1. **让维护者重新看懂它**
2. **让老师真正感受到它的产品价值**
3. **让市场能用一句话定义它**

这三件事做完，项目就会从“长周期 vibe coding 产物”变成“可重新掌控的系统工程”。
