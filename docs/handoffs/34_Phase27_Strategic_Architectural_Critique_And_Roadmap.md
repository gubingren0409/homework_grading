# 🎯 战略快照：架构技术债定性与演进路线 (Phase 27)

## 0. 核心共识 (Core Consensus)
- **坚持 SRP (单一职责原则)**：搁置前置文档解析层，严守核心引擎职责边界。
- **现状定性**：系统目前处于“过度拟合理想路径”的脆弱状态，缺乏对非理想输入（脏数据）的抵御能力。

## 1. 三大核心隐患与工程修正方案

### 🛡️ 一、 容错与防御机制 (Fail-Fast & Chaos Engineering)
- **问题**：系统存在 "Error Masking" 现象，强迫模型对不可读数据进行“幻觉评分”。
- **最优解**：
    - **状态机重构**：`EvaluationReport` 引入 `status: Literal["SCORED", "REJECTED_UNREADABLE", "REJECTED_BLANK"]`。
    - **赋予“拒绝权”**：Prompt 级联注入，乱码率超标或逻辑断裂时强制触发 `REJECTED_UNREADABLE`。
    - **混沌验证**：构建全黑、极度模糊、恶意涂鸦等脏数据集进行靶向压测，确保 100% 拦截。

### 🚀 二、 高并发压测盲区 (Message Queue Decoupling)
- **问题**：`asyncio.gather` + 本地 SQLite 无法支撑分钟级长耗时任务的真实并发需求（连接池耗尽、OOM）。
- **最优解**：
    - **物理隔离**：FastAPI (接入层) 仅负责入队并返回 `TaskID` (HTTP 202)。
    - **任务队列**：引入 Redis/RabbitMQ + Celery Workers，基于 API Key 的 RPM/TPM 动态配置并发数。
    - **异步回调**：通过状态轮询或 Webhook 交付结果。

### 🔄 三、 闭环反馈机制 (HITL & Feedback Loop)
- **问题**：系统缺乏纠错通道，无法利用业务数据实现自我进化。
- **最优解**：
    - **数据扩展**：数据库新增 `teacher_override_score/reason` 字段。
    - **纠错接口**：暴露 `PATCH` 接口供教师重写 AI 结果。
    - **动态 Few-Shot**：提取高偏差样本及其 `override_reason`，动态注入 System Prompt 实现进化。

## 2. 执行优先级 (Execution Priority)
1. **逻辑防线**：单体逻辑的“拒绝权”与状态机重构（防止防线被击穿）。
2. **并发外壳**：消息队列解耦（支撑生产规模）。
3. **数据飞轮**：HITL 管道建设。

---
*Snapshot captured by Gemini CLI on 2026-03-25. This document serves as the "North Star" for the upcoming refactoring phase.*
