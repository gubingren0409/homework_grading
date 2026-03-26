# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 3 (End-to-End API Testing)

## 1. 架构约束 (Architectural Constraints)
禁止启动 uvicorn 进行手动验证。必须使用 `fastapi.testclient.TestClient` 编写同步的端到端（E2E）黑盒测试。测试必须验证 HTTP 状态码的准确映射，以及 JSON 序列化是否严格符合 Phase 0 定义的 `EvaluationReport` 契约。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `tests/`。
你需要生成核心测试文件：
1. `tests/test_api.py`

## 3. 核心实现规范 (Testing Specifications)

### 任务: RESTful API 边界压测
**目标文件:** `tests/test_api.py`
**设计规则:**
1. 导入 `pytest`, `TestClient` (从 `fastapi.testclient`)，导入 `app` (从 `src.main`)。导入 `GradingWorkflow` 以及测试所需的依赖提供者 `get_grading_workflow`。
2. 实例化 `client = TestClient(app)`。
3. **测试用例 1: `test_grade_endpoint_happy_path`**
   - 构造伪造的文件上传载荷（payload）：`files={"file": ("test_hw.jpg", b"fake_image_bytes", "image/jpeg")}`。
   - 对 `/api/v1/grade/` 发起 POST 请求。
   - 断言：`response.status_code == 200`。
   - 断言：反序列化返回的 JSON，提取 `is_fully_correct` 字段并确认其等于 `False`（因 Mock 引擎硬编码了计算错误），提取 `step_evaluations` 确认错误归因存在。
4. **测试用例 2: `test_grade_endpoint_circuit_breaker_422`**
   - **核心逻辑（依赖倒置劫持）**: 在测试内部定义 `DirtyPerceptionEngine`（强制抛出脏数据标记）。实例化一个注入了此引擎的 `GradingWorkflow`。
   - 使用 `app.dependency_overrides[get_grading_workflow] = override_get_workflow` 劫持 FastAPI 的依赖注入容器。
   - 发起相同的 POST 请求。
   - 断言：`response.status_code == 422`。
   - 断言：解析响应 JSON，确认其包含了 `readability_status` 字段（如 `UNREADABLE`），证明底层的 `PerceptionShortCircuitError` 被成功转换为规范的 HTTP 错误载荷。
   - 测试末尾必须执行 `app.dependency_overrides.clear()` 清理环境。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出 `tests/test_api.py` 的完整 Python 源码。代码必须通过严格的类型推导。仅输出生产级代码，禁止生成解释性文本。
