from src.core.config import settings
from src.orchestration.workflow import GradingWorkflow
from src.perception.engines.qwen_engine import QwenVLMPerceptionEngine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine

# Database path from config
DB_PATH = settings.sqlite_db_path if hasattr(settings, 'sqlite_db_path') else "outputs/grading_database.db"

def get_grading_workflow() -> GradingWorkflow:
    """Provides a thread-safe orchestration instance."""
    perception = QwenVLMPerceptionEngine()
    cognitive = DeepSeekCognitiveEngine()
    return GradingWorkflow(perception, cognitive)

def get_db_path() -> str:
    """Simple dependency for global DB access."""
    return DB_PATH
