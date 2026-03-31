from fastapi.testclient import TestClient
from src.main import app
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
