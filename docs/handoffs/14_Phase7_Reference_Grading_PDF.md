# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 7 (Reference-Based Grading & PDF Support)

## 1. 架构约束 (Architectural Constraints)
当前任务是修补系统缺失的“参考答案（Rubric）”上下文，并支持 PDF 格式的文件预处理。必须保证接口的向后兼容性（允许无 Rubric 时的纯逻辑校验降级）。

## 2. 工程拓扑上下文 (Directory Context)
目标工作目录为 `src/schemas/`, `src/cognitive/` 和 `src/utils/`。
你需要生成/修改以下文件：
1. `src/schemas/rubric_ir.py` (新建)
2. `src/cognitive/base.py` (修改接口签名)
3. `src/utils/file_parsers.py` (新建)

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 评分标准契约 (Rubric Schema)
**目标文件:** `src/schemas/rubric_ir.py`
**设计规则:**
1. 导入 `pydantic.BaseModel`。
2. 定义 `GradingPoint(BaseModel)`，包含 `point_id: str`, `description: str` (如 "列出安培力公式 F=BIL"), `score: float`。
3. 定义 `TeacherRubric(BaseModel)`，包含 `question_id: str`, `correct_answer: str`, `grading_points: list[GradingPoint]`。

### 任务 B: 认知基类签名重构
**目标文件:** `src/cognitive/base.py`
**设计规则:**
1. 导入 `TeacherRubric` (从 `src.schemas.rubric_ir`)。
2. 修改抽象基类方法：`@abstractmethod async def evaluate_logic(self, perception_data: PerceptionOutput, rubric: TeacherRubric | None = None) -> EvaluationReport`。保留 `None` 作为默认值以维持向下兼容。

### 任务 C: 统一文件预处理器
**目标文件:** `src/utils/file_parsers.py`
**设计规则:**
1. 引入 `fitz` (PyMuPDF) 和 `io`。
2. 定义异步函数 `async def normalize_to_images(file_bytes: bytes, filename: str) -> list[bytes]:`。
3. 逻辑：
   - 如果 `filename` 以 `.pdf` 结尾：使用 `fitz.open(stream=file_bytes, filetype="pdf")` 加载。遍历每一页，使用 `page.get_pixmap(dpi=150)` 提取图像，将其转换为 JPEG 字节流 (`pix.tobytes("jpeg")`)，追加至列表并返回。
   - 否则（假设为普通图片）：直接包裹进列表 `[file_bytes]` 返回。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出上述三个文件的完整源码。确保更新了认知层的接口签名，并提供了健壮的 PDF 转图片处理逻辑。禁止输出解释性文本。