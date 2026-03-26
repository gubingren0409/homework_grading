# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 26 & 26.1 (Perception-Cognition Alignment & Edge Cases)
## 1. 架构变更 (Architectural Changes)
- **感知层 (VLM) 规则硬注入**:
  - `is_blank` 拦截: 空白/无作答卷强制输出 `{"is_blank": true}`，取代“图像质量低”。
  - 涂改屏蔽: 忽略划线作废内容。
  - 序位锚定: 多空题严格遵循键值对顺序，漏答必须显式输出 `null` 或 `"未作答"`。
  - 置信度标记收缩: `[]` 仅限用于填空/简答题的孤立难辨字，**绝对禁止**污染计算题的数学公式提取。
- **认知层 (LLM) 评判纪判**:
  - 引入抗位移评判，依据 Key 独立判分，禁止因漏答触发连坐扣分。
  - 引入 OCR 容错，物理语义等价或视觉形近字（如 $\alpha$ 与 a）免于扣分。
- **网关层 (API Gateway) 状态机阻断**:
  - 捕获到 `is_blank` 后，直接赋 0 分并流转至 `COMPLETED`，物理切断后续昂贵的 Reasoner 算力消耗。