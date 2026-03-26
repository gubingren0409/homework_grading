# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 24 & 25 (Asymmetric Degradation & API Error Catching)
## 1. 架构变更 (Architectural Changes)
- **硬性熔断器 (Task-Level Circuit Breaker)**: 
  - 引入 `connection_error_count`，阈值设定为 2 (`MAX_CONNECTION_ERRORS = 2`)。
  - 捕获范围扩大至完整的 `openai.APIError`（涵盖网络级断连与流式不完整读取 `incomplete chunked read`）。
- **非对称降级路由 (Fallback to V3)**:
  - 当 Reasoner (R1) 在特定长尾样本上陷入死结或被持续掐断时，触发兜底逻辑。
  - 临时将模型标识切换至 `deepseek-chat` (V3)，关闭流式请求，执行一次低延迟的同步/异步全量生成，确保任务必须输出合法 JSON 落盘，防止死循环。