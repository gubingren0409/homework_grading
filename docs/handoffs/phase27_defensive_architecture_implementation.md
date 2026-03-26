# Phase 27: 数字契约重构与混沌防御注入 - 实施报告

**时间**: 2026-03-25 22:38
**状态**: 已完成并验证
**类型**: 架构重构 - 容错机制强化

---

## 执行摘要

根据 Phase 27 架构指令，成功实施了三层防御机制，并通过混沌工程验证了系统的拒绝能力。系统现在能够在面对不可读输入时正确触发 Fail-Fast 机制，而不是强行幻觉脑补。

---

## 实施清单

### ✅ 一、Pydantic 契约重构

**目标文件**: `src/schemas/cognitive_ir.py`

**执行动作**:
- 在 `EvaluationReport` 类中新增 `status` 字段：
  ```python
  status: Literal["SCORED", "REJECTED_UNREADABLE"] = Field(
      default="SCORED", 
      description="任务状态。正常批改输出 SCORED；若输入无法阅读或逻辑完全破损则输出 REJECTED_UNREADABLE"
  )
  ```
- 将 `step_evaluations` 字段默认值设为空列表 `[]`，确保拒绝状态下的 JSON 合法性

**状态**: ✅ 已完成

---

### ✅ 二、认知层防御性 Prompt 注入

**目标文件**: `src/cognitive/engines/deepseek_engine.py`

**执行动作**:
在 `_system_prompt_grading_base` 中追加了"最高纪律：拒绝批改权"指令：
```
【最高纪律：拒绝批改权】
如果感知层提取的文本属于以下情况：极度残缺导致物理逻辑断裂、毫无意义的乱码、或与本题物理考点毫无关联（如纯粹的涂鸦提取物）。
你必须立即停止推导，直接输出 JSON：
将 `status` 设为 "REJECTED_UNREADABLE"，`total_score_deduction` 设为 0，`is_fully_correct` 设为 false，`step_evaluations` 设为空数组 []。并在总评语中简述拒绝原因。绝对禁止试图通过猜测来强行打分。
```

**状态**: ✅ 已完成

---

### ✅ 三、网关与编排层状态机拦截

**目标文件**: `scripts/batch_grade.py`

**执行动作**:
1. 在 `process_single_student` 函数中增加状态检测逻辑：
   ```python
   if report.status == "REJECTED_UNREADABLE":
       logger.warning(
           "Task rejected by Cognitive Engine for student %s: Unreadable or invalid input.",
           student_id
       )
   ```
2. 在返回字典中新增 `Status` 字段用于追踪任务状态
3. 更新 CSV 汇总表头，包含 `Status` 列

**状态**: ✅ 已完成

---

## 混沌工程验证结果

### 测试样本设计

构建了 4 个极端脏数据样本：

| 样本ID | 类型 | 描述 | 预期行为 |
|--------|------|------|---------|
| `stu_ans_97.png` | 全黑图像 | 800x1000 纯黑像素图 | 感知层拦截 |
| `stu_ans_98.png` | 纯噪点图 | 随机彩色像素噪点 | 感知层拦截 |
| `stu_ans_99.png` | 乱涂鸦图 | 白底+黑色粗笔随机涂抹 | 感知层拦截 |
| `stu_ans_96.png` | 无意义文本 | 清晰文字但与物理题无关（"Hello World", "喵喵喵"等） | **认知层拦截** |

### 验证结果

#### 1. 感知层防线（第一道防线）

**样本**: stu_ans_97, stu_ans_98, stu_ans_99

**结果**: ✅ **100% 拦截成功**

日志证据：
```
[ERROR] Failed to process student stu_ans_97: Workflow halted on page 0: Image quality too poor.
[ERROR] Failed to process student stu_ans_98: Workflow halted on page 0: Image quality too poor.
[ERROR] Failed to process student stu_ans_99: Workflow halted on page 0: Image quality too poor.
```

**结论**: 感知层的 `readability_status` 检测和 `trigger_short_circuit` 机制正常工作，阻止了完全不可读的图像进入认知层。

---

#### 2. 认知层防线（第二道防线）

**样本**: stu_ans_96（无意义文本但感知层可读）

