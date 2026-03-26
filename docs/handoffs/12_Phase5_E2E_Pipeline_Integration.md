# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 5 (Real End-to-End Pipeline Integration)

## 1. 架构约束 (Architectural Constraints)
当前任务是验证全物理链路。绝对禁止修改原有的 `test_api.py` 或 `dependencies.py` 中的 Mock 逻辑。必须新建独立的 E2E 测试 file。由于 VLM 和 LLM 均存在非确定性，测试断言应聚焦于数据流转的连贯性与最终输出契约（Schema）的完整性，放弃对具体错题内容的硬编码文本比对。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `tests/`。
你需要生成核心测试文件：
1. `tests/test_e2e_real_pipeline.py`

## 3. 核心测试基建规范 (Testing Specifications)

### 任务: 真实物理全链路压测 (Real E2E Pipeline Test)
**目标文件:** `tests/test_e2e_real_pipeline.py`
**设计规则:**
1. **依赖导入:** 导入 `pytest`, `io`, `PIL.Image`, `PIL.ImageDraw`。导入全局 `settings`。导入 `QwenVLMPerceptionEngine`, `DeepSeekCognitiveEngine`, 以及中枢 `GradingWorkflow`。
2. **前置防御:** 使用 `@pytest.mark.skipif` 确保只有在 `QWEN_API_KEY` 和 `DEEPSEEK_API_KEY` 同时存在时才执行，否则跳过。
3. **图像生成 Fixture:** 动态绘制一张包含微积分极限替换谬误的测试图片（例如写入文本："lim(x->0) sin(x)/x = 0"）。
4. **核心执行流 `test_full_pipeline_with_real_engines` (异步):**
   - 实例化 `QwenVLMPerceptionEngine` 和 `DeepSeekCognitiveEngine`。
   - 将双引擎注入 `GradingWorkflow` 进行组装。
   - 调用 `await workflow.run_pipeline(image_bytes)`。
5. **韧性断言 (Resilient Assertions):**
   - 断言返回结果类型严格为 `EvaluationReport`。
   - 断言 `step_evaluations` 列表长度 `> 0`（证明视觉提取与逻辑分步均成功打通）。
   - 断言 `system_confidence` 处于 `[0.0, 1.0]` 区间。
   - 断言无未捕获异常抛出。

## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出 `tests/test_e2e_real_pipeline.py` 的完整 Python 源码。代码必须符合异步标准，严禁引入非标准库。禁止输出任何解释性文本。
