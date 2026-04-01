import pytest
from PIL import Image
import io
from src.orchestration.workflow import GradingWorkflow
from src.perception.base import BasePerceptionEngine
from src.perception.mock_engine import MockPerceptionEngine
from src.cognitive.mock_agent import MockCognitiveAgent
from src.core.exceptions import PerceptionShortCircuitError
from src.core.config import settings
from src.schemas.perception_ir import PerceptionOutput, LayoutIR
from src.schemas.cognitive_ir import EvaluationReport


def _make_test_image_bytes() -> bytes:
    img = Image.new("RGB", (64, 64), color=(255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


class DirtyPerceptionEngine(BasePerceptionEngine):
    """
    Concrete implementation of BasePerceptionEngine that forces a short-circuit.
    Used to test the orchestrator's circuit-breaker logic.
    """
    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        return PerceptionOutput(
            readability_status="UNREADABLE",
            elements=[],
            global_confidence=0.0,
            trigger_short_circuit=True
        )

    async def extract_layout(
        self,
        image_bytes: bytes,
        *,
        context_type: str,
        target_question_no: str | None = None,
        page_index: int = 0,
    ) -> LayoutIR:
        payload = {
            "context_type": context_type,
            "target_question_no": target_question_no,
            "page_index": page_index,
            "regions": [
                {
                    "target_id": f"dirty_region_{page_index}",
                    "region_type": "answer_region",
                    "bbox": {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0},
                }
            ],
            "warnings": [],
        }
        return LayoutIR.model_validate(payload, context={"image_width": 64, "image_height": 64})


class NoLayoutPerceptionEngine(BasePerceptionEngine):
    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        return PerceptionOutput(
            readability_status="CLEAR",
            elements=[],
            global_confidence=1.0,
            trigger_short_circuit=False,
        )


@pytest.mark.asyncio
async def test_workflow_happy_path(monkeypatch):
    """
    Verifies that valid input flows correctly from Perception to Cognition.
    """
    # 1. Dependency Injection
    workflow = GradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent()
    )
    monkeypatch.setattr(settings, "enable_layout_preprocess", True)

    # 2. Execution
    report = await workflow.run_pipeline([(_make_test_image_bytes(), "fake.jpg")])

    # 3. Assertions
    assert isinstance(report, EvaluationReport)
    assert report.is_fully_correct is False
    assert any(ev.error_type == "CALCULATION" for ev in report.step_evaluations)


@pytest.mark.asyncio
async def test_workflow_circuit_breaker(monkeypatch):
    """
    Verifies that the workflow halts and raises an exception when 
    perception data is marked as unreadable.
    """
    # 1. Inject the "Dirty" engine that simulates unreadable input
    workflow = GradingWorkflow(
        perception_engine=DirtyPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent()
    )
    monkeypatch.setattr(settings, "enable_layout_preprocess", True)

    # 2. Assert circuit-breaker interception
    with pytest.raises(PerceptionShortCircuitError) as exc_info:
        await workflow.run_pipeline([(_make_test_image_bytes(), "garbage.jpg")])

    # 3. Verify exception metadata
    assert exc_info.value.readability_status == "UNREADABLE"
    assert "Workflow halted" in str(exc_info.value)


@pytest.mark.asyncio
async def test_workflow_blocks_when_phase35_disabled(monkeypatch):
    workflow = GradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )
    monkeypatch.setattr(settings, "enable_layout_preprocess", False)

    with pytest.raises(RuntimeError) as exc_info:
        await workflow.run_pipeline([(_make_test_image_bytes(), "fake.jpg")])

    assert "PHASE35_CONTRACT_BLOCK" in str(exc_info.value)


@pytest.mark.asyncio
async def test_workflow_blocks_without_layout_capability(monkeypatch):
    workflow = GradingWorkflow(
        perception_engine=NoLayoutPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )
    monkeypatch.setattr(settings, "enable_layout_preprocess", True)

    with pytest.raises(RuntimeError) as exc_info:
        await workflow.run_pipeline([(_make_test_image_bytes(), "fake.jpg")])

    assert "extract_layout capability" in str(exc_info.value)
