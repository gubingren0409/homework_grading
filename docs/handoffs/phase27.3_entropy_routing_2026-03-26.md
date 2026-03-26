# Phase 27.3：Entropy-Based Routing（三层漏斗）

## 目标
- 保持编排层 Phase 27.1 策略不变：仅对 `UNREADABLE` / `trigger_short_circuit` 硬拦截。
- 对 `HEAVILY_ALTERED` 高噪声样本，在认知层直接旁路 `deepseek-reasoner`，改走 `deepseek-chat`（非流式）。

## 实施内容

### 1) 编排层策略（回滚确认）
- 文件：`scripts/batch_grade.py`
- 状态：无需改动，当前已符合要求：
  - `HEAVILY_ALTERED` 仅记录 warning 并放行；
  - 仅 `UNREADABLE` 或 `trigger_short_circuit` 触发硬拦截。

### 2) 认知层动态路由（新增）
- 文件：`src/cognitive/engines/deepseek_engine.py`
- 新增判定：
  - 若 `perception_data.readability_status == "HEAVILY_ALTERED"`，
    - 直接 `model_to_use = "deepseek-chat"`
    - 强制 `use_stream = False`
    - 记录旁路 warning 日志
- 既有降级链路保留不变（网络/解析异常仍会走 `deepseek-chat + non-stream`）。

### 3) 回归测试
- 文件：`tests/test_deepseek_degradation_logic.py`
- 新增测试：`test_heavily_altered_bypasses_reasoner_to_v3`
  - 构造 `HEAVILY_ALTERED` 输入；
  - 断言首轮即调用 `deepseek-chat`；
  - 断言 `stream=False`。

### 4) 文档同步
- 文件：`README.md`
- 更新状态机表中 `HEAVILY_ALTERED` 的后续处理说明：
  - 放行编排层；
  - 认知层旁路至 DeepSeek-Chat 快速判定。

## 架构结果
- 已形成三层漏斗：
  1. 廉价算力（VLM）过滤底线；
  2. 快速算力（DeepSeek-Chat）处理高噪边缘样本；
  3. 昂贵算力（DeepSeek-Reasoner）聚焦高维逻辑推导。

## 说明
- 按指令本次未执行混沌样本复测。
- 仅做代码落盘与基线锁定准备。
