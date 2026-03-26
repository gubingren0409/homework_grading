# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 4B (Real LLM Cognitive Engine - DeepSeek)

## 1. 架构约束 (Architectural Constraints)
当前任务是实现接入 DeepSeek V3 的真实逻辑推理引擎。
必须严格继承 `BaseCognitiveAgent`。该引擎对物理图像保持绝对“盲态”，仅接收 `PerceptionOutput` 的 JSON 序列化字符串作为输入。必须使用 OpenAI 官方的异步 Python SDK 进行桥接（修改 `base_url`），并强制开启 JSON 输出模式，确保返回严格符合 `EvaluationReport` 契约。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `src/core/` 和 `src/cognitive/engines/`。
你需要修改/生成以下文件：
1. `src/core/config.py` (追加 DeepSeek 配置)
2. `src/cognitive/engines/deepseek_engine.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 配置管理更新
**目标文件:** `src/core/config.py`
**设计规则:**
1. 在现有的 `Settings` 类中，追加 `deepseek_api_key: str | None = None` 和 `deepseek_model_name: str = "deepseek-chat"`。

### 任务 B: DeepSeek 逻辑引擎实现
**目标文件:** `src/cognitive/engines/deepseek_engine.py`
**设计规则:**
1. 继承 `src.cognitive.base.BaseCognitiveAgent`。
2. 依赖导入：导入全局 `settings`，导入 `PerceptionOutput`, `EvaluationReport` 契约，导入 `AsyncOpenAI` (来自 `openai`)。
3. 实现异步方法：`async def evaluate_logic(self, perception_data: PerceptionOutput) -> EvaluationReport`。
4. **内部逻辑编排**:
   - **客户端实例化**: `client = AsyncOpenAI(api_key=settings.deepseek_api_key, base_url="https://api.deepseek.com")`。
   - **Prompt 工程**: 编写严厉的 System Prompt。声明角色为“教育级数理逻辑验证引擎”。核心约束：只基于传入的 JSON IR 数据进行验算；必须对每一条 `element_id` 的推导进行校验；必须输出纯合法的 JSON 且符合 `EvaluationReport` 的 Schema。
   - **数据转换**: 将 `perception_data.model_dump_json()` 作为 User Prompt。
   - **网络调用**: 调用 `await client.chat.completions.create(...)`，**强制设置 `response_format={"type": "json_object"}`**，并将 `temperature` 设为极低值（如 0.0 或 0.1）以消除幻觉。
   - **契约锁定**: 提取返回的文本，清理 Markdown JSON 标记，强制使用 `EvaluationReport.model_validate_json()` 进行反序列化。
   - **异常捕获**: 捕获 `openai.OpenAIError` 及 `ValidationError`，并抛出系统内部定义的 `CognitiveRefusalError` 或 `GradingSystemError`。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出更新后的 `src/core/config.py` 与全新的 `src/cognitive/engines/deepseek_engine.py` 源码。代码必须通过严格的类型推导。网络层必须妥善处理超时。禁止输出解释性文本。