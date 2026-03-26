# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 3 (API Gateway & Dependency Injection)

## 1. 架构约束 (Architectural Constraints)
当前任务是基于 FastAPI 构建系统的网络表现层。
必须严格遵循依赖注入（Dependency Injection）范式。路由层（Routes）绝对禁止直接实例化引擎，必须通过 FastAPI 的 `Depends` 机制获取 `GradingWorkflow`。全局异常必须在 FastAPI 层面被捕获并转化为标准的 HTTP 响应。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `src/api/` 和 `src/`。
你需要生成三个核心文件：
1. `src/api/dependencies.py`
2. `src/api/routes.py`
3. `src/main.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 依赖注入容器 (DI Container)
**目标文件:** `src/api/dependencies.py`
**设计规则:**
1. 导入 `MockPerceptionEngine`, `MockCognitiveAgent` 以及 `GradingWorkflow`。
2. 编写依赖提供者函数 `def get_grading_workflow() -> GradingWorkflow:`。
3. 在该函数内，实例化 Mock 引擎并注入 `GradingWorkflow`，返回工作流实例。

### 任务 B: RESTful 路由端点 (API Routes)
**目标文件:** `src/api/routes.py`
**设计规则:**
1. 实例化 `APIRouter(prefix="/api/v1/grade", tags=["Grading"])`。
2. 编写 `POST /` 接口。参数接收 `file: UploadFile = File(...)` 和 `workflow: GradingWorkflow = Depends(get_grading_workflow)`。
3. 接口逻辑：读取 `file.read()` 获取字节流，调用 `await workflow.run_pipeline(image_bytes)`。
4. 返回值类型注解必须为 `EvaluationReport`（FastAPI 会自动将其序列化为 JSON）。

### 任务 C: ASGI 入口与异常拦截 (Main App)
**目标文件:** `src/main.py`
**设计规则:**
1. 实例化 `FastAPI` 应用。
2. 使用 `app.include_router` 注册上述路由。
3. **核心防御逻辑**: 注册全局异常处理器 `@app.exception_handler(PerceptionShortCircuitError)`。当调度层抛出此熔断异常时，必须返回 HTTP 422 (Unprocessable Entity)，并将异常自带的 `readability_status` 包装在 JSON 返回体中。
4. 注册全局异常处理器 `@app.exception_handler(GradingSystemError)`，作为其他业务异常的兜底，返回 HTTP 400 或 500。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出 `src/api/dependencies.py`, `src/api/routes.py` 与 `src/main.py` 的完整 Python 源码。代码必须通过 `mypy` 校验，包含标准的 FastAPI 类型注解，仅输出生产级代码。
