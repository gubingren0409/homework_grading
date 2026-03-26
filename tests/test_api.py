from fastapi.testclient import TestClient
from src.main import app
from src.api.dependencies import get_grading_workflow
from src.orchestration.workflow import GradingWorkflow
from src.perception.base import BasePerceptionEngine
from src.cognitive.base import BaseCognitiveAgent
from src.cognitive.mock_agent import MockCognitiveAgent
from src.schemas.perception_ir import PerceptionOutput
from src.schemas.rubric_ir import TeacherRubric
from src.schemas.cognitive_ir import EvaluationReport

client = TestClient(app)


def test_grade_endpoint_happy_path():
    """
    E2E test: Verifies that a valid file upload returns a 200 OK 
    and a correctly structured BatchGradingResponse JSON.
    """
    # 1. Prepare payload
    files = {"file": ("test_hw.jpg", b"fake_image_bytes", "image/jpeg")}

    # 2. Execute POST
    response = client.post("/api/v1/grade/", files=files)

    # 3. Assertions
    assert response.status_code == 200
    data = response.json()
    
    # Contract validation: Must be wrapped in a 'reports' key
    assert "reports" in data
    assert isinstance(data["reports"], list)
    assert len(data["reports"]) > 0
    
    # Internal content validation (based on Mock Engine)
    report = data["reports"][0]
    assert report["is_fully_correct"] is False
    assert report["step_evaluations"][0]["error_type"] == "CALCULATION"


def test_grade_endpoint_circuit_breaker_422():
    """
    E2E test: Verifies that the API correctly maps the PerceptionShortCircuitError 
    to an HTTP 422 Unprocessable Entity response using Dependency Overrides.
    """
    # 1. Internal Mock setup for the failure case
    class DirtyPerceptionEngine(BasePerceptionEngine):
        async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
            return PerceptionOutput(
                readability_status="UNREADABLE",
                elements=[],
                global_confidence=0.0,
                trigger_short_circuit=True
            )

    def override_get_workflow():
        return GradingWorkflow(
            perception_engine=DirtyPerceptionEngine(),
            cognitive_agent=MockCognitiveAgent()
        )

    # 2. Hijack FastAPI's dependency injection container
    app.dependency_overrides[get_grading_workflow] = override_get_workflow

    try:
        # 3. Execute POST with same payload
        files = {"file": ("dirty_hw.jpg", b"dirty_bytes", "image/jpeg")}
        response = client.post("/api/v1/grade/", files=files)

        # 4. Assertions for HTTP mapping
        assert response.status_code == 422
        error_data = response.json()
        assert error_data["error"] == "PerceptionShortCircuit"
        assert error_data["readability_status"] == "UNREADABLE"
        assert "suggestion" in error_data
    
    finally:
        # 5. Clean up environment
        app.dependency_overrides.clear()


def test_health_check():
    """Simple test for the health check endpoint."""
    response = client.get("/api/v1/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
