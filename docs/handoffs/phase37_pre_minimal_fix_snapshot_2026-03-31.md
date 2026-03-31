# Phase 37：最小修复包前置快照（状态语义分离）

**状态**：✅ 快照已建立（实施前）  
**日期**：2026-03-31  
**范围**：在执行“最小修复包”前，对系统态/业务态约束与风险基线进行冻结记录

---

## 一、当前阶段定位

- Phase 36（最小前端复核台 + HITL API 契约）已落地。
- 进入“最小修复包”执行窗口，目标是：
  - 修复测试 collection 失效；
  - 固化 pipeline_status 与 grading_status 分离语义；
  - 不引入超出本轮范围的断言重构。

---

## 二、冻结的系统级约束（本轮必须满足）

1) **严格分离系统态与业务态**

- `pipeline_status`：仅表示物理执行态（`PENDING | PROCESSING | COMPLETED | FAILED`）
- `grading_status`：仅表示业务判定态（`SCORED | REJECTED_UNREADABLE`）

约束：
- `REJECTED_UNREADABLE` 属于业务防线触发，执行链路成功完成，因此 `pipeline_status` 必须为 `COMPLETED`。
- 严禁将业务拒绝与系统崩溃混同为 `FAILED`。

2) **状态投影规则（Projection）**

- `pipeline_status == FAILED`：仅系统异常；走重试 + DLQ；`review_status` 不介入。
- `pipeline_status == COMPLETED && grading_status == REJECTED_UNREADABLE`：`review_status = PENDING_REVIEW`。
- `pipeline_status == COMPLETED && grading_status == SCORED && requires_human_review == true`：`review_status = PENDING_REVIEW`。

3) **测试修复红线**

- 仅定点替换：
  - 过期路由 `/api/v1/grade/`
  - 废弃对象 `ExtractedElement`
  - 陈旧签名（传 `bytes` 替代 `list[(bytes, filename)]`）
- 禁止在本次提交中夹带超出“恢复测试运行”的断言逻辑重构。

---

## 三、实施前风险基线

- 风险1：测试系统在 collection 阶段失效，CI 信号不可用。  
- 风险2：任务态与业务态语义漂移，导致待办池、SSE/UI 分流与统计口径不稳定。  
- 风险3：若继续以旧口径推进，会把“业务拒绝”误判为“系统失败”，污染运维与质量指标。

---

## 四、本快照用途

- 作为本轮“最小修复包”验收前后的对照锚点。  
- 作为后续 Phase 37 执行记录与回归核查的语义基线。  

