"""
Phase 30: Robust JSON Serialization Utilities

Ensures all Celery payloads are JSON-compatible without destructive str() coercion.
Handles Path objects, Pydantic models, and other non-primitive types gracefully.
"""
from pathlib import Path
from typing import Any, Dict, List, Union
import json


class CeleryJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder for Celery task arguments.
    
    Handles:
    - pathlib.Path -> str
    - Pydantic models -> dict
    - bytes -> list[int]
    """
    def default(self, obj: Any) -> Any:
        # Handle Path objects
        if isinstance(obj, Path):
            return str(obj)
        
        # Handle Pydantic models
        if hasattr(obj, 'model_dump'):
            return obj.model_dump(mode='json')
        
        # Handle bytes (should already be converted by API, but safety fallback)
        if isinstance(obj, bytes):
            return list(obj)
        
        # Delegate to default JSON encoder
        return super().default(obj)


def serialize_for_celery(payload: Any) -> Any:
    """
    Convert arbitrary Python objects to JSON-serializable primitives.
    
    Args:
        payload: Any Python object
        
    Returns:
        JSON-compatible primitive (str, int, float, list, dict, None)
        
    Raises:
        TypeError: If object cannot be serialized
        
    Examples:
        >>> serialize_for_celery(Path("/tmp/test.txt"))
        '/tmp/test.txt'
        
        >>> serialize_for_celery({"path": Path("/tmp"), "data": b"\\x01\\x02"})
        {'path': '/tmp', 'data': [1, 2]}
    """
    return json.loads(json.dumps(payload, cls=CeleryJSONEncoder))


def prepare_file_payload(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Phase 30: Safe file payload preparation for Celery transport.
    
    Args:
        file_bytes: Raw uploaded file bytes
        filename: Original filename (may be Path object)
        
    Returns:
        Dict with 'content' (int list) and 'filename' (str)
    """
    return {
        "content": list(file_bytes),  # bytes -> list[int]
        "filename": str(filename),    # Ensure string (handles Path)
    }
