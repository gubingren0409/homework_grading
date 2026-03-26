import asyncio
import argparse
import logging
import sys
import json
from pathlib import Path
from pydantic import ValidationError

from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.workflow import GradingWorkflow
from src.schemas.rubric_ir import TeacherRubric
from src.core.exceptions import GradingSystemError, PerceptionShortCircuitError


# Configure logging for CLI feedback
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


async def run_grading(student_files: list[str], rubric_file: str, output_file: str):
    """
    Main entry point for Track 2: Grading student work against a local rubric.
    """
    output_path = Path(output_file)
    rubric_path = Path(rubric_file)
    
    # 1. Load Rubric
    if not rubric_path.exists():
        logger.error(f"Rubric file not found: {rubric_file}")
        return
    
    try:
        rubric = TeacherRubric.model_validate_json(rubric_path.read_text(encoding="utf-8"))
        logger.info(f"Loaded Rubric: {rubric.question_id}")
    except Exception as e:
        logger.error(f"FAILED TO PARSE RUBRIC: {str(e)}")
        return

    # 2. Load Student Files
    files_data = []
    for p in student_files:
        path = Path(p)
        if not path.exists():
            logger.error(f"Student file not found: {p}")
            return
        files_data.append((path.read_bytes(), path.name))

    # 3. Pipeline Assembly
    logger.info("Initializing Real AI Grading Engines...")
    perception_engine = QwenVLMPerceptionEngine()
    cognitive_agent = DeepSeekCognitiveEngine()
    workflow = GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent
    )

    try:
        logger.info(f"Grading student work ({len(files_data)} file(s)) with AI Pipeline...")
        
        # 4. Execute Grading Pipeline
        report = await workflow.run_pipeline(files_data, rubric=rubric)

        # 5. Persistence
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))

        # 6. Summary Feedback
        logger.info("--------------------------------------------------")
        logger.info(f"SUCCESS: Evaluation complete. Report saved to {output_file}")
        logger.info(f"Total Score Deduction: {report.total_score_deduction}")
        logger.info(f"Pass Status: {'PASS' if report.is_fully_correct else 'FAIL'}")
        
        logger.info(f"Steps Evaluated: {len(report.step_evaluations)}")
        for step in report.step_evaluations:
            status = "CORRECT" if step.is_correct else "ERROR"
            logger.info(f"  [-] {step.reference_element_id}: {status} -> {step.correction_suggestion}")
        logger.info("--------------------------------------------------")

    except PerceptionShortCircuitError as pse:
        logger.error(f"PERCEPTION FAILURE: {pse.message}")
    except GradingSystemError as gse:
        logger.error(f"SYSTEM ERROR: {str(gse)}")
    except ValidationError as ve:
        logger.error(f"VALIDATION ERROR: Evaluation report did not match schema.\n{str(ve)}")
    except Exception as e:
        logger.exception(f"UNEXPECTED ERROR: {str(e)}")


def main():
    parser = argparse.ArgumentParser(
        description="Dual-Track Grading: Grade student homework against a pre-generated rubric."
    )
    parser.add_argument(
        "--student_files", 
        type=str, 
        nargs='+',
        required=True,
        help="Path(s) to student answer images or PDFs."
    )
    parser.add_argument(
        "--rubric_file", 
        type=str, 
        required=True,
        help="Path to the reference JSON rubric."
    )
    parser.add_argument(
        "--output_file", 
        type=str, 
        default="outputs/grading_report.json",
        help="Path to save the evaluation report."
    )

    args = parser.parse_args()

    # Run the async grading loop
    try:
        asyncio.run(run_grading(args.student_files, args.rubric_file, args.output_file))
    except KeyboardInterrupt:
        logger.info("Grading process interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
