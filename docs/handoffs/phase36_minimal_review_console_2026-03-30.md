# Phase 36：最小前端复核台落地说明

**状态**：✅ 已落地（MVP）  
**日期**：2026-03-30

---

## 页面入口

- 路由：`GET /review-console`
- 文件：`src/api/static/review_console.html`

---

## MVP 功能范围

1. 上传图片/PDF  
- 调用：`POST /api/v1/grade/submit`

2. SSE 实时状态监听  
- 调用：`GET /api/v1/tasks/{task_id}/stream`

3. 待人工复核任务池  
- 调用：`GET /api/v1/tasks/pending-review`
- 支持过滤（按业务判定）：`status=REJECTED_UNREADABLE|SCORED`

4. 提交人工复核  
- 调用：`POST /api/v1/tasks/{task_id}/review`
- 提交 `human_feedback_json` 与 `is_regression_sample`

---

## 目标

把“上传 -> 机器判定 -> 待办分流 -> 人工回写”打通成一条可见、可操作、可验证的链路，作为后续前端产品化的基础壳。

---

## 限制（有意保持最小）

- 无登录与权限系统
- 无复杂样式框架
- 无富文本差异编辑器（当前以 JSON 直输）
- 无任务详情二次页面（保留后续迭代）

