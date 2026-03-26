import pytest
from pathlib import Path


@pytest.fixture(scope="session")
def mock_fixtures_dir() -> Path:
    """Fixture to dynamically resolve and return the absolute path to tests/fixtures."""
    base_dir = Path(__file__).resolve().parent / "fixtures"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


@pytest.fixture
def mock_perception_output_json(mock_fixtures_dir: Path) -> str:
    """Fixture to read and return the standard JSON input stream from math_integral_error.json."""
    json_path = mock_fixtures_dir / "mock_jsons" / "math_integral_error.json"
    if not json_path.exists():
        raise FileNotFoundError(f"Mock JSON file not found at: {json_path}")
    return json_path.read_text(encoding="utf-8")
