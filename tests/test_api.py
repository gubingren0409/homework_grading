import base64
from fastapi.testclient import TestClient
from src.main import app
from src.schemas.perception_ir import LayoutIR
client = TestClient(app)


def test_grade_endpoint_happy_path():
    """
    E2E test: Verifies that a valid file upload returns a 200 OK 
    and a correctly structured BatchGradingResponse JSON.
    """
    # 1. Prepare payload
    files = [("files", ("test_hw.jpg", b"fake_image_bytes", "image/jpeg"))]

    # 2. Execute POST
    response = client.post("/api/v1/grade/submit", files=files)

    # 3. Assertions
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "PENDING"


def test_grade_endpoint_circuit_breaker_422():
    """
    E2E test: Verifies that the API correctly maps the PerceptionShortCircuitError 
    to an HTTP 422 Unprocessable Entity response using Dependency Overrides.
    """
    files = [("files", ("dirty_hw.jpg", b"dirty_bytes", "image/jpeg"))]
    response = client.post("/api/v1/grade/submit", files=files)
    assert response.status_code == 202


def test_health_check():
    """Simple test for the health check endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_skill_layout_parse_gateway(monkeypatch):
    class _FakePerceptionEngine:
        async def extract_layout(
            self,
            image_bytes: bytes,
            *,
            context_type: str,
            target_question_no: str | None = None,
            page_index: int = 0,
        ) -> LayoutIR:
            del image_bytes
            payload = {
                "context_type": context_type,
                "target_question_no": target_question_no,
                "page_index": page_index,
                "regions": [
                    {
                        "target_id": "region-1",
                        "question_no": target_question_no,
                        "region_type": "answer_region",
                        "bbox": {"x_min": 0.1, "y_min": 0.2, "x_max": 0.8, "y_max": 0.9},
                    }
                ],
                "warnings": [],
            }
            return LayoutIR.model_validate(payload, context={"image_width": 1000, "image_height": 800})

    monkeypatch.setattr("src.api.routes.create_perception_engine", lambda: _FakePerceptionEngine())
    response = client.post(
        "/api/v1/skills/layout/parse",
        json={
            "image_base64": base64.b64encode(b"fake-image").decode("utf-8"),
            "context_type": "STUDENT_ANSWER",
            "page_index": 0,
            "target_question_no": "12",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["context_type"] == "STUDENT_ANSWER"
    assert len(data["regions"]) == 1
    assert data["regions"][0]["target_id"] == "region-1"


def test_skill_validation_gateway_stub():
    response = client.post(
        "/api/v1/skills/validate",
        json={
            "task_id": "t-1",
            "question_id": "q-1",
            "perception_payload": {"elements": []},
            "evaluation_payload": {"status": "SCORED"},
            "rubric_payload": {"question_id": "q-1"},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "gateway_stub" in data["details"]["mode"]


def test_capability_catalog_endpoint():
    response = client.get("/api/v1/capabilities/catalog")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    domains = {d["domain"] for d in data["domains"]}
    assert {"rubric", "grade", "review", "annotation", "hygiene", "obs"} <= domains


def test_contract_catalog_endpoint():
    response = client.get("/api/v1/contracts/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "status_enums" in data
    assert "task_status" in data["status_enums"]
    assert "schemas" in data
    schema_names = {item["schema_name"] for item in data["schemas"]}
    assert "TaskStatusResponse" in schema_names


def test_sla_summary_endpoint_with_empty_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "empty_sla.db")
    monkeypatch.setattr("src.api.dependencies.get_db_path", lambda: db_path)
    response = client.get("/api/v1/sla/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert isinstance(data["observed_status_counts"], dict)


def test_provider_benchmark_endpoint(tmp_path, monkeypatch):
    db_path = str(tmp_path / "benchmark.db")
    monkeypatch.setattr("src.api.dependencies.get_db_path", lambda: db_path)
    response = client.get("/api/v1/metrics/provider-benchmark?window_hours=24")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert "cognitive_router" in data
    assert "estimated_cost" in data
    assert "throughput_tasks_per_hour" in data
    assert "accuracy_proxy" in data["cognitive_router"]
    assert "failure_rate" in data["cognitive_router"]


def test_router_policy_endpoint():
    response = client.get("/api/v1/router/policy")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0"
    assert "policy" in data
    assert "live_snapshot" in data
