# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 2 (Orchestration & State Machine)

## 1. 架构约束 (Architectural Constraints)
当前任务是构建系统的中枢调度器与全局异常类。调度器必须严格遵守依赖注入（Dependency Injection）原则，禁止在调度器内部硬编码实例化特定的引擎（如 `MockPerceptionEngine`），必须通过构造函数接收实现了 `BasePerceptionEngine` 和 `BaseCognitiveAgent` 接口的实例。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `src/core/` 和 `src/orchestration/`。
你需要生成两个核心文件：
1. `src/core/exceptions.py`
2. `src/orchestration/workflow.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 全局异常基建 (Core Exceptions)
**目标文件:** `src/core/exceptions.py`
**设计规则:**
1. 定义系统级异常基类 `GradingSystemError(Exception)`。
2. 定义感知层熔断异常 `PerceptionShortCircuitError(GradingSystemError)`。当图像质量极差或置信度跌破阈值时抛出，必须能携带 `readability_status` 供上层路由。
3. 定义认知层拒绝服务异常 `CognitiveRefusalError(GradingSystemError)`。

### 任务 B: 调度器主循环 (Grading Workflow)
**目标文件:** `src/orchestration/workflow.py`
**设计规则:**
1. 定义类 `GradingWorkflow`。
2. `__init__` 方法接收两个参数：`perception_engine: BasePerceptionEngine` 和 `cognitive_agent: BaseCognitiveAgent`。
3. 实现核心异步方法：`async def run_pipeline(self, image_bytes: bytes) -> EvaluationReport`。
4. **状态机流转逻辑**:
   - 调用 `perception_engine.process_image` 获取 `PerceptionOutput`。
   - **断路器检查 (Circuit Breaker)**: 检查 `trigger_short_circuit` 是否为 True，或者 `readability_status` 是否在 `["HEAVILY_ALTERED", "UNREADABLE"]` 中。如果满足任一条件，立即抛出 `PerceptionShortCircuitError`，严禁将脏数据传入认知层。
   - 调用 `cognitive_agent.evaluate_logic` 获取 `EvaluationReport`。
   - 返回最终的报告。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出 `src/core/exceptions.py` 与 `src/orchestration/workflow.py` 的完整 Python 源码。
代码必须：
1. 正确导入 typing 模块、自定义的契约模型（schemas）与接口基类（perception.base, cognitive.base）。
2. 通过 `mypy` 严格模式校验。
3. 仅输出生产级代码，禁止生成测试用例或解释性文本。
