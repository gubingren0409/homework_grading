# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 12 (Dual-Track Signature Alignment & CLI Loop)
## 1. 架构约束 (Architectural Constraints)
同步主批改流水线的多文件处理能力。修复 CLI 工具签名断层。编写本地双轨批改（Track 1 -> Track 2）的端到端集成验证脚本。
## 2. 工程拓扑上下文 (Directory Context)
你需要修改/新建以下文件：
1. `src/core/workflow.py` (或包含 `GradingWorkflow` 的对应文件)
2. `scripts/extract_rubric.py` (修改)
3. `scripts/grade_student.py` (新建)
## 3. 核心实现规范 (Implementation Specifications)
### 任务 A: 主批改流水线 (Track 2) 聚合升级
**目标文件:** 包含 `GradingWorkflow` 的文件
**设计规则:**
1. 修改 `run_pipeline` 签名：`async def run_pipeline(self, files_data: list[tuple[bytes, str]], rubric: TeacherRubric | None = None) -> EvaluationReport:`。
2. **复用多源感知聚合逻辑:**
   - 移除旧的单文件 `normalize_to_images` 调用。
   - 替换为：`image_bytes_list = await process_multiple_files(files_data)`。
   - 按照 `generate_rubric_pipeline` 中相同的逻辑，遍历图像池，调用感知引擎，重写带页码的 `element_id` (`f"p{page_index}_{elem.element_id}"`)，收集所有 `all_elements`。
   - 构造 `merged_ir`。
   - 将 `merged_ir` 与 `rubric` 传递给认知引擎：`return await self.cognitive_engine.evaluate_logic(merged_ir, rubric)`。
### 任务 B: 提取工具签名修复
**目标文件:** `scripts/extract_rubric.py`
**设计规则:**
1. 修改 `argparse`：将 `--input_file` 改为 `--input_files`，并添加 `nargs='+'` 参数以支持接收多个文件路径。
2. **文件读取:** 循环读取传入的路径列表，构建 `files_data = [(Path(p).read_bytes(), Path(p).name) for p in args.input_files]`。
3. 将 `files_data` 传入 `await workflow.generate_rubric_pipeline(files_data)`。
### 任务 C: 双轨批改本地闭环工具 (Dual-Track CLI)
**目标文件:** `scripts/grade_student.py`
**设计规则:**
1. **依赖:** 引入必要的模块，包括 `TeacherRubric` (用于反序列化本地 JSON)。
2. **命令行参数:**
   - `--student_files`: 必填，`nargs='+'`，学生作答的图片/PDF路径列表。
   - `--rubric_file`: 必填，由 `extract_rubric.py` 生成的 `TeacherRubric` JSON 文件路径。
   - `--output_file`: 选填，保存批改报告的路径（默认为 `outputs/grading_report.json`）。
3. **主逻辑:**
   - 读取并解析 Rubric：`rubric = TeacherRubric.model_validate_json(Path(args.rubric_file).read_text(encoding="utf-8"))`。
   - 构建 `files_data`。
   - 调用：`report = await workflow.run_pipeline(files_data, rubric=rubric)`。
   - 将结果序列化写入 `--output_file` 并打印系统总扣分及通过状态。
## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出上述三个文件的完整更新源码。确保 `workflow.run_pipeline` 返回单一的合并报告，而不是报告列表。禁止输出解释性文本。
