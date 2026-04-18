import io

import pytest
from PIL import Image

from src.cognitive.mock_agent import MockCognitiveAgent
from src.core.exceptions import PerceptionShortCircuitError
from src.orchestration.workflow import GradingWorkflow
from src.perception.base import BasePerceptionEngine
from src.perception.mock_engine import MockPerceptionEngine
from src.schemas.cognitive_ir import EvaluationReport
from src.schemas.perception_ir import PerceptionOutput


def _make_test_image_bytes() -> bytes:
    img = Image.new("RGB", (64, 64), color=(255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    return buffer.getvalue()


class DirtyPerceptionEngine(BasePerceptionEngine):
    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        del image_bytes
        return PerceptionOutput(
            readability_status="UNREADABLE",
            elements=[],
            global_confidence=0.0,
            trigger_short_circuit=True,
        )


@pytest.mark.asyncio
async def test_workflow_happy_path():
    workflow = GradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )

    report = await workflow.run_pipeline([(_make_test_image_bytes(), "fake.jpg")])

    assert isinstance(report, EvaluationReport)
    assert report.is_fully_correct is False
    assert any(ev.error_type == "CALCULATION" for ev in report.step_evaluations)


@pytest.mark.asyncio
async def test_workflow_circuit_breaker():
    workflow = GradingWorkflow(
        perception_engine=DirtyPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )

    with pytest.raises(PerceptionShortCircuitError) as exc_info:
        await workflow.run_pipeline([(_make_test_image_bytes(), "garbage.jpg")])

    assert exc_info.value.readability_status == "UNREADABLE"
    assert "Workflow halted" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_pipeline_with_snapshots_returns_phase34_shape():
    workflow = GradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )

    report, perception_snapshot, cognitive_snapshot = await workflow.run_pipeline_with_snapshots(
        [(_make_test_image_bytes(), "fake.jpg")]
    )

    assert isinstance(report, EvaluationReport)
    assert isinstance(perception_snapshot, dict)
    assert isinstance(cognitive_snapshot, dict)
    assert "elements" in perception_snapshot
    assert "regions" not in perception_snapshot
    assert "step_evaluations" in cognitive_snapshot


@pytest.mark.asyncio
async def test_generate_rubric_pipeline_happy_path():
    workflow = GradingWorkflow(
        perception_engine=MockPerceptionEngine(),
        cognitive_agent=MockCognitiveAgent(),
    )
    rubric = await workflow.generate_rubric_pipeline([(_make_test_image_bytes(), "reference.jpg")])
    assert rubric.question_id
    assert len(rubric.grading_points) >= 1
