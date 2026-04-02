import asyncio
import argparse
import logging
import sys
from pathlib import Path
from pydantic import ValidationError

from src.perception.factory import create_perception_engine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.workflow import GradingWorkflow
from src.core.exceptions import GradingSystemError


# Configure logging for CLI feedback
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


async def run_extraction(input_files: list[str], output_file: str):
    """
    Orchestrates the extraction of a TeacherRubric from multiple physical files.
    """
    output_path = Path(output_file)
    
    # 1. File Loading & Validation
    files_data = []
    for p in input_files:
        path = Path(p)
        if not path.exists():
            logger.error(f"Input file not found: {p}")
            return
        files_data.append((path.read_bytes(), path.name))

    # 2. Pipeline Assembly (Real Engines)
    logger.info("Initializing Real AI Engines for Multi-File Rubric Extraction...")
    perception_engine = create_perception_engine()
    cognitive_agent = DeepSeekCognitiveEngine()
    workflow = GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent
    )

    try:
        logger.info(f"Processing {len(files_data)} file(s) for rubric extraction...")

        # 3. Execute Rubric Generation Pipeline
        rubric = await workflow.generate_rubric_pipeline(files_data)

        # 4. Persistence
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(rubric.model_dump_json(indent=2))

        logger.info("--------------------------------------------------")
        logger.info(f"SUCCESS: Rubric extracted and saved to {output_file}")
        logger.info(f"Question ID: {rubric.question_id}")
        logger.info(f"Grading Points Found: {len(rubric.grading_points)}")
        for i, pt in enumerate(rubric.grading_points, 1):
            logger.info(f"  [{i}] {pt.description} ({pt.score} pts)")
        logger.info("--------------------------------------------------")

    except GradingSystemError as gse:
        logger.error(f"SYSTEM ERROR: {str(gse)}")
    except ValidationError as ve:
        logger.error(f"VALIDATION ERROR: AI output did not match TeacherRubric schema.\n{str(ve)}")
    except Exception as e:
        logger.exception(f"UNEXPECTED ERROR: {str(e)}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract a structured TeacherRubric from model answer file(s)."
    )
    parser.add_argument(
        "--input_files", 
        type=str, 
        nargs='+',
        required=True,
        help="Path(s) to the model answer images or PDFs."
    )
    parser.add_argument(
        "--output_file", 
        type=str, 
        default="outputs/reference_rubric.json",
        help="Path to save the generated JSON rubric."
    )

    args = parser.parse_args()

    # Run the async extraction process
    try:
        asyncio.run(run_extraction(args.input_files, args.output_file))
    except KeyboardInterrupt:
        logger.info("Extraction interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
