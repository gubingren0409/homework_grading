# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 23 (Streaming Heartbeat & TCP Timeout Bypass)
## 1. 架构变更 (Architectural Changes)
- **TCP 空闲超时规避**: 针对 DeepSeek-Reasoner 长达数分钟的思维链导致中间件 60s 静默断流问题，废弃阻塞式请求。
- **流式接收重构**: 
  - `src/cognitive/engines/deepseek_engine.py` 开启 `stream=True`。
  - 引入异步块级读取（Chunked Read），并行累加 `reasoning_content` (CoT 过程) 与 `content` (最终结果)。
  - 维持数据持续下发，物理重置网络节点的 Keep-Alive 计时器。