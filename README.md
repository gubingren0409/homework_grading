# 🤖 AI Homework Grading System

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Phase](https://img.shields.io/badge/Phase-27.1-brightgreen.svg)](docs/handoffs/)

基于多模态大模型的智能作业批改系统，采用双引擎架构（Qwen-VL + DeepSeek-R1），实现自动化、高精度的理科作业评分。

---

## ✨ 核心特性

### 🎯 双引擎架构
- **感知层 (Qwen-VL)**: 视觉信息提取，OCR + 公式识别 + 图表理解
- **认知层 (DeepSeek-R1)**: 逻辑推理与评分，支持复杂物理/数学推导验证
- **Skill 扩展层 (可选)**: 外部版面解析与客观校验（默认关闭，失败可回退）

### 🛡️ 分层防御机制
- **感知层防线**: 图像质量检测，拦截完全不可读的输入
- **认知层防线**: 语义相关性判断，拒绝逻辑断裂和无关内容
- **状态机驱动**: `SCORED` / `REJECTED_UNREADABLE` 明确区分

### 🚀 生产特性
- ⚡ **异步批量处理**: 基于 `asyncio.gather` 的高并发批改
- 🔄 **熔断器模式**: API Key 池化 + 自动故障转移
- 📊 **持久化存储**: SQLite 数据库 + JSON 报告双轨落盘
- 🔍 **增量重跑**: 自动跳过已成功批改的样本
- 📈 **降级策略**: DeepSeek-R1 → DeepSeek-Chat 自动降级
- 🎯 **混沌工程**: 极端数据验证，100% 拦截准确率

---

## 🏗️ 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      API Gateway (FastAPI)                  │
│                  /api/v1/evaluate (POST)                    │
└────────────────────────────┬────────────────────────────────┘
                             ↓
┌─────────────────────────────────────────────────────────────┐
│              Orchestration Workflow (GradingWorkflow)       │
└────────────────────────────┬────────────────────────────────┘
                             ↓
                    ┌────────┴────────┐
                    ↓                 ↓
        ┌───────────────────┐  ┌─────────────────┐
        │  Perception Layer │  │ Cognitive Layer │
        │   (Qwen-VL-Max)   │  │ (DeepSeek-R1)   │
        ├───────────────────┤  ├─────────────────┤
        │ • OCR 识别        │  │ • 逻辑验证      │
        │ • 公式提取        │  │ • 按 Rubric 扣分│
        │ • 图表理解        │  │ • 语义判断      │
        │ • 质量检测        │  │ • 拒绝权        │
        └───────────────────┘  └─────────────────┘
                    │                 │
                    └────────┬────────┘
                             ↓
                ┌────────────────────────┐
                │   Persistence Layer    │
                │  • SQLite Database     │
                │  • JSON Reports        │
                │  • CSV Summaries       │
                └────────────────────────┘
```

---

## 🚀 快速开始

### 环境要求
- Python 3.11+
- pip 或 conda

### 安装依赖

```bash
cd homework_grader_system
pip install -r requirements.txt
```

### 配置环境变量

创建 `.env` 文件：

```bash
# Qwen-VL API Keys (支持多 Key 池化)
QWEN_API_KEYS=sk-xxx,sk-yyy,sk-zzz

# DeepSeek API Keys (支持多 Key 池化)
DEEPSEEK_API_KEYS=sk-aaa,sk-bbb,sk-ccc,sk-ddd

# 模型配置（可选）
QWEN_MODEL_NAME=qwen-vl-max
PERCEPTION_PROVIDER=qwen
DEEPSEEK_MODEL_NAME=deepseek-reasoner
DEEPSEEK_USE_STREAM=false  # 推荐 false：缓存命中率更高

# Optional Skills (Phase 43, all disabled by default)
SKILL_LAYOUT_PARSER_ENABLED=false
SKILL_LAYOUT_PARSER_PROVIDER=none
SKILL_LAYOUT_PARSER_API_URL=
SKILL_LAYOUT_PARSER_API_KEY=
SKILL_VALIDATION_ENABLED=false
SKILL_VALIDATION_PROVIDER=none
SKILL_VALIDATION_API_URL=
SKILL_VALIDATION_API_KEY=
SKILL_VALIDATION_FAIL_OPEN=true

# Runtime Router / Auto Circuit (Phase B)
AUTO_CIRCUIT_CONTROLLER_ENABLED=true
AUTO_CIRCUIT_FAILURE_RATE_THRESHOLD=0.30
AUTO_CIRCUIT_TOKEN_SPIKE_THRESHOLD=1.80
AUTO_CIRCUIT_MIN_SAMPLES=20
ROUTER_BUDGET_TOKEN_LIMIT=9000
```

当你准备接入三方 Skill 时：
- 版面解析：`SKILL_LAYOUT_PARSER_PROVIDER=llamaparse` 或 `unstructured`
- 客观校验：`SKILL_VALIDATION_PROVIDER=e2b`
- 建议先在灰度环境开启，并保留 `SKILL_VALIDATION_FAIL_OPEN=true`

**⚠️ 重要：代理环境优化**

如果你的系统使用了代理（如 Clash、V2Ray），强烈建议设置 `NO_PROXY` 让国内 API 直连：

```bash
# Windows PowerShell（推荐）
$env:NO_PROXY = "localhost,127.0.0.1,*.aliyuncs.com,*.deepseek.com,*.cn"
[System.Environment]::SetEnvironmentVariable("NO_PROXY", "localhost,127.0.0.1,*.aliyuncs.com,*.deepseek.com,*.cn", "User")

# Linux/macOS
export NO_PROXY=localhost,127.0.0.1,*.aliyuncs.com,*.deepseek.com,*.cn
```

**效果**：响应速度提升 30-50%，稳定性显著改善。详见 [代理优化文档](docs/handoffs/proxy_optimization_2026-03-26.md)。

### 运行批量批改

```bash
python scripts/batch_grade.py \
  --students_dir data/3.20_physics/question_05/students \
  --rubric_file outputs/q5_rubric.json \
  --output_dir outputs/batch_results/q05 \
  --db_path outputs/grading_database.db \
  --concurrency 3
```

### 启动 API 服务

```bash
# 开发模式
python src/api/main.py

# 生产模式
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

API 文档：http://localhost:8000/docs

### 关键运维接口（Phase B）

- `GET /api/v1/metrics/provider-benchmark`：Provider 基准视图（任务量、吞吐、失败率、fallback 率、token 分布、成本代理指标）
- `GET /api/v1/router/policy`：运行时路由策略与实时快照（阈值、预算、是否触发自动降级）

### 数据与评测接口（Phase C）

- `GET /api/v1/metrics/dataset-pipeline`：样本资产闭环摘要（golden assets 总量、已入库量、待入库量、复核队列）
- `GET /api/v1/metrics/runtime-dashboard`：在线指标看板聚合（provider 命中、fallback 触发、prompt cache 命中、人工复核率）

### Phase C 回归矩阵自动化

```bash
python scripts/run_phasec_regression_matrix.py
```

默认会执行：
- 契约/SSE 回归（包含 Phase C 新接口）
- 状态幂等与路由回归
- Payload 边界回归
- 启动本地 API 并采样 `runtime-dashboard` 延迟

输出报告：`outputs/phasec_regression_matrix_report.json`

### 启动 Celery Worker（异步批改必需）

```bash
# Linux / macOS
celery -A src.worker.main worker --loglevel=info --concurrency=4

# Windows（强烈建议）
celery -A src.worker.main worker --loglevel=info --pool=solo --concurrency=1
```

> 说明：Celery 官方 FAQ 标注 Windows 非正式支持；在 Windows 上使用 `solo` 池更稳定，可避免 `billiard/fast_trace_task` 类崩溃。

---

## 📖 使用指南

### 1. 生成评分标准（Rubric）

```bash
python scripts/extract_rubric.py \
  --model_answer_dir data/3.20_physics/question_05/standard \
  --output_file outputs/q5_rubric.json
```

### 2. 批量批改

```bash
python scripts/batch_grade.py \
  --students_dir <学生答卷目录> \
  --rubric_file <评分标准JSON> \
  --output_dir <输出目录> \
  --concurrency <并发数>
```

**输出文件**：
- `<学生ID>.json`: EvaluationReport（评分报告）
- `<学生ID>_full.json`: 完整输出（含感知层 + 认知层）
- `summary.csv`: 批量汇总表

### 3. API 调用

```bash
curl -X POST "http://localhost:8000/api/v1/evaluate" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@student_answer.jpg" \
  -F "rubric=@rubric.json"
```

### 4. 单样本测试

```bash
python scripts/grade_student.py \
  --student_file data/test/stu_ans_01.png \
  --rubric_file outputs/q5_rubric.json
```

---

## 🔧 核心配置

### 🌐 代理环境优化（重要！）

如果系统使用了全局代理（Clash、V2Ray），**务必配置 NO_PROXY 让国内 API 直连**：

```powershell
# Windows - 永久设置
$no_proxy = "localhost,127.0.0.1,*.aliyuncs.com,*.deepseek.com,*.cn"
[System.Environment]::SetEnvironmentVariable("NO_PROXY", $no_proxy, "User")
```

```bash
# Linux/macOS - 添加到 ~/.bashrc
export NO_PROXY=localhost,127.0.0.1,*.aliyuncs.com,*.deepseek.com,*.cn
```

**为什么？**
- 国内 API 走代理会降速 30-50% 并增加错误率
- 之前的并发速率问题可能部分由此引起
- 详见：[代理优化文档](docs/handoffs/proxy_optimization_2026-03-26.md)

---

### API Key 池化（高并发）

系统支持多 API Key 自动轮转和熔断器保护：

```bash
# .env 配置
QWEN_API_KEYS=key1,key2,key3       # 逗号分隔
DEEPSEEK_API_KEYS=key1,key2,key3,key4
```

### DeepSeek 流式开关（性能建议）

```bash
# .env 配置
DEEPSEEK_USE_STREAM=false
```

- `false`（推荐）：非流式，缓存命中率更高，响应更稳定，避免流式 JSON 截断。
- `true`：流式，适合需要实时 token 输出的场景，但缓存收益通常较低。

**特性**：
- 🔄 Round-Robin 轮询
- 🚨 触发 429 自动熔断 60 秒
- ⚡ 自动故障转移到健康 Key
- 📊 所有 Key 耗尽时抛出明确错误

### 并发控制

```bash
--concurrency N  # 建议值：2-5（取决于 API Key 数量和 RPM 限制）
```

---

## 📊 状态机设计

### EvaluationReport.status

| 状态 | 含义 | 触发条件 |
|------|------|---------|
| `SCORED` | 正常批改 | 输入可读且逻辑完整 |
| `REJECTED_UNREADABLE` | 拒绝批改 | 输入无法理解或逻辑断裂 |

### PerceptionOutput.readability_status

| 状态 | 含义 | 后续处理 |
|------|------|---------|
| `CLEAR` | 清晰可读 | 正常流转 |
| `MINOR_ALTERATION` | 轻微问题 | 正常流转 |
| `HEAVILY_ALTERED` | 严重涂改但可提取 | 放行编排层；认知层旁路到 DeepSeek-Chat 快速判定 |
| `UNREADABLE` | 完全不可读 | 立即拦截 |

---

## 🧪 测试

### 运行单元测试

```bash
pytest tests/ -v
```

### 边界测试

```bash
# 感知层边界测试
pytest tests/test_qwen_boundary.py -v

# 认知层边界测试
pytest tests/test_deepseek_boundary.py -v

# 降级逻辑测试
pytest tests/test_deepseek_degradation_logic.py -v
```

### 混沌工程测试

```bash
python scripts/batch_grade.py \
  --students_dir data/3.20_physics/question_05/chaos_test_students \
  --rubric_file outputs/q5_rubric.json \
  --output_dir outputs/chaos_test \
  --concurrency 1
```

验证极端样本（全黑图、噪点图、涂鸦）的拦截效果。

---

## 📐 数据格式

### 输入

#### 图像格式
- 支持：`.jpg`, `.jpeg`, `.png`, `.pdf`
- 推荐分辨率：800x1000 以上
- 颜色空间：RGB

#### Rubric 格式
```json
{
  "question_id": "5",
  "question_type": "选择题+简答题",
  "correct_answer": "a; clockwise",
  "grading_points": [
    {
      "point_id": "1",
      "description": "正确应用 LC 频率公式",
      "score_if_wrong": 2.0
    }
  ]
}
```

### 输出

#### EvaluationReport
```json
{
  "status": "SCORED",
  "is_fully_correct": false,
  "total_score_deduction": 2.0,
  "step_evaluations": [...],
  "overall_feedback": "第一步公式正确，但开关选择错误...",
  "system_confidence": 0.85,
  "requires_human_review": false
}
```

---

## 🏛️ 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| **API 层** | FastAPI + Uvicorn | RESTful API 服务 |
| **编排层** | asyncio | 异步工作流调度 |
| **感知层** | Qwen-VL-Max | 视觉多模态理解 |
| **认知层** | DeepSeek-R1 Reasoner | 逻辑推理与评分 |
| **持久层** | SQLite + aiosqlite | 异步数据库 |
| **数据层** | Pydantic | Schema 验证 |
| **测试层** | pytest + pytest-mock | 单元测试与集成测试 |

---

## 📂 项目结构

```
homework_grader_system/
├── src/
│   ├── api/              # FastAPI 路由与依赖注入
│   ├── cognitive/        # 认知引擎（DeepSeek）
│   ├── perception/       # 感知引擎（Qwen-VL）
│   ├── orchestration/    # 工作流编排
│   ├── schemas/          # Pydantic 数据模型
│   ├── db/               # 数据库客户端
│   ├── core/             # 核心配置与异常
│   └── utils/            # 工具函数
├── scripts/              # CLI 工具集
│   ├── batch_grade.py    # 批量批改
│   ├── extract_rubric.py # Rubric 生成
│   └── grade_student.py  # 单样本测试
├── tests/                # 测试套件
│   ├── test_qwen_boundary.py
│   ├── test_deepseek_boundary.py
│   └── test_e2e_real_pipeline.py
├── docs/
│   └── handoffs/         # 30+ 阶段快照文档
├── configs/              # 配置文件
├── data/                 # 数据集（不入库）
├── outputs/              # 输出结果（不入库）
├── requirements.txt      # Python 依赖
└── .env.example          # 环境变量模板
```

---

## ⚙️ 高级配置

### 降级策略

当 DeepSeek-R1 流式响应出现网络异常时，自动降级到 DeepSeek-Chat：

```python
# src/cognitive/engines/deepseek_engine.py
MAX_CONNECTION_ERRORS = 1  # 触发阈值
```

### 人工复核触发

```python
# EvaluationReport 字段
requires_human_review: bool  # True 时强制人工介入
```

触发条件：
- 逻辑极度混乱无法还原学生意图
- 上游视觉提取疑似发生灾难性乱码
- 异常解法（创新或超纲）

---

## 🔬 防御机制验证

### 混沌工程测试

系统通过以下极端样本验证：

| 样本 | 类型 | 防线 | 结果 |
|------|------|------|------|
| 全黑图像 | 完全不可读 | 感知层 | ✅ 拦截 |
| 纯噪点图 | 随机像素 | 感知层 | ✅ 拦截 |
| 乱涂鸦图 | 逻辑断裂 | 认知层 | ✅ 拒绝 (`REJECTED_UNREADABLE`) |
| 无关文本 | 语义不符 | 认知层 | ✅ 拒绝 (`REJECTED_UNREADABLE`) |

---

## 📝 开发指南

### 添加新的题型支持

1. **扩展 Perception Prompt**（`src/perception/prompts.py`）
2. **更新 Schema**（`src/schemas/perception_ir.py`）
3. **添加单元测试**（`tests/test_qwen_boundary.py`）

### 自定义评分规则

1. 修改 `src/cognitive/engines/deepseek_engine.py` 中的 `_system_prompt_grading_base`
2. 运行边界测试验证：`pytest tests/test_deepseek_boundary.py -v`

### 贡献代码

```bash
# 创建功能分支
git checkout -b feature/your-feature-name

# 开发并测试
pytest tests/ -v

# 提交
git commit -m "feat: your feature description"
git push origin feature/your-feature-name
```

---

## 📊 已验证场景

- ✅ 物理计算题（力学、电学、光学）
- ✅ 多步推导题（公式变换、数值计算）
- ✅ 选择题 + 简答题混合
- ✅ 涂改容忍（OCR 误差、笔误、跳步）
- ✅ 空白卷短路
- ✅ 多页 PDF 合并处理
- ⚠️ 多题同页“布局切片严格对齐”（Phase 35）已完成代码落地，尚缺真实样本验收

### 🔎 Phase 35 当前状态（显式说明）

- 已完成：对称布局预处理管线（`REFERENCE` / `STUDENT_ANSWER` 同一切片链路）
- 已完成：`LayoutIR` 契约与坐标清洗、物理切片器、CLI 验证脚本
- 未完成：缺少“多题同页+对应标准答案同页”的真实数据验收与 A/B 归档
- 安全性：默认关闭，不影响当前主流程
  - 配置项：`enable_layout_preprocess=false`

> 说明：该能力当前属于“可灰度启用的预备能力”，不会阻塞后续功能开发。

---

## 🛣️ 开发路线图

### ✅ 已完成
- Phase 1-14: 核心引擎与持久化
- Phase 15-22: 稳定性与容错
- Phase 23-26: 流式处理与降级
- Phase 27: 拒绝机制与混沌验证
- Phase 27.1: 防御层级优化

### 🔄 进行中
- Phase 28: 消息队列解耦（Celery + Redis）
- Phase 29: HITL 反馈闭环（Human-in-the-Loop）

### 📅 规划中
- 动态 Few-Shot 样本库
- 教师复核界面
- 模型微调管道
- 多语言支持

---

## 📄 文档

- [架构快照文档](docs/handoffs/) - 30+ 份阶段演进记录
- [API 文档](http://localhost:8000/docs) - FastAPI 自动生成
- [测试覆盖报告](tests/) - pytest 输出

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

请确保：
1. 代码通过所有测试：`pytest tests/ -v`
2. 遵循现有代码风格
3. 更新相关文档
4. 提交信息清晰（参考 [Conventional Commits](https://www.conventionalcommits.org/)）

---

## 📜 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

---

## 🙏 致谢

- **Qwen-VL**: 阿里云通义千问视觉模型
- **DeepSeek-R1**: DeepSeek Reasoner 推理引擎
- **OpenAI SDK**: 兼容模式 API 客户端

---

## 📧 联系

- GitHub: [@gubingren0409](https://github.com/gubingren0409)
- 仓库: [homework_grading](https://github.com/gubingren0409/homework_grading)

---

**Built with ❤️ for Education**
