# Phase 38 Handoff: 领域硬切割 + Phase35 空间契约验收

## 背景
按架构审查结论，系统从“混合复核语义”切换到“双管线物理隔离”：

- 数据卫生清洗流（Data Hygiene Pipeline）
- 教师高价值反馈流（Annotation Asset Pipeline）

并将前端“原图标注”需求反向约束到底层 Phase35 坐标契约。

## 本阶段已完成

### 1) 数据层硬切割（Schema + DB Client）

新增并启用两张物理隔离表：

- `hygiene_interception_log`
  - 关键字段：`trace_id`, `task_id`, `interception_node`, `raw_image_path`, `action`, `created_at`
  - 节点枚举：`blank`, `short_circuit`, `unreadable`
  - 操作枚举：`discard`, `manual_review`

- `golden_annotation_assets`
  - 关键字段：`trace_id`, `task_id`, `region_id`, `region_type`, `image_width`, `image_height`, `bbox_coordinates`, `perception_ir_snapshot`, `cognitive_ir_snapshot`, `teacher_text_feedback`, `expected_score`, `is_integrated_to_dataset`
  - 明确不包含 OCR 字段
  - 保留快照深拷贝，保证后续微调可重演

对应 DB Client 新增能力：

- hygiene：新增记录、分页查询、单条动作更新、批量动作更新
- golden：新增资产写入、分页查询

### 2) API 契约重构（视图A/视图B）

新增运维视图 A 接口：

- `GET /api/v1/hygiene/interceptions`
- `POST /api/v1/hygiene/interceptions/{record_id}/action`
- `POST /api/v1/hygiene/interceptions/bulk-action`

新增教学视图 B 接口：

- `POST /api/v1/annotations/feedback`
- `GET /api/v1/annotations/assets`

`POST /api/v1/annotations/feedback` 强制执行空间契约校验：

1. `task_id` 必须存在且 `grading_status == SCORED`
2. `bbox` 必须在原图像素坐标边界内，且满足单调性
3. `region_id` 必须存在于 `perception_ir_snapshot.regions`
4. `region_type` 必须与 Perception 快照一致
5. 提交 bbox 必须被源 region bbox 包含
6. `cognitive_ir_snapshot.step_evaluations` 必须有 `reference_element_id == region_id`

任一不满足返回 `HTTP 422`，阻断脏数据进入黄金资产库。

### 3) Worker 卫生流接线

在 worker 中增加 hygiene 日志写入：

- 感知短路异常（`PerceptionShortCircuitError`）直接落 `hygiene_interception_log`
- 评估结果为 `REJECTED_UNREADABLE` 时也落 hygiene 日志

此外，空白卷短路结果统一标记 `EvaluationReport.status="REJECTED_UNREADABLE"`，实现流程语义一致。

### 4) Phase35 契约验收（脚本 + 测试）

新增验收脚本：

- `scripts/validate_phase35_contract.py`
  - 校验 `LayoutIR.regions` 的 bbox 合法性
  - 校验 cognition 文本评语对 perception anchor 的 ID 绑定

新增测试：

- `tests/test_phase35_contract_acceptance.py`
- `tests/test_phase35_contract_script.py`
- fixtures:
  - `tests/fixtures/phase35/layout_extreme_misalignment.json`
  - `tests/fixtures/phase35/cognition_anchor_valid.json`

并新增 API/领域切割测试：

- `tests/test_phase38_domain_split_api.py`

## 兼容性说明

- 旧接口（`/tasks/{task_id}/review`, `/review/regression-samples`）仍保留，避免一次性破坏既有集成。
- 新策略下，推荐业务只写入 `/annotations/feedback` 进入黄金资产流；卫生流只通过 hygiene 接口运维处理。

## 下一阶段建议

1. 逐步下线 `human_feedback_json + is_regression_sample` 在业务路径中的写入
2. 在前端 Canvas 接入前，先用真实多题同页样本跑 Phase35 A/B 验收
3. 为 `golden_annotation_assets` 增加数据集集成批处理任务（仅消费 `is_integrated_to_dataset=false`）
