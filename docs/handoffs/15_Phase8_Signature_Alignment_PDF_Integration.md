# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 8 (Signature Alignment & PDF Workflow Integration)

## 1. 架构约束 (Architectural Constraints)
修复基类重构导致的接口断层。认知层引擎必须正确接收并处理 `TeacherRubric`。调度层（Workflow）必须挂载 PDF 预处理算子，将单文件流转化为多页处理循环（Batch Processing Loop）。

## 2. 工程拓扑上下文 (Directory Context)
你需要修改以下文件（若 `GradingWorkflow` 位于其他路径，请自动寻址）：
1. `src/cognitive/engines/deepseek_engine.py`
2. `src/core/workflow.py` (或包含 `GradingWorkflow` 的文件)

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 认知引擎签名对齐与 Rubric 注入
**目标文件:** `src/cognitive/engines/deepseek_engine.py`
**设计规则:**
1. 导入 `TeacherRubric` (从 `src.schemas.rubric_ir`)。
2. 修改 `evaluate_logic` 签名，完全对齐基类：`async def evaluate_logic(self, perception_data: PerceptionOutput, rubric: TeacherRubric | None = None) -> EvaluationReport`。
3. **上下文动态挂载:** 在向 OpenAI/DeepSeek 客户端构建 `messages` 数组时，检测 `rubric` 变量。如果 `rubric` 不为 `None`，向 User Prompt 中强行注入标准答案上下文：
   `"Reference Rubric (Standard Answer & Grading Points): " + rubric.model_dump_json()`，并附加强制指令要求大模型必须基于此 Rubric 进行扣分判断。

### 任务 B: 调度层多页循环重构
**目标文件:** 包含 `GradingWorkflow` 的文件
**设计规则:**
1. 导入 `normalize_to_images` (从 `src.utils.file_parsers`)。导入 `TeacherRubric`。
2. 修改 `run_pipeline` 签名：`async def run_pipeline(self, file_bytes: bytes, filename: str = "document.jpg", rubric: TeacherRubric | None = None) -> list[EvaluationReport]:`
3. **内部流转逻辑 (Pipeline Orchestration):**
   - 执行预处理：`image_bytes_list = await normalize_to_images(file_bytes, filename)`
   - 初始化空列表 `reports = []`。
   - 遍历 `image_bytes_list`：
     - 调用 `await self.perception_engine.process_image(page_bytes)`
     - 调用 `await self.cognitive_engine.evaluate_logic(ir_data, rubric)`
     - 将结果追加至 `reports`。
   - 返回 `reports`。

## 4. 执行指令 (Execution Directive)
收到此文件后，直接输出更新后的完整源码处理好所有相关的类型提示（Type Hints）导入。禁止输出解释性文本。