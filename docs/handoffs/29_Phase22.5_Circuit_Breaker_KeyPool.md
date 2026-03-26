# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 22.5 (Dynamic Circuit-Breaker Key Pool)

## 1. 架构变更 (Architectural Changes)
- **熔断机制引入**: 新增 `src/core/connection_pool.py`。废弃了简单的 `itertools.cycle` 轮询，改为具备状态感知的 `CircuitBreakerKeyPool`。
- **状态追踪**: 
  - 每个 API Key 现在具备 `HEALTHY` 或 `COOLDOWN` 状态。
  - 捕获 HTTP 429 (RateLimitError) 后，该 Key 自动进入 60 秒熔断期，调度器将自动跳过该 Key 尝试下一个健康资源。
- **引擎热插拔**:
  - `DeepSeekCognitiveEngine` 与 `QwenVLMPerceptionEngine` 已全面接入 `CircuitBreakerKeyPool`。
  - 实现了“瞬间切流”：捕获限流后仅休眠 0.5s 即可发起下一次 Key 切换重试，极大提升了并发吞吐上限。

## 2. 关键代码逻辑 (Circuit Breaker Core)
```python
def get_key_metadata(self) -> Dict[str, Any]:
    now = time.time()
    num_keys = len(self.keys_metadata)
    for _ in range(num_keys):
        meta = self.keys_metadata[self._current_index]
        # 自动解除过期熔断
        if meta["status"] == "COOLDOWN" and now >= meta["cooldown_until"]:
            meta["status"] = "HEALTHY"
        # 返回第一个健康 Key
        if meta["status"] == "HEALTHY":
            self._current_index = (self._current_index + 1) % num_keys
            return meta
        self._current_index = (self._current_index + 1) % num_keys
    raise AllKeysExhaustedError("All keys in pool are in COOLDOWN.")
```

## 3. 下一步计划 (Next Steps)
- 观测 Concurrency 6 下的熔断触发频率，动态调整 `cooldown_seconds`。
- 考虑引入 Redis 存储 Pool 状态以支持多进程/多容器集群共享 Key 状态。
