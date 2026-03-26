# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 2 (Orchestration Integration Testing)

## 1. 架构约束 (Architectural Constraints)
当前任务是编写中枢调度器 `GradingWorkflow` 的异步集成测试。
必须验证两条核心执行流：正常流转（Happy Path）与感知层脏数据熔断（Circuit Breaker）。测试必须依赖于此前编写的 Mock 引擎与全局异常类。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `tests/`。
你需要生成/重写以下文件：
1. `tests/test_workflow.py`

## 3. 核心测试基建规范 (Testing Specifications)

### 任务 A: 状态机集成压测 (Workflow Integration Tests)
**目标文件:** `tests/test_workflow.py`
**测试框架:** 必须引入 `pytest` 并在所有异步测试用例上使用 `@pytest.mark.asyncio` 装饰器。
**依赖导入:** - 导入 `GradingWorkflow` (从 `src.orchestration.workflow`)。
- 导入 `MockPerceptionEngine`, `MockCognitiveAgent` (从 `src.perception.mock_engine` 和 `src.cognitive.mock_agent`)。
- 导入 `PerceptionShortCircuitError` (从 `src.core.exceptions`)。
- 导入契约 `PerceptionOutput`, `EvaluationReport` (从 `src.schemas...`)。

**测试用例 1: `test_workflow_happy_path` (正常流转断言)**
- **逻辑:** 实例化 `GradingWorkflow`，注入标准的 `MockPerceptionEngine` 与 `MockCognitiveAgent`。传入空字节流 `b""` 调用 `run_pipeline`。
- **断言:** 确认返回结果的类型为 `EvaluationReport`，且包含预期的 `error_type` (如 `CALCULATION`)。这证明了 Track A 到 Track B 的数据流转无阻塞。

**测试用例 2: `test_workflow_circuit_breaker` (硬件级熔断断言)**
- **逻辑:** 在测试文件内动态定义一个 `DirtyPerceptionEngine(BasePerceptionEngine)`。重写其 `process_image` 方法，使其强行返回一个 `readability_status="UNREADABLE"` 且 `trigger_short_circuit=True` 的 `PerceptionOutput` 伪造对象。
- **依赖注入:** 使用这个 `DirtyPerceptionEngine` 和标准的 `MockCognitiveAgent` 实例化 `GradingWorkflow`。
- **断言:** 使用 `with pytest.raises(PerceptionShortCircuitError) as exc_info:` 强行拦截调用。断言抛出的异常信息中包含了触发熔断的底层原因。这证明了脏数据隔离墙生效。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出 `tests/test_workflow.py` 的完整 Python 源码。代码必须包含严谨的 Type Hints，遵守异步测试规范。禁止输出任何非代码的解释性文本。
