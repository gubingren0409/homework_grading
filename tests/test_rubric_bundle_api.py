import asyncio
import io

from fastapi.testclient import TestClient
from PIL import Image

from src.api.dependencies import get_db_path
from src.db.client import get_rubric_bundle, init_db
from src.main import app
from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput


client = TestClient(app)


def _make_test_image_bytes() -> bytes:
    image = Image.new("RGB", (32, 32), color=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class _FakePerceptionEngine:
    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        del image_bytes
        return PerceptionOutput(
            readability_status="CLEAR",
            elements=[
                PerceptionNode(
                    element_id="title",
                    content_type="plain_text",
                    raw_content="一、电磁波",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.3, y_max=0.15),
                ),
                PerceptionNode(
                    element_id="q1",
                    content_type="plain_text",
                    raw_content="1．第一题答案",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.2, x_max=0.5, y_max=0.25),
                ),
                PerceptionNode(
                    element_id="q2",
                    content_type="plain_text",
                    raw_content="2．第二题答案",
                    confidence_score=1.0,
                    bbox=BoundingBox(x_min=0.1, y_min=0.3, x_max=0.5, y_max=0.35),
                ),
            ],
            global_confidence=1.0,
        )


def test_rubric_bundle_generate_persists_bundle(tmp_path, monkeypatch):
    db_path = str(tmp_path / "rubric_bundle.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    monkeypatch.setattr("src.api.routers.rubric.create_perception_engine", lambda: _FakePerceptionEngine())

    response = client.post(
        "/api/v1/rubric/bundle/generate",
        files=[("files", ("reference.jpg", _make_test_image_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["question_count"] == 2
    assert payload["bundle_json"]["paper_id"] == payload["paper_id"]
    assert [item["question_id"] for item in payload["bundle_json"]["rubrics"]] == ["一/1", "一/2"]

    saved = asyncio.run(get_rubric_bundle(db_path, payload["bundle_id"]))
    assert saved is not None
    assert payload["paper_id"] == saved["paper_id"]

    app.dependency_overrides.clear()


def test_rubric_bundle_generate_supports_handwritten_reference_mode(tmp_path, monkeypatch):
    db_path = str(tmp_path / "rubric_bundle_handwritten.db")
    asyncio.run(init_db(db_path))
    app.dependency_overrides[get_db_path] = lambda: db_path
    client.app.state.limiter.reset()
    monkeypatch.setattr("src.api.routers.rubric.create_perception_engine", lambda: _FakePerceptionEngine())

    response = client.post(
        "/api/v1/rubric/bundle/generate",
        data={"reference_mode": "handwritten"},
        files=[("files", ("reference.jpg", _make_test_image_bytes(), "image/jpeg"))],
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["question_count"] == 2
    assert [item["question_id"] for item in payload["bundle_json"]["rubrics"]] == ["一/1", "一/2"]
    assert all("【手写标准答案OCR】" in item["correct_answer"] for item in payload["bundle_json"]["rubrics"])

    saved = asyncio.run(get_rubric_bundle(db_path, payload["bundle_id"]))
    assert saved is not None

    app.dependency_overrides.clear()
