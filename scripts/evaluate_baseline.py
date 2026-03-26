import asyncio
import json
import logging
import traceback
import argparse
import csv
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple

from tqdm.asyncio import tqdm
from pydantic import ValidationError

# 导入现有架构组件
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.workflow import GradingWorkflow
from src.schemas.rubric_ir import TeacherRubric
from src.core.exceptions import PerceptionShortCircuitError, GradingSystemError

# 配置日志 - 仅记录重要错误到文件
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filename="outputs/baseline_eval.log"
)
logger = logging.getLogger(__name__)

class BaselineEvaluator:
    def __init__(self, concurrency: int = 2):
        self.perception = QwenVLMPerceptionEngine()
        self.cognitive = DeepSeekCognitiveEngine()
        self.workflow = GradingWorkflow(self.perception, self.cognitive)
        self.semaphore = asyncio.Semaphore(concurrency)
        
        # 指标统计
        self.metrics = {
            "total_cases": 0,
            "success_count": 0,
            "perception_schema_errors": 0,
            "cognitive_schema_errors": 0,
            "short_circuit_count": 0,  # [DISASTER] 或 [MESSY]
            "api_timeout_errors": 0,
            "unexpected_errors": 0
        }
        self.bad_cases_dir = Path("outputs/bad_cases")
        self.bad_cases_dir.mkdir(parents=True, exist_ok=True)

    def _log_bad_case(self, student_path: Path, error_type: str, details: str, raw_response: str = None):
        """记录 Bad Case 到本地 JSON 文件"""
        case_id = f"{error_type}_{student_path.stem}_{datetime.now().strftime('%H%M%S')}"
        case_data = {
            "timestamp": datetime.now().isoformat(),
            "student_file": str(student_path),
            "error_type": error_type,
            "error_details": details,
            "raw_model_response": raw_response,
            "stack_trace": traceback.format_exc()
        }
        with open(self.bad_cases_dir / f"{case_id}.json", "w", encoding="utf-8") as f:
            json.dump(case_data, f, ensure_ascii=False, indent=2)

    async def evaluate_single_case(self, question_id: str, student_path: Path, rubric: TeacherRubric):
        """执行单个测试用例的批改并捕获异常"""
        async with self.semaphore:
            self.metrics["total_cases"] += 1
            try:
                # 构建输入数据
                files_data = [(student_path.read_bytes(), student_path.name)]
                
                # 执行核心流水线
                # 注意：此处加入少量延时以防止 API 频率限制
                await asyncio.sleep(1.0) 
                
                report = await self.workflow.run_pipeline(files_data, rubric=rubric)
                self.metrics["success_count"] += 1
                return True

            except ValidationError as ve:
                # 区分是感知层还是认知层的 Schema 崩溃
                err_msg = str(ve)
                if "PerceptionOutput" in err_msg:
                    self.metrics["perception_schema_errors"] += 1
                    self._log_bad_case(student_path, "PERCEPTION_SCHEMA_FAIL", err_msg)
                else:
                    self.metrics["cognitive_schema_errors"] += 1
                    self._log_bad_case(student_path, "COGNITIVE_SCHEMA_FAIL", err_msg)
            
            except PerceptionShortCircuitError as pse:
                # 捕获拒答机制 [DISASTER] / [MESSY]
                self.metrics["short_circuit_count"] += 1
                self._log_bad_case(student_path, "SHORT_CIRCUIT", pse.message)

            except GradingSystemError as gse:
                # 捕获 API 超时或业务逻辑报错
                if "timeout" in str(gse).lower():
                    self.metrics["api_timeout_errors"] += 1
                else:
                    self.metrics["unexpected_errors"] += 1
                self._log_bad_case(student_path, "SYSTEM_ERROR", str(gse))

            except Exception as e:
                self.metrics["unexpected_errors"] += 1
                self._log_bad_case(student_path, "UNEXPECTED", str(e))
            
            return False

    def print_markdown_report(self):
        """生成并打印 Markdown 汇总报告"""
        m = self.metrics
        success_rate = (m["success_count"] / m["total_cases"] * 100) if m["total_cases"] > 0 else 0
        
        report = f"""
# 🚀 AI 批改系统基准测试汇总报告 ({datetime.now().strftime('%Y-%m-%d')})

| 指标项目 | 统计数值 | 百分比 |
| :--- | :--- | :--- |
| **总测试用例数** | {m["total_cases"]} | 100% |
| **成功批改数 (Track 2)** | {m["success_count"]} | {success_rate:.1f}% |
| **感知层 Schema 崩溃 (Qwen)** | {m["perception_schema_errors"]} | {(m["perception_schema_errors"]/m["total_cases"]*100):.1f}% |
| **认知层 Schema 崩溃 (DeepSeek)** | {m["cognitive_schema_errors"]} | {(m["cognitive_schema_errors"]/m["total_cases"]*100):.1f}% |
| **拒答/短路 (Short-Circuit)** | {m["short_circuit_count"]} | {(m["short_circuit_count"]/m["total_cases"]*100):.1f}% |
| **API 超时/系统故障** | {m["api_timeout_errors"] + m["unexpected_errors"]} | {((m["api_timeout_errors"] + m["unexpected_errors"])/m["total_cases"]*100):.1f}% |

> **Bad Cases 已导出至**: `outputs/bad_cases/`
        """
        print(report)

async def main():
    parser = argparse.ArgumentParser(description="AI Grader Baseline Evaluation Tool")
    parser.add_argument("--data_dir", type=str, default="data/3.20_physics", help="物理测试集根目录")
    parser.add_argument("--concurrency", type=int, default=2, help="API 并发限制")
    args = parser.parse_args()

    evaluator = BaselineEvaluator(concurrency=args.concurrency)
    data_path = Path(args.data_dir)
    
    if not data_path.exists():
        print(f"❌ 错误：找不到数据集目录 {args.data_dir}")
        return

    # 1. 扫描所有题目目录
    question_dirs = [d for d in data_path.iterdir() if d.is_dir() and d.name.startswith("question_")]
    print(f"🔍 发现 {len(question_dirs)} 道题目，准备生成标准 Rubrics...")

    all_tasks = []
    
    for q_dir in question_dirs:
        # 寻找标准答案
        standard_dir = q_dir / "standard"
        ref_files = list(standard_dir.glob("reference.*"))
        if not ref_files:
            continue
            
        # Track 1: 生成 Rubric (标准答案解析)
        print(f"📖 正在解析题目 {q_dir.name} 的标准答案...")
        try:
            ref_bytes = ref_files[0].read_bytes()
            rubric = await evaluator.workflow.generate_rubric_pipeline([(ref_bytes, ref_files[0].name)])
            
            # 2. 扫描该题目下的所有学生作答
            student_dir = q_dir / "students"
            student_files = [f for f in student_dir.iterdir() if f.suffix.lower() in [".jpg", ".jpeg", ".png", ".pdf"]]
            
            for s_file in student_files:
                all_tasks.append(evaluator.evaluate_single_case(q_dir.name, s_file, rubric))
                
        except Exception as e:
            print(f"⚠️ 题目 {q_dir.name} 的 Rubric 生成失败: {e}")
            continue

    # 3. 并发执行全量跑测
    print(f"🧪 开始全量测试 ({len(all_tasks)} 个学生用例)...")
    await tqdm.gather(*all_tasks, desc="基准测试进度")

    # 4. 输出报告
    evaluator.print_markdown_report()

if __name__ == "__main__":
    asyncio.run(main())
