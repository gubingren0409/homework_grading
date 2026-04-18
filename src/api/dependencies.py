from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import settings
from src.orchestration.workflow import GradingWorkflow
from src.perception.factory import create_perception_engine
from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.skills.service import SkillService

# Shared rate-limiter instance — all routers must import from here.
limiter = Limiter(key_func=get_remote_address)

# Database path from config
DB_PATH = settings.sqlite_db_path if hasattr(settings, 'sqlite_db_path') else "outputs/grading_database.db"

def get_grading_workflow() -> GradingWorkflow:
    """Provides a thread-safe orchestration instance."""
    perception = create_perception_engine()
    cognitive = DeepSeekCognitiveEngine()
    return GradingWorkflow(perception, cognitive, skill_service=SkillService(db_path=DB_PATH))

def get_db_path() -> str:
    """Simple dependency for global DB access."""
    return DB_PATH
