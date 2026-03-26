# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 11 (Multi-File I/O Flattening & Format Firewall)
## 1. 架构约束 (Architectural Constraints)
实现 API 层对多文件（PDF 与 图片混合）数组的接收与展平。建立硬性格式防火墙阻断 Word 文档。修复多页/多图聚合时的 IR (Intermediate Representation) 标识符冲突问题。
## 2. 工程拓扑上下文 (Directory Context)
你需要修改以下文件：
1. `src/api/routes.py`
2. `src/utils/file_parsers.py`
3. `src/core/workflow.py` (或包含 `GradingWorkflow` 的文件)
## 3. 核心实现规范 (Implementation Specifications)
### 任务 A: 格式防火墙与 I/O 展平算子
**目标文件:** `src/utils/file_parsers.py`
**设计规则:**
1. 顶部定义自定义异常：`class UnsupportedFormatError(Exception): pass`。
2. 重构/新增异步函数 `async def process_multiple_files(files_data: list[tuple[bytes, str]]) -> list[bytes]:`。
3. **逻辑流：** - 初始化 `all_image_bytes = []`。
   - 遍历 `files_data` (每个元素为 `(file_bytes, filename)`)。
   - **拦截器：** 若 `filename` 以 `.doc` 或 `.docx` 结尾，立即 `raise UnsupportedFormatError("Word documents are unsupported. Please convert to PDF.")`。
   - **栅格化与展平：** 若为 PDF，调用 `fitz` 提取每一页的 JPEG 字节流并追加至 `all_image_bytes`；若为普通图片，直接追加至 `all_image_bytes`。
   - 返回 `all_image_bytes`（此时所有输入已展平为纯粹的图像流数组）。
### 任务 B: 聚合调度器重构
**目标文件:** 包含 `GradingWorkflow` 的文件
**设计规则:**
1. 修改 `generate_rubric_pipeline` 签名：`async def generate_rubric_pipeline(self, files_data: list[tuple[bytes, str]]) -> TeacherRubric:`。
2. **多源感知聚合:**
   - 获取展平后的图像池：`image_bytes_list = await process_multiple_files(files_data)`。
   - 初始化 `all_elements = []`。
   - 遍历 `image_bytes_list` (使用 `enumerate` 获取 `page_index`)，逐个调用感知引擎：`ir = await self.perception_engine.process_image(page_bytes)`。
   - **冲突避免：** 遍历 `ir.elements`，强制重写 `element_id` 为 `f"p{page_index}_{elem.element_id}"`，将其追加至 `all_elements`。
3. **全局推演:** 构造 `merged_ir = PerceptionOutput(readability_status="CLEAR", elements=all_elements, global_confidence=1.0, trigger_short_circuit=False)`，交由认知引擎生成 `TeacherRubric`。
### 任务 C: API 端点数组化
**目标文件:** `src/api/routes.py`
**设计规则:**
1. 修改 `/api/v1/rubrics/generate` 路由。
2. 签名变更为接收文件列表：`files: list[UploadFile] = File(...)`。
3. 循环读取：使用列表推导式或循环提取所有文件的 `(await file.read(), file.filename)`。
4. 传递给工作流：`rubric = await workflow.generate_rubric_pipeline(files_data)`。
5. 捕获 `UnsupportedFormatError` 并抛出 `HTTPException(status_code=415)`。
## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出上述三个文件的完整源码更新。确保导包与异步调用的正确性。禁止输出冗余的解释性文本。
