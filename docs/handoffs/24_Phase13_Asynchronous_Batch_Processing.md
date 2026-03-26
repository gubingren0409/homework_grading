# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 13 (Asynchronous Batch Processing Pipeline)
## 1. 架构约束 (Architectural Constraints)
实现班级规模的并发批改。必须引入信号量（Semaphore）控制并发吞吐率。必须建立异常隔离（Fault Isolation）机制，防止单个损坏的图像文件导致整个批处理队列崩溃。必须生成全局降维数据报告（CSV）。
## 2. 工程拓扑上下文 (Directory Context)
目标工作目录为 `scripts/`。
你需要新建以下文件：
1. `scripts/batch_grade.py`
## 3. 核心实现规范 (Implementation Specifications)
### 任务: 并发批改调度器与数据降维
**目标文件:** `scripts/batch_grade.py`
**设计规则:**
1. **依赖注入:** 引入 `asyncio`, `argparse`, `pathlib.Path`, `json`, `csv` 等标准库。引入核心工作流组件。
2. **并发节流 (Throttling):** 在主逻辑中初始化 `semaphore = asyncio.Semaphore(args.concurrency)`。
3. **参数解析:**
   - `--students_dir`: 必填。学生作答的根目录（例：`data/.../students`）。脚本需遍历该目录下的文件，将文件名或所在子目录名视为 `Student_ID`。
   - `--rubric_file`: 必填。标准答案 JSON 路径。
   - `--output_dir`: 必填。批改结果输出目录。
   - `--concurrency`: 选填。默认值为 5。
4. **单任务协程 `async def process_single_student(student_id, file_paths, rubric, semaphore, workflow, output_dir)`:**
   - 必须 be `async with semaphore:` 包裹。
   - 包含 `try...except Exception as e` 块。
   - 成功时：将 JSON 报告持久化至 `output_dir/student_id.json`。
   - 失败时：记录错误日志，并返回一个标记为失败的兜底字典结构。
5. **全局聚合与降维:**
   - 使用 `asyncio.gather(*tasks)` 并发执行所有学生的批改。
   - 在 `--output_dir` 根目录下生成一份 `summary.csv`。
   - CSV 表头必须包含：`Student_ID`, `Total_Deduction`, `Is_Fully_Correct`, `Requires_Human_Review`, `Error_Status`。
## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出 `batch_grade.py` 的完整源码。确保异步 I/O 无阻塞，CSV 写入操作正确。禁止输出解释性文本。
