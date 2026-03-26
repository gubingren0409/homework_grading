# Phase 27.2：R1 非流式缓存策略固化

## 背景
- 在 Phase 27.1 后，系统已具备双层防御（感知层 + 认知层）并稳定运行。
- 基准测试中观察到 `deepseek-reasoner` 流式路径存在较高解析失败概率，并触发降级。
- 用户提出关键假设：缓存命中率差异可能是主要性能瓶颈。

## 实验结论（已执行）
- 针对同题型样本执行 R1 非流式试验后，DeepSeek 请求响应显著加速，且未出现流式 JSON 截断问题。
- 业务结果保持一致：正常样本 `SCORED`，脏数据样本按既有防线拦截或拒绝，不破坏防御闭环。

## 本次工程落地
1. **配置层新增开关**
   - 文件：`src/core/config.py`
   - 新增：`deepseek_use_stream: bool = False`
   - 说明：默认关闭流式，优先稳定性与缓存收益；保留可回切能力。

2. **认知引擎改为配置驱动**
   - 文件：`src/cognitive/engines/deepseek_engine.py`
   - 主路径 `use_stream` 改为读取 `settings.deepseek_use_stream`。
   - 降级路径仍保持：`deepseek-chat + stream=False`（确定性兜底不变）。

3. **测试对齐**
   - 文件：`tests/test_deepseek_degradation_logic.py`
   - 测试中显式设置 `settings.deepseek_use_stream = True` 覆盖流式分支，并在 `finally` 恢复。
   - 断言更新为“首轮流式失败后，下一轮降级非流式成功”的实际行为。

4. **文档与示例更新**
   - `.env.example` 增加 `DEEPSEEK_USE_STREAM=false`。
   - `README.md` 增加流式开关说明与推荐值（默认 false）。

## 验证结果
- 命令：
  - `python -m pytest tests/test_deepseek_degradation_logic.py tests/test_schemas/test_cognitive_schema.py -q`
- 结果：
  - `3 passed`
  - 仅存在 `pytest-asyncio` 既有 deprecation warning（与本次改动无关）。

## 最终决策
- 将 **R1 非流式** 作为默认生产策略（`DEEPSEEK_USE_STREAM=false`）。
- 保留流式为可选能力（`DEEPSEEK_USE_STREAM=true`），用于需要实时 token 输出的特定场景。
