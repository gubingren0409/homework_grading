# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 12B (VLM Coordinate Sanitization & Clipping)
## 1. 架构约束 (Architectural Constraints)
在引擎适配器层拦截并修复大模型的坐标幻觉，绝对禁止越界坐标污染 Pydantic 数据契约。
## 2. 工程拓扑上下文 (Directory Context)
目标工作目录为 `src/perception/engines/`。
你需要修改以下文件：
1. `src/perception/engines/qwen_engine.py`
## 3. 核心实现规范 (Implementation Specifications)
### 任务: 坐标边界截断算子
**目标文件:** `src/perception/engines/qwen_engine.py`
**设计规则:**
1. 定位到解析 VLM 返回 JSON 的方法（例如 `_parse_response` 或直接在 `process_image` 中处理反序列化字典的地方）。
2. 在调用 `PerceptionOutput(**parsed_dict)` 之前，插入清洗逻辑。
3. **清洗算法:** 遍历 `parsed_dict.get("elements", [])`，如果元素存在 `bbox`，强制执行上下界截断：
   ```python
   for elem in parsed_dict.get("elements", []):
       if "bbox" in elem and isinstance(elem["bbox"], (list, dict)):
           # 兼容列表 [x_min, y_min, x_max, y_max] 或 字典 {"x_min": ..., ...}
           if isinstance(elem["bbox"], list):
               elem["bbox"] = [max(0.0, min(1.0, float(c))) for c in elem["bbox"]]
           elif isinstance(elem["bbox"], dict):
               for k, v in elem["bbox"].items():
                   elem["bbox"][k] = max(0.0, min(1.0, float(v)))
   ```
4. 确保处理完后再将清洗后的字典解包传给 Pydantic 模型。
## 4. 执行指令 (Execution Directive)
收到此文件后，立即输出包含截断防腐逻辑的 `qwen_engine.py` 完整源码。严禁修改现有的异步调用逻辑。禁止输出解释性文本。
