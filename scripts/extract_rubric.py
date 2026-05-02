import asyncio
import argparse
import logging
import re
import sys
from pathlib import Path
from pydantic import ValidationError

# Allow direct script execution via `python scripts\extract_rubric.py`
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.perception.factory import create_perception_engine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.core.circuit_breaker import CircuitBreakerOpenError
from src.core.exceptions import GradingSystemError
from src.schemas.perception_ir import PerceptionOutput
from src.utils.file_parsers import process_multiple_files


# Configure logging for CLI feedback
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def _extract_retry_seconds(message: str) -> float | None:
    match = re.search(r"Retry in ([0-9]+(?:\.[0-9]+)?)s", message)
    if not match:
        return None
    return float(match.group(1))


async def _perceive_reference_page(
    perception_engine,
    page_bytes: bytes,
    *,
    page_index: int,
    total_pages: int,
    max_attempts: int,
    cooldown_seconds: float,
) -> PerceptionOutput:
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(
                "Perceiving reference page %s/%s (attempt %s/%s)...",
                page_index,
                total_pages,
                attempt,
                max_attempts,
            )
            return await perception_engine.process_image(page_bytes)
        except CircuitBreakerOpenError as exc:
            last_error = exc
            wait_seconds = _extract_retry_seconds(str(exc)) or cooldown_seconds
            wait_seconds = max(wait_seconds + 2.0, cooldown_seconds)
            logger.warning(
                "Qwen breaker OPEN while perceiving reference page %s/%s; waiting %.1fs before retry.",
                page_index,
                total_pages,
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
        except GradingSystemError as exc:
            last_error = exc
            message = str(exc)
            wait_seconds = _extract_retry_seconds(message)
            if wait_seconds is not None or "Persistent network instability for Qwen" in message:
                wait_seconds = max((wait_seconds or cooldown_seconds) + 2.0, cooldown_seconds)
                logger.warning(
                    "Recoverable Qwen perception failure on reference page %s/%s: %s. Waiting %.1fs before retry.",
                    page_index,
                    total_pages,
                    message,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                continue
            raise
    assert last_error is not None
    raise last_error


async def run_extraction(
    input_files: list[str],
    output_file: str,
    *,
    page_retry_attempts: int,
    cooldown_seconds: float,
):
    """
    Orchestrates the extraction of a TeacherRubric from multiple physical files.
    Perception is processed sequentially to avoid tripping the global Qwen
    circuit breaker on large multi-page reference answers.
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
    try:
        logger.info(f"Processing {len(files_data)} file(s) for rubric extraction...")
        image_bytes_list = await process_multiple_files(files_data)

        # 3. Execute sequential perception + merged rubric generation
        perception_outputs = []
        for idx, page_bytes in enumerate(image_bytes_list, start=1):
            perception_outputs.append(
                await _perceive_reference_page(
                    perception_engine,
                    page_bytes,
                    page_index=idx,
                    total_pages=len(image_bytes_list),
                    max_attempts=page_retry_attempts,
                    cooldown_seconds=cooldown_seconds,
                )
            )

        all_elements = []
        for page_idx, ir_data in enumerate(perception_outputs):
            for elem in ir_data.elements:
                elem.element_id = f"p{page_idx}_{elem.element_id}"
                all_elements.append(elem)

        global_conf = (
            sum(p.global_confidence for p in perception_outputs) / len(perception_outputs)
            if perception_outputs
            else 0.0
        )
        merged_ir = PerceptionOutput(
            readability_status="CLEAR",
            elements=all_elements,
            global_confidence=global_conf,
            is_blank=all(len(p.elements) == 0 for p in perception_outputs),
            trigger_short_circuit=False,
        )
        rubric = await cognitive_agent.generate_rubric(merged_ir)

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
    parser.add_argument(
        "--page_retry_attempts",
        type=int,
        default=3,
        help="Max attempts per normalized reference page when Qwen perception hits transient failures or circuit open.",
    )
    parser.add_argument(
        "--cooldown_seconds",
        type=float,
        default=65.0,
        help="Cooldown used when Qwen circuit breaker opens during reference perception.",
    )

    args = parser.parse_args()

    # Run the async extraction process
    try:
        asyncio.run(
            run_extraction(
                args.input_files,
                args.output_file,
                page_retry_attempts=args.page_retry_attempts,
                cooldown_seconds=args.cooldown_seconds,
            )
        )
    except KeyboardInterrupt:
        logger.info("Extraction interrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
