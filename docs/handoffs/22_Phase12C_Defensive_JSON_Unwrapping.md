# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 12C (Defensive JSON Unwrapping)
## 1. 架构约束 (Architectural Constraints)
在认知引擎反序列化阶段引入动态解包逻辑，消除 LLM 幻觉生成的包裹性根节点，保证下游 Pydantic 契约校验的稳定性。同步强化系统提示词。
## 2. 工程拓扑上下文 (Directory Context)
目标工作目录为 `src/cognitive/engines/`。
你需要修改以下文件：
1. `src/cognitive/engines/deepseek_engine.py`
## 3. 核心实现规范 (Implementation Specifications)
### 任务: 防御性解析与提示词加固
**目标文件:** `src/cognitive/engines/deepseek_engine.py`
**设计规则:**
1. **提示词加固:** 在 `_system_prompt_grading` (或对应的系统提示词属性) 中追加硬性指令：`"CRITICAL: Output flat JSON ONLY. DO NOT wrap the response in any parent keys like 'evaluation_report'."`。
2. **反序列化拦截:** 在 `evaluate_logic` 方法中，解析 LLM 响应内容 (`json.loads`) 之后，实例化 `EvaluationReport` 之前，注入以下清洗逻辑：
   ```python
   parsed_data = json.loads(response_content)
   
   # 防御性解包：处理已知的包裹键
   if "evaluation_report" in parsed_data and isinstance(parsed_data["evaluation_report"], dict):
       parsed_data = parsed_data["evaluation_report"]
   # 泛化兜底：如果顶层只有一个未知键且值为字典，强行提取内部结构
   elif len(parsed_data) == 1 and isinstance(list(parsed_data.values())[0], dict):
       parsed_data = list(parsed_data.values())[0]
       
   return EvaluationReport(**parsed_data)
   ```
   *(注意：如果你的代码中使用的是其他变量名，请对齐上下文)*
## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出包含动态解包逻辑的 `deepseek_engine.py` 完整源码。禁止输出解释性文本。
