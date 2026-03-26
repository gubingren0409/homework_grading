# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 4 (Qwen VLM Boundary Integration Testing)

## 1. 架构约束 (Architectural Constraints)
当前任务是编写针对 `QwenVLEngine` 的真实网络边界测试。
测试必须是自包含的（Self-contained），禁止依赖外部静态图片文件。必须在运行时通过代码在内存中生成测试图像。测试必须具有防御性：如果环境变量中未配置 `QWEN_API_KEY`，测试必须被优雅地跳过（Skip），而不是导致整个测试套件崩溃。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `tests/`。
你需要生成/重写以下文件：
1. `tests/test_qwen_boundary.py`

## 3. 核心测试基建规范 (Testing Specifications)

### 任务: 真实大模型边界压测 (Real API Boundary Tests)
**目标文件:** `tests/test_qwen_boundary.py`
**设计规则:**
1. **依赖导入:** 导入 `pytest`, `io`, 导入 `PIL.Image`, `PIL.ImageDraw`（需确保环境中安装了 `Pillow`）。导入 `QwenVLEngine` (从 `src.perception.engines.qwen_engine`) 和全局 `settings` (从 `src.core.config`)。
2. **前置防御拦截:** 使用 `@pytest.mark.skipif(not settings.qwen_api_key, reason="Missing QWEN_API_KEY")` 装饰器。只有在配置了真实秘钥时才发起网络请求。
3. **动态物料生成逻辑 (Fixture):**
   - 编写一个辅助函数 `generate_test_image_bytes() -> bytes:`。
   - 使用 `Pillow` 创建一张白色背景的图像，并在其上绘制简单的文本 "y = x^2"。
   - 将图像保存到 `io.BytesIO()` 中（格式为 JPEG），并返回 `.getvalue()` 字节流。
4. **核心测试用例 `test_qwen_engine_real_network_call` (异步):**
   - 实例化 `QwenVLEngine()`。
   - 调用 `await engine.process_image(generate_test_image_bytes())`。
   - **硬性断言 (Hard Assertions):**
     - 断言返回值的类型为 `PerceptionOutput`。
     - 断言 `elements` 列表不为空。
     - 断言 `global_confidence` 在 0.0 到 1.0 之间。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出 `tests/test_qwen_boundary.py` 的完整 Python 源码。代码必须遵守异步测试规范，并妥善处理资源流。禁止输出解释性文本。
