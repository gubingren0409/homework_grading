# Handoff: Phase 18 & 19 - Production Infrastructure & Async Load Testing

## 1. Phase 18: 生产环境容器化与限流防御
- **架构重构**: 引入 `slowapi` 中间件，在 `src/main.py` 挂载全局限流器。
- **端点防护**:
  - `POST /api/v1/grade/submit`: 限制为 `5次/分钟` (Protecting GPU/Reasoner resources)。
  - `GET /api/v1/grade/{task_id}`: 限制为 `30次/分钟` (Polling protection)。
- **容器化基建**:
  - `Dockerfile`: 基于 `python:3.11-slim`，非 root 用户运行，多层构建。
  - `docker-compose.yml`: 实现了持久化卷挂载 (`outputs/`, `data/`) 与外部环境配置 (`.env`) 的解耦。

## 2. Phase 19: 异步网关压测与防御验证
- **目标**: 验证 `slowapi` 的防御有效性与异步链路在高并发下的稳定性。
- **工具**: `scripts/load_test_async_api.py` (Async HTTPX Submitter & Poller)。
- **逻辑**: 
  - 强制触发 429 错误并执行指数退避重试。
  - 全量处理 `data/3.20_physics/question_02/students/` 下的 21 个物理样本。
  - 验证 `PENDING -> PROCESSING -> COMPLETED` 的闭环状态流转。

## 3. 当前系统状态
- **API 层**: 异步化完成，任务提交与结果检索解耦。
- **安全层**: 已挂载 IP 限流器，防止算力资源穿透。
- **部署层**: 具备物理隔离的容器化部署能力。
