# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 10 (Rubric Generation Pipeline)

## 1. 架构约束 (Architectural Constraints)
当前任务是补齐缺失的“标准答案解析”业务流。
必须利用现有的视觉感知层获取 IR，然后使用 DeepSeek 构建新的 `TeacherRubric`。必须暴露出一个独立的 API 端点供前端/教师端调用。

## 2. 工程拓扑上下文 (Directory Context)
你需要修改以下文件：
1. `src/cognitive/base.py`
2. `src/cognitive/engines/deepseek_engine.py`
3. `src/orchestration/workflow.py` (原指令为 src/core/workflow.py)
4. `src/api/routes.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 认知引擎功能扩展
**目标文件:** `src/cognitive/base.py` & `src/cognitive/engines/deepseek_engine.py`
**设计规则:**
1. **基类 (`base.py`):** 新增抽象方法 `@abstractmethod async def generate_rubric(self, perception_data: PerceptionOutput) -> TeacherRubric:`。
2. **派生类 (`deepseek_engine.py`):** 实现该方法。
   - **System Prompt:** 声明角色为“资深理科教研员”。核心约束：根据传入的 JSON 步骤，提炼出关键得分点（Grading Points），分配分值（总分默认10分），并严格按照 `TeacherRubric` 结构输出 JSON。
   - **网络调用:** 使用 `response_format={"type": "json_object"}`，并反序列化为 `TeacherRubric`。

### 任务 B: 调度层支持
**目标文件:** `src/orchestration/workflow.py`
**设计规则:**
1. 新增方法：`async def generate_rubric_pipeline(self, file_bytes: bytes, filename: str = "answer.jpg") -> TeacherRubric:`
2. **内部编排:**
   - 考虑到标准答案通常为单页，暂时只取预处理后的第一页：`image_bytes = (await normalize_to_images(file_bytes, filename))[0]`。
   - `ir_data = await self.perception_engine.process_image(image_bytes)`
   - `return await self.cognitive_engine.generate_rubric(ir_data)`

### 任务 C: 暴露独立 API 端点
**目标文件:** `src/api/routes.py`
**设计规则:**
1. 新增路由：`POST /api/v1/rubrics/generate`。
2. 接收 `UploadFile`，调用 `workflow.generate_rubric_pipeline`，直接返回生成的 `TeacherRubric` 对象作为响应体。

## 4. 执行指令 (Execution Directive)
收到此文件后，请严格按照顺序重构上述四个文件。注意导包关系的正确性，严禁破坏已有的批改逻辑。禁止输出任何解释性文本。
