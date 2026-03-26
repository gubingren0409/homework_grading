# 架构技术债评审与工程修正方案

**时间**: 2026-03-25
**状态**: 阶段快照（定性评论）
**类型**: 架构决策与技术债分析

---

## 核心决策确认

搁置前置文档解析层的决策正确。坚持核心引擎的单一职责原则（SRP）是防止系统级复杂度失控的物理底线。

另一份审查报告对当前架构技术债的定性准确。系统目前处于"过度拟合理想路径"的脆弱状态。针对排除了文档解析后的三大核心隐患，以下是直击本质的工程修正方案与最优解。

---

## 一、容错与防御机制虚设 (Error Masking)

### 问题诊断
当前系统为了追求全量落盘，强迫大模型对不可读的垃圾数据进行"幻觉脑补"，这违背了高可用系统的 Fail-Fast（快速失败）原则。

### 修正方案：引入硬性拒绝状态与混沌测试

1. **契约重构**：在 Pydantic 的 EvaluationReport 中，必须立即加入状态机枚举字段：
   ```python
   status: Literal["SCORED", "REJECTED_UNREADABLE", "REJECTED_BLANK"]
   ```

2. **指令解绑**：在 Prompt 中明确赋予模型"拒绝权"。强制规定：若提取文本乱码率超过阈值或逻辑断裂，直接输出 `REJECTED_UNREADABLE` 并立即终止推导，禁止强行打分。

3. **混沌工程验证 (Chaos Engineering)**：构建极端脏数据集（全黑图像、纯风景图、极度模糊的倒置答题卡、恶意涂鸦）。利用该数据集对引擎进行靶向压测，若系统依然输出 `SCORED`，则证明防线仍被击穿；必须 100% 触发 `REJECTED` 拦截并记录警告日志，才算防线稳固。

---

## 二、高并发压测盲区 (Concurrency Limits)

### 问题诊断
依靠 `asyncio.gather` 配合本地 SQLite 应对百级别以上的长时阻塞任务（DeepSeek-R1 响应通常为分钟级），是典型的玩具级架构。在真实生产流量下，必然触发连接池耗尽、ASGI Worker 内存溢出 (OOM) 或 `database is locked`。

### 最优解：基于消息队列的异步任务分离 (Message Queue Decoupling)

必须将 API 接收层与 AI 计算层进行物理隔离。

1. **接入层 (FastAPI)**：仅负责接收批改请求，将任务序列化后推入 Redis/RabbitMQ 队列，并立即向客户端返回 HTTP 202 Accepted 和 TaskID。请求耗时降至毫秒级。

2. **消费层 (Celery Workers)**：独立运行的后台进程。根据 API Key 的 RPM/TPM 限制，动态配置 Worker 并发数，从队列中拉取任务执行大模型调用。

3. **状态轮询/回调**：前端通过 TaskID 轮询任务状态（Pending -> Processing -> Completed/Rejected），或系统通过 Webhook 将结果推回。

---

## 三、闭环反馈机制断层 (UAT & Feedback Loop)

### 问题诊断
缺乏纠错通道的 AI 批改系统是死循环系统，无法利用业务数据产生飞轮效应。

### 最优解：构建 HITL (Human-in-the-Loop) 数据拦截与微调管道

1. **数据模型扩展**：在落盘数据库中新增 `teacher_override_score`、`teacher_override_reason` 字段。

2. **前置接口暴露**：提供 `PATCH /api/v1/evaluations/{id}/override` 接口，允许前端教师端重写 AI 的批改结果。

3. **动态 Few-Shot 样本库**：通过后台定时任务，筛选出"AI 评分与教师重写评分偏差大于 X"的记录，提取其 `override_reason`，自动组装为 Few-Shot 样本，在下一次相同题型的批改中动态注入到 System Prompt 中，实现自我进化。

---

## 实施优先级

系统重构不能齐头并进。根据依赖关系，必须先确保单体逻辑在面对脏数据时能正确阻断，再进行外围的并发与反馈链路建设。

**推荐顺序**：
1. 容错机制（Fail-Fast）
2. 高并发架构（消息队列）
3. 反馈闭环（HITL）

---

## 备注

本文档为阶段性架构评审快照，记录技术债识别与修正方案，不包含具体实施计划。
