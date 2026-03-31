# Phase 36：人工复核流 API 契约（前端落地最小闭环）

**状态**：✅ 已落地（后端契约就绪）  
**日期**：2026-03-30  
**范围**：数据库状态机扩展 + 待办列表接口 + 人工提交接口 + 分流字段回传

---

## 1. 目标

在不依赖 Phase 35 真实样本验收的前提下，先打通“机器判定 -> 人工复核 -> 回写数据库”的最小闭环，让前端可直接落地。

---

## 2. 状态机（Task 维度）

### 执行状态 `tasks.status`（pipeline_status）

- `PENDING`
- `PROCESSING`
- `COMPLETED`
- `FAILED`

### 业务判定 `tasks.grading_status`

- `SCORED`
- `REJECTED_UNREADABLE`

### 复核状态 `tasks.review_status`

- `NOT_REQUIRED`：无需人工复核
- `PENDING_REVIEW`：进入人工待办池
- `REVIEWED`：人工已处理完成

---

## 3. 新增/补齐字段（tasks）

- `review_status`：复核状态（带 CHECK 约束）
- `grading_status`：业务判定状态（`SCORED` / `REJECTED_UNREADABLE`）
- `human_feedback_json`：结构化人工修正内容（JSON 字符串）
- `is_regression_sample`：是否纳入回归样本（0/1）
- `fallback_reason`：进入人工链路原因（如感知拒绝）

并新增索引：
- `idx_review_status`

---

## 4. 自动状态推进规则（Worker）

1）任务正常完成（`pipeline_status=COMPLETED`, `grading_status=SCORED`）：
- 若 `report.requires_human_review=True` -> `review_status=PENDING_REVIEW`
- 否则 -> `review_status=NOT_REQUIRED`

2）业务拒绝（`pipeline_status=COMPLETED`, `grading_status=REJECTED_UNREADABLE`）：
- 强制 `review_status=PENDING_REVIEW`
- `fallback_reason=PERCEPTION_SHORT_CIRCUIT:<readability_status>`

3）系统失败（`pipeline_status=FAILED`）：
- 进入重试 / DLQ 路径
- 不进入人工复核待办池

---

## 5. 前端对接 API

## A. 获取待人工复核任务

`GET /api/v1/tasks/pending-review`

参数：
- `status`（可选）：`SCORED` 或 `REJECTED_UNREADABLE`（按 `grading_status` 过滤）
- `page`（默认 1）
- `limit`（默认 20）

返回：`review_status=PENDING_REVIEW` 的任务摘要列表。

## B. 提交人工复核结果

`POST /api/v1/tasks/{task_id}/review`

请求体：
```json
{
  "human_feedback_json": {
    "before": {"score": 6, "comment": "..." },
    "after": {"score": 8, "comment": "..." },
    "diff": ["扣分点2撤销", "评语改写"]
  },
  "is_regression_sample": false
}
```

行为：
- 将 `review_status` 推进为 `REVIEWED`
- 写入 `human_feedback_json` 与 `is_regression_sample`

## C. 任务详情接口补齐分流字段

`GET /api/v1/grade/{task_id}` 现已返回：
- `grading_status`
- `review_status`
- `fallback_reason`
- `is_regression_sample`

前端可直接据此决定：
- 是否进结果页
- 是否进人工待办池

## D. 对接辅助接口（便于前端快速落地）

`GET /api/v1/review/flow-guide`

返回核心端点模板与枚举说明，减少前端硬编码错误。

---

## 6. 前端最小落地流程建议

1）上传图片/PDF -> 获取 `task_id`  
2）接入 SSE：`/api/v1/tasks/{task_id}/stream`  
3）任务终态后查询：`/api/v1/grade/{task_id}`  
4）若 `pipeline_status=COMPLETED` 且 `review_status=PENDING_REVIEW`，进入待办池视图  
5）教师提交人工修正：`POST /api/v1/tasks/{task_id}/review`  

---

## 7. 与 Phase 35 的关系

- Phase 35（多题同页对称切片）当前为“代码完成、样本验收待补”。
- 本阶段（Phase 36）不依赖 Phase 35 验收结果，可独立推进前端落地。
- 两者并行不冲突：保证主干交付优先。

