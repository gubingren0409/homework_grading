# 文档导航索引

这份索引的目标只有一个：

> **帮助你绕开 phase 历史噪音，直接建立对“当前系统”的理解。**

---

## 1. 当前应优先阅读的文档

| 文档 | 用途 |
| --- | --- |
| `README.md` | 当前系统总览、结构、进度、运行方式 |
| `EXECUTIVE_SUMMARY.md` | 快速获取审计结论、成熟度、主要风险 |
| `AUDIT_REPORT.md` | 查看详细技术判断与维护重点 |
| `docs/product_strategy_cn.md` | 看市场定位、交互场景、前端设计建议 |
| `docs/deployment_guide_cn.md` | 看单机/试点部署步骤 |
| `docs/production_readiness_cn.md` | 看生产前检查清单与单点风险 |
| `docs/postgresql_migration_plan_cn.md` | 看 SQLite -> PostgreSQL 升级规划 |
| `docs/demo_script_cn.md` | 看对外演示脚本与讲解顺序 |
| `docs/go_to_market_cn.md` | 看对外口径、试点场景与 FAQ |

---

## 2. 按目标选择阅读路径

### 路径 A：我想重新理解整个项目

1. `README.md`
2. `EXECUTIVE_SUMMARY.md`
3. `AUDIT_REPORT.md`
4. `docs/product_strategy_cn.md`

### 路径 B：我想重新接手代码维护

1. `README.md`
2. `AUDIT_REPORT.md`
3. `src/main.py`
4. `src/api/routes.py`
5. `src/worker/main.py`
6. `src/orchestration/workflow.py`

### 路径 C：我想先想清楚它该卖什么

1. `EXECUTIVE_SUMMARY.md`
2. `docs/product_strategy_cn.md`

### 路径 D：我想追历史决策

1. 先读 `README.md`
2. 再读 `docs/handoffs/`

---

## 3. 当前最重要的认知前提

1. **主工程在 `homework_grader_system/`，不是仓库根目录那些早期脚本。**
2. 当前最完整的是 **后端批改链路与平台控制面**。
3. 当前最薄弱的是 **上线治理、交付包装、以及更深层结构拆分**。
4. `docs/handoffs/` 是历史材料，不适合作为当前入口。

---

## 4. 建议先搞清楚的四件事

1. 它已经不是 demo，而是后端系统。
2. 它最适合先定义为教师侧 AI 阅卷与复核工作台。
3. 它的主要风险是维护认知成本，不是单纯功能不够。
4. 现在最值钱的工作不是继续堆 phase，而是让系统更可理解、更可交付、更可售卖。
