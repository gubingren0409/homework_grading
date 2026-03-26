# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 9 (API Layer & Test Alignment)

## 1. 架构约束 (Architectural Constraints)
修复底层工作流重构导致的 API 路由层契约断层。遵循 RESTful 规范，接口顶层必须返回 JSON 对象而非裸数组。测试用例必须同步覆盖多页报告的断言逻辑。

## 2. 工程拓扑上下文 (Directory Context)
目标工作目录为 `src/api/` 与 `tests/`。
你需要修改以下文件：
1. `src/api/routes.py`
2. `tests/test_api.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 路由契约重构
**目标文件:** `src/api/routes.py`
**设计规则:**
1. 导入 `BaseModel` (来自 `pydantic`) 和 `EvaluationReport` (来自对应数据结构路径)。
2. 在路由定义上方新建顶层响应模型：
   ```python
   class BatchGradingResponse(BaseModel):
       reports: list[EvaluationReport]
   ```
3. 修改批改接口（如 `POST /grade` 或类似端点）：
   - 显式声明 `response_model=BatchGradingResponse`。
   - 确保将上传文件的 `filename` 传递给底层 `workflow.run_pipeline` 算子。
   - 接收返回的报表列表，组装并返回：`return BatchGradingResponse(reports=reports_list)`。

### 任务 B: API 单元测试修复
**目标文件:** `tests/test_api.py`
**设计规则:**
1. 定位到测试 API 响应的核心集成用例。
2. 覆盖断言逻辑：
   - 断言 `response.status_code == 200`。
   - 解析 JSON：`data = response.json()`。
   - 断言 `"reports" in data`，证明顶层结构已包裹。
   - 断言 `isinstance(data["reports"], list)`。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出更新后的 `src/api/routes.py` 与 `tests/test_api.py` 完整源码。确保异步路由处理和依赖注入未被破坏。禁止输出任何解释性文本。
