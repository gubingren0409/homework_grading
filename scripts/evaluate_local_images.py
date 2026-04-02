import asyncio
import argparse
import logging
import os
import json
from pathlib import Path
from typing import List

from src.perception.factory import create_perception_engine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.workflow import GradingWorkflow
from src.core.exceptions import PerceptionShortCircuitError, GradingSystemError


# Configure logging for CLI feedback
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


async def process_batch(input_dir: str, output_dir: str):
    """
    Sequentially processes all images in the input directory.
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 1. Pipeline Assembly (Real Engines)
    logger.info("Initializing Real AI Grading Engines (Qwen-VL + DeepSeek-V3)...")
    perception_engine = create_perception_engine()
    cognitive_agent = DeepSeekCognitiveEngine()
    workflow = GradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=cognitive_agent
    )

    # 2. File Discovery
    extensions = [".jpg", ".jpeg", ".png"]
    image_files = [
        f for f in input_path.iterdir() 
        if f.is_file() and f.suffix.lower() in extensions
    ]

    if not image_files:
        logger.warning(f"No supported images found in {input_dir}")
        return

    logger.info(f"Found {len(image_files)} images. Starting sequential processing...")

    # 3. Main Loop (Strictly Sequential to respect rate limits)
    for idx, img_file in enumerate(image_files, 1):
        logger.info(f"[{idx}/{len(image_files)}] Processing: {img_file.name}")
        
        try:
            # Read bytes
            image_bytes = img_file.read_bytes()

            # Execute Pipeline
            report = await workflow.run_pipeline(image_bytes)

            # Serialization
            output_file = output_path / f"{img_file.stem}_report.json"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(report.model_dump_json(indent=2))

            logger.info(f"SUCCESS: Saved report to {output_file}")

        except PerceptionShortCircuitError as pse:
            logger.error(f"SHORT-CIRCUIT on {img_file.name}: {pse.message}")
        except GradingSystemError as gse:
            logger.error(f"SYSTEM ERROR on {img_file.name}: {str(gse)}")
        except Exception as e:
            logger.exception(f"UNEXPECTED ERROR on {img_file.name}: {str(e)}")
        
        # Optional: Small cooldown to further prevent rate limiting
        await asyncio.sleep(1)

    logger.info("Batch processing completed.")


def main():
    parser = argparse.ArgumentParser(
        description="Batch Evaluate Local Homework Images using Real AI Pipeline."
    )
    parser.add_argument(
        "--input_dir", 
        type=str, 
        default=r"E:\ai批改\测试用例",
        help="Directory containing student homework images."
    )
    parser.add_argument(
        "--output_dir", 
        type=str, 
        default="outputs",
        help="Directory to save the structured JSON evaluation reports."
    )

    args = parser.parse_args()

    # Run the async batch process
    try:
        asyncio.run(process_batch(args.input_dir, args.output_dir))
    except KeyboardInterrupt:
        logger.info("Batch processing interrupted by user.")


if __name__ == "__main__":
    main()
