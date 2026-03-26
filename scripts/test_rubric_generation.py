import asyncio
import logging
import sys
from pathlib import Path
from typing import List

from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.orchestration.workflow import GradingWorkflow
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.schemas.perception_ir import PerceptionOutput

# Configure logging to show info in console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Target questions that previously failed
FAILING_QUESTIONS = ["question_05", "question_10", "question_12", "question_13", "question_14"]
DATA_ROOT = Path("data/3.20_physics")

async def test_rubric_extraction():
    """
    Focused test for Track 1: Rubric extraction for previously failed questions.
    """
    perception_engine = QwenVLMPerceptionEngine()
    # We only need perception for this test, but workflow usually needs both
    cognitive_agent = DeepSeekCognitiveEngine() 
    workflow = GradingWorkflow(perception_engine, cognitive_agent)

    print("\n" + "="*80)
    print("🚀 STARTING TARGETED RUBRIC EXTRACTION RETEST")
    print("="*80 + "\n")

    for q_id in FAILING_QUESTIONS:
        q_dir = DATA_ROOT / q_id
        ref_files = list((q_dir / "standard").glob("reference.*"))
        
        if not ref_files:
            logger.warning(f"No reference image found for {q_id}")
            continue
            
        ref_path = ref_files[0]
        print(f"🔍 Testing {q_id} | File: {ref_path.name}")
        
        try:
            # 1. Direct Perception Test
            image_bytes = ref_path.read_bytes()
            ir_data: PerceptionOutput = await perception_engine.process_image(image_bytes)
            
            print(f"✅ Perception Success for {q_id}!")
            
            # 2. Highlight specialized nodes
            found_special = False
            for elem in ir_data.elements:
                if elem.content_type in ["image_diagram", "table", "image"]:
                    found_special = True
                    color_code = "\033[94m" if elem.content_type == "table" else "\033[92m"
                    reset_code = "\033[0m"
                    print(f"\n  --- {color_code}SPECIAL NODE DETECTED: {elem.content_type}{reset_code} ---")
                    print(f"  [ID]: {elem.element_id}")
                    print(f"  [CONTENT]:\n{elem.raw_content}")
                    print("-" * 40)
            
            if not found_special:
                print("  (No image_diagram or table nodes found in this response)")

            # 3. Full Pipeline Test (Track 1)
            print(f"⚙️ Running full Rubric Pipeline for {q_id}...")
            rubric = await workflow.generate_rubric_pipeline([(image_bytes, ref_path.name)])
            print(f"✨ Rubric Generated Successfully! Points found: {len(rubric.grading_points)}")
            
        except Exception as e:
            print(f"❌ FAILED {q_id}: {str(e)}")
        
        print("\n" + "-"*60 + "\n")

    print("="*80)
    print("🎯 RETEST COMPLETE")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(test_rubric_extraction())