**结果**: ✅ **拒绝机制成功触发**

日志证据：
```
[WARNING] Task rejected by Cognitive Engine for student stu_ans_96: Unreadable or invalid input.
```

输出 JSON 验证：
```json
{
  "status": "REJECTED_UNREADABLE",
  "is_fully_correct": false,
  "total_score_deduction": 0.0,
  "step_evaluations": [],
  "overall_feedback": "提取的文本内容与物理问题无关，无法进行批改。",
  "system_confidence": 0.8,
  "requires_human_review": true
}
```

CSV 汇总验证：
```csv
Student_ID,Total_Deduction,Is_Fully_Correct,Requires_Human_Review,Error_Status,Status
stu_ans_96,0.0,False,True,NONE,REJECTED_UNREADABLE
```

**结论**: 
- ✅ 模型正确识别了无意义输入
- ✅ 成功输出 `REJECTED_UNREADABLE` 状态
- ✅ 避免了强行打分的幻觉行为
- ✅ 状态正确落盘到数据库和 CSV
- ✅ 日志正确记录 WARNING 级别警告

---

## 系统架构改进点

### 防御深度（Defense in Depth）

系统现在具备**两层独立防御机制**：

```
输入图像
    ↓
[感知层防线] ← 检测图像质量、可读性
    ↓ (UNREADABLE → Short-circuit)
    ↓ (可读但无意义 → 放行)
[认知层防线] ← 检测语义相关性
    ↓ (无关内容 → REJECTED_UNREADABLE)
    ↓ (正常内容)
正常批改流程
```

### Fail-Fast 原则落地

- **旧行为**: 强制大模型对垃圾数据进行脑补打分，导致幻觉结果
- **新行为**: 立即拒绝并标记状态，避免浪费计算资源和产生误导性结果

### 可观测性增强

- 数据库记录完整状态（包括 `REJECTED_UNREADABLE`）
- CSV 汇总包含 `Status` 列，便于批量审查
- 日志明确区分感知层拦截（ERROR）和认知层拒绝（WARNING）

---

## 遗留问题与后续优化

### 已识别的改进空间

1. **混沌测试覆盖度**：
   - 当前测试仅覆盖极端情况
   - 建议扩展到"边界模糊"样本（部分可读+部分乱码）

2. **拒绝原因细分**：
   - 当前仅有 `REJECTED_UNREADABLE` 一个状态
   - 可考虑细化为：`REJECTED_BLANK`、`REJECTED_UNREADABLE`、`REJECTED_IRRELEVANT`

3. **人工复核流程**：
   - 当前 `requires_human_review=True` 后无后续流程
   - 需配合 HITL (Human-in-the-Loop) 接口实施

---

## 依赖文件清单

| 文件路径 | 变更类型 | 描述 |
|---------|---------|------|
| `src/schemas/cognitive_ir.py` | 修改 | 添加 `status` 字段 |
| `src/cognitive/engines/deepseek_engine.py` | 修改 | 注入拒绝权 Prompt |
| `scripts/batch_grade.py` | 修改 | 添加状态拦截逻辑 |
| `data/3.20_physics/question_05/chaos_test_students/*` | 新增 | 混沌测试样本 |
| `outputs/batch_results/chaos_test/*` | 新增 | 验证结果输出 |

---

## 下一步行动建议

根据快照评论中的优先级排序：

✅ **Phase 27 完成**: 容错机制（Fail-Fast）  
⏭️ **Phase 28**: 高并发架构（消息队列解耦）  
⏭️ **Phase 29**: 反馈闭环（HITL 数据拦截）

---

## 附录：测试命令

```bash
# 环境准备
cd E:\ai批改\homework_grader_system
$env:PYTHONPATH = "E:\ai批改\homework_grader_system"

# 执行混沌测试
python scripts/batch_grade.py \
  --students_dir data/3.20_physics/question_05/chaos_test_students \
  --rubric_file outputs/q5_rubric.json \
  --output_dir outputs/batch_results/chaos_test \
  --db_path outputs/grading_database.db \
  --concurrency 1
```

---

**审查人**: GitHub Copilot CLI  
**实施状态**: 验证通过，生产就绪  
**风险评估**: 低（向后兼容，仅新增防御能力）
