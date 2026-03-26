# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 4B (DeepSeek Boundary Integration Testing)

## 1. 架构约束 (Architectural Constraints)
当前任务是编写针对 `DeepSeekCognitiveEngine` 的真实网络边界测试。
测试必须对视觉层保持盲态，直接在内存中构造强类型的 `PerceptionOutput` 实例作为输入。如果环境变量中未配置 `DEEPSEEK_API_KEY`，测试必须被优雅跳过。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `tests/`。
你需要生成核心测试文件：
1. `tests/test_deepseek_boundary.py`

## 3. 核心测试基建规范 (Testing Specifications)

### 任务: 真实逻辑引擎边界压测 (Real LLM Boundary Tests)
**目标文件:** `tests/test_deepseek_boundary.py`
**设计规则:**
1. **依赖导入:** 导入 `pytest`, 导入 `DeepSeekCognitiveEngine` (从 `src.cognitive.engines.deepseek_engine`), 导入 `PerceptionOutput` (从 `src.schemas.perception_ir`), 导入全局 `settings`。
2. **前置防御拦截:** 使用 `@pytest.mark.skipif(not settings.deepseek_api_key, reason="Missing DEEPSEEK_API_KEY")`。
3. **核心测试用例 `test_deepseek_engine_logic_evaluation` (异步):**
   - **构造极性脏数据:** 实例化一个 `PerceptionOutput`。
     - `readability_status="CLEAR"`, `trigger_short_circuit=False`, `global_confidence=0.99`。
     - `elements` 列表包含三个 `latex_formula` 类型的步骤：
       - `element_id="step_1"`, `raw_content="2x = 4"`
       - `element_id="step_2"`, `raw_content="x = 4 - 2"` (注入概念/逻辑错误：除法变减法)
       - `element_id="step_3", raw_content="x = 2"` (错误推导下的巧合正确结果)
   - **引擎实例化与调用:** `engine = DeepSeekCognitiveEngine()`，调用 `await engine.evaluate_logic(mock_perception_output)`。
   - **硬性断言 (Hard Assertions):**
     - 断言返回对象的 `is_fully_correct` 为 `False`。
     - 提取 `step_evaluations` 中 `reference_element_id == "step_2"` 的评估节点。
     - 断言该节点的 `is_correct` 为 `False`。
     - 断言该节点的 `error_type` 在 `["CALCULATION", "LOGIC", "CONCEPTUAL"]` 之中。
     - 断言该节点的 `correction_suggestion` 不为空。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出 `tests/test_deepseek_boundary.py` 的完整 Python 源码。代码必须遵守异步测试规范，并严格使用 Pydantic 模型实例化数据。禁止输出解释性文本。
