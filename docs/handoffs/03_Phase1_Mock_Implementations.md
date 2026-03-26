# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 1 (Mock Implementations for Orchestration Testing)

## 1. 架构约束 (Architectural Constraints)
当前任务是基于已定义的抽象基类（ABCs），实现用于测试调度层的 Mock 引擎。
绝对禁止引入任何网络请求（如 `aiohttp`, `requests`）或大模型 SDK。所有输入输出必须是确定性的硬编码或读取本地 Fixture。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `src/perception/` 和 `src/cognitive/`。
你需要生成两个核心文件：
1. `src/perception/mock_engine.py`
2. `src/cognitive/mock_agent.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 伪造视觉感知引擎 (MockPerceptionEngine)
**目标文件:** `src/perception/mock_engine.py`
**设计规则:**
1. 继承 `src.perception.base.BasePerceptionEngine`。
2. 实现 `process_image(self, image_bytes: bytes) -> PerceptionOutput`。
3. **核心逻辑**: 无视传入的 `image_bytes`，直接使用 `asyncio.sleep(0.5)` 模拟 I/O 延迟。
4. **返回值**: 直接构造并返回一个包含错误微积分公式（如 $\int_{0}^{1} x^2 dx = 1/2$）的 `PerceptionOutput` 对象（内容须与之前在 tests 中构建的 mock_jsons 保持语义一致）。

### 任务 B: 伪造逻辑推理引擎 (MockCognitiveAgent)
**目标文件:** `src/cognitive/mock_agent.py`
**设计规则:**
1. 继承 `src.cognitive.base.BaseCognitiveAgent`。
2. 实现 `evaluate_logic(self, perception_data: PerceptionOutput) -> EvaluationReport`。
3. **核心逻辑**: 无视传入的 `perception_data` 实际内容，使用 `asyncio.sleep(0.5)` 模拟大模型推理延迟。
4. **返回值**: 硬编码并返回一个 `EvaluationReport` 对象。设定 `is_fully_correct=False`，在 `step_evaluations` 中精准指出计算错误（指出 $1/2$ 应为 $1/3$），`error_type` 设为 `CALCULATION`。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出 `src/perception/mock_engine.py` 与 `src/cognitive/mock_agent.py` 的完整 Python 源码。
代码必须：
1. 正确导入 Pydantic 契约与本地的 Base classes。
2. 通过 `mypy` 严格模式校验。
3. 仅输出生产级代码，禁止生成解释性文本。
