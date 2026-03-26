# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 10B (Empirical Validation of Rubric Generation)

## 1. 架构约束 (Architectural Constraints)
必须在进入 Track 2（真实比对批改）之前，物理验证 Track 1（Rubric 生成）的有效性。禁止使用 Mock 数据测试大模型的 Prompt 表现。必须编写一个独立的 CLI 脚本，调用真实双引擎流水线提取并持久化 `TeacherRubric`。

## 2. 工程拓扑上下文 (Directory Context)
目标工作目录为项目根目录下的 `scripts/`。
你需要生成核心执行脚本：
1. `scripts/extract_rubric.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务: 真实物理评分标准提取工具
**目标文件:** `scripts/extract_rubric.py`
**设计规则:**
1. **依赖导入:** 导入 `asyncio`, `argparse`, `pathlib.Path`。导入真实的 `QwenVLMPerceptionEngine`, `DeepSeekCognitiveEngine`, `GradingWorkflow`。
2. **命令行参数解析:**
   - `--input_file`: 必填参数，指定标准答案图片或 PDF 的绝对路径。
   - `--output_file`: 选填参数，指定生成的 JSON 评分标准的保存路径（默认为 `outputs/reference_rubric.json`）。
3. **主循环逻辑 `async def main()`:**
   - 实例化真实引擎与 `GradingWorkflow`。
   - 读取 `--input_file` 为 `bytes`。
   - 调用 `rubric = await workflow.generate_rubric_pipeline(file_bytes, filename=input_path.name)`。
   - 将返回的 `TeacherRubric` 序列化为格式化的 JSON (`rubric.model_dump_json(indent=2)`)。
   - 将 JSON 写入 `--output_file`，并向终端打印成功提取的给分点数量（`len(rubric.grading_points)`）。
4. **异常拦截:** 捕获底层抛出的 `GradingSystemError` 或 `ValidationError` 并打印标准错误日志。

## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出 `scripts/extract_rubric.py` 的完整 Python 源码。代码必须符合异步标准，严禁引入非标准库。禁止输出任何解释性文本。
