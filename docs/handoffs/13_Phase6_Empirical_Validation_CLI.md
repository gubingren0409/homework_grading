# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 6 (Empirical Validation & Batch CLI)

## 1. 架构约束 (Architectural Constraints)
当前任务是编写一个游离于 FastAPI 之外的独立批处理脚本。
必须引入 `argparse` 处理命令行参数。为了防止触发第三方 API 的并发速率限制（Rate Limits，如 HTTP 429 错误），必须强制采用**串行（Sequential）**处理逻辑，严禁使用 `asyncio.gather` 进行并发轰炸。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为项目根目录下的 `scripts/`（如无则创建）。
你需要生成核心执行脚本：
1. `scripts/evaluate_local_images.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务: 真实物理数据批处理工具
**目标文件:** `scripts/evaluate_local_images.py`
**设计规则:**
1. **依赖导入:** 导入 `os`, `json`, `asyncio`, `argparse`, `pathlib.Path`。导入真实的 `QwenVLMPerceptionEngine`, `DeepSeekCognitiveEngine`, `GradingWorkflow`。
2. **命令行参数解析:**
   - `--input_dir`: 必填参数，指定包含测试图片的目录路径（默认可设为 `E:\ai批改\测试用例`）。
   - `--output_dir`: 选填参数，指定 JSON 报告输出路径（默认为 `outputs/`）。
3. **主循环逻辑 `async def main()`:**
   - 实例化真实引擎与 `GradingWorkflow`。
   - 遍历 `--input_dir` 下的常见图像文件（`.jpg`, `.png`, `.jpeg`）。
   - 对每张图片：
     - 读取为 `bytes`。
     - `await workflow.run_pipeline(image_bytes)`。
     - 将返回的 `EvaluationReport` 序列化为格式化的 JSON (`.model_dump_json(indent=2)`)。
     - 将 JSON 写入 `--output_dir` 中与原图同名的 `.json` 文件中。
   - 增加异常隔离：使用 `try...except Exception` 包裹单张图片的调用。如果某张图片触发熔断（`PerceptionShortCircuitError`）或超时，记录错误日志并 `continue` 处理下一张，绝对禁止整个脚本因单点错误而崩溃。

## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出 `scripts/evaluate_local_images.py` 的完整 Python 源码。代码必须包含严谨的文件 I/O 处理与终端日志打印（显示进度与成功/失败状态）。禁止输出解释性文本。