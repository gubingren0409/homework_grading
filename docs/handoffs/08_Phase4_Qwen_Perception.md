# ENGINERING HANDOFF: AI-Driven Homework Grader Core
# TARGET PHASE: Phase 4 (Real VLM Perception Engine - Qwen)

## 1. 架构约束 (Architectural Constraints)
当前任务是实现接入通义千问视觉大模型（Qwen-VL）的真实感知引擎。
必须严格继承 `BasePerceptionEngine`。必须使用大模型的“结构化输出（Structured Outputs）”能力或强硬的 System Prompt，要求其严格返回符合 `PerceptionOutput` 契约的 JSON。
绝对禁止在代码中硬编码 API Key，必须引入 Pydantic 的 `BaseSettings` 进行环境变量注入。

## 2. 工程拓扑上下文 (Directory Context)
你的目标工作目录为 `src/core/` 和 `src/perception/engines/`。
你需要生成/重写以下文件：
1. `src/core/config.py`
2. `src/perception/engines/qwen_engine.py`

## 3. 核心实现规范 (Implementation Specifications)

### 任务 A: 环境变量与配置管理
**目标文件:** `src/core/config.py`
**设计规则:**
1. 引入 `pydantic_settings.BaseSettings`（注意：需在依赖中确保安装了 `pydantic-settings`）。
2. 定义 `Settings` 类，包含字段 `qwen_api_key: str` 和 `qwen_model_name: str = "qwen-vl-max"`。
3. 实例化全局 `settings` 对象。

### 任务 B: Qwen 视觉引擎实现
**目标文件:** `src/perception/engines/qwen_engine.py`
**设计规则:**
1. 继承 `src.perception.base.BasePerceptionEngine`。
2. 依赖导入：导入全局 `settings`，导入 `PerceptionOutput` 契约。
3. 实现异步方法：`async def process_image(self, image_bytes: bytes) -> PerceptionOutput`。
4. **内部逻辑编排**:
   - 图像预处理：将 `image_bytes` 转换为 Base64 字符串（用于 API 载荷）。
   - Prompt 工程：编写严厉的 System Prompt，告知模型其角色为“光学字符与公式结构解析器”，明确要求输出必须是合法的 JSON，且完全符合 `PerceptionOutput` 的 Schema 结构。
   - 网络调用：使用官方的 OpenAI 兼容接口形式（通过异步的 HTTP 客户端如 `httpx`，或异步的 `openai` SDK 配合 Qwen 的 base_url）发起请求。
   - 契约断言：提取模型返回的文本，去除可能存在的 Markdown 代码块标记（如 ```json），强制使用 `PerceptionOutput.model_validate_json(cleaned_text)` 进行反序列化。如果 Pydantic 校验失败，必须捕获并抛出内部的 `GradingSystemError`。

## 4. 执行指令 (Execution Directive)
收到此文件后，请立即输出 `src/core/config.py` 与 `src/perception/engines/qwen_engine.py` 的完整 Python 源码。代码必须通过严格的类型检查。网络请求部分必须妥善处理超时与底层连接异常。禁止输出解释性文本。
