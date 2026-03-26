# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 12D (Dynamic Schema Injection)
## 1. 架构约束 (Architectural Constraints)
消除认知引擎的 JSON 字段幻觉。严禁在提示词中硬编码字段名。必须利用 Pydantic 动态反射机制，将目标数据契约的 JSON Schema 强行注入给大模型。
## 2. 工程拓扑上下文 (Directory Context)
目标工作目录为 `src/cognitive/engines/`。
你需要修改以下文件：
1. `src/cognitive/engines/deepseek_engine.py`
## 3. 核心实现规范 (Implementation Specifications)
### 任务: 提示词 Schema 动态挂载
**目标文件:** `src/cognitive/engines/deepseek_engine.py`
**设计规则:**
1. 确保已导入 `json` 模块以及 `EvaluationReport` 数据模型。
2. 定位到 `evaluate_logic` 方法中构建发送给大模型的 `messages` 数组的位置。
3. **动态 Schema 生成与注入:**
   在组合最终的 Prompt 之前，获取 `EvaluationReport` 的 Schema 字符串：
   ```python
   target_schema = json.dumps(EvaluationReport.model_json_schema(), indent=2)
   schema_instruction = (
       f"\n\nCRITICAL CONSTRAINTS:\n"
       f"1. You MUST return ONLY a valid JSON object.\n"
       f"2. The JSON object MUST strictly conform to the following JSON Schema:\n"
       f"{target_schema}\n"
       f"3. DO NOT wrap the output in any parent keys. Output the flat structure directly."
   )
   ```
4. 将 `schema_instruction` 追加到发送给 DeepSeek 的 System Prompt 中（或作为 User Prompt 的强制后缀）。
## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出包含动态 Schema 注入逻辑的 `deepseek_engine.py` 完整源码。严禁破坏原有的防御性解包逻辑（Phase 12C）。禁止输出解释性文本。
