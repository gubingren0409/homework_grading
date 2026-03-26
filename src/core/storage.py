"""
Phase 31: Claim Check Pattern - File Storage Layer

Decouples file payload from message broker by implementing reference-based
file passing. Prevents Redis OOM and enables large file processing.

Pattern:
1. API Gateway: Write file to disk → Enqueue path reference
2. Worker: Read path from queue → Load file from disk → Process
3. Cleanup: Delete temp file after completion
"""
import os
import shutil
from pathlib import Path
from typing import Dict, Any, List
import logging

from src.core.config import settings


logger = logging.getLogger(__name__)


def store_uploaded_file(task_id: str, file_bytes: bytes, filename: str) -> str:
    """
    Phase 31: Gateway storage - persist uploaded file to disk.
    
    Args:
        task_id: Business task UUID (used as directory name)
        file_bytes: Raw file bytes
        filename: Original filename
        
    Returns:
        Absolute file path (reference for Celery payload)
        
    Example:
        >>> path = store_uploaded_file("uuid-123", b"data", "test.png")
        >>> # Returns: "/app/data/uploads/uuid-123/test.png"
    """
    # Create task-specific directory
    task_dir = settings.uploads_path / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    
    # Write file to disk
    file_path = task_dir / filename
    file_path.write_bytes(file_bytes)
    
    logger.info(f"[Storage] Stored file: {file_path} ({len(file_bytes)} bytes)")
    return str(file_path.resolve())


def prepare_claim_check_payload(file_paths: List[str]) -> Dict[str, Any]:
    """
    Phase 31: Claim Check Pattern - create reference-only payload.
    
    Args:
        file_paths: List of absolute file paths
        
    Returns:
        Minimal payload with file references (NOT content)
        
    Example:
        >>> payload = prepare_claim_check_payload(["/app/uploads/task/file.png"])
        >>> # Returns: {"file_paths": ["/app/uploads/task/file.png"]}
    """
    return {"file_paths": file_paths}


def retrieve_files(file_paths: List[str]) -> List[tuple[bytes, str]]:
    """
    Phase 31: Worker retrieval - load files from disk using references.
    
    Args:
        file_paths: List of absolute file paths from claim check payload
        
    Returns:
        List of (file_bytes, filename) tuples for workflow processing
        
    Raises:
        FileNotFoundError: If referenced file doesn't exist
    """
    files_data = []
    
    for file_path in file_paths:
        path = Path(file_path)
        
        if not path.exists():
            logger.error(f"[Storage] File not found: {file_path}")
            raise FileNotFoundError(f"Claim check failed: {file_path}")
        
        file_bytes = path.read_bytes()
        filename = path.name
        
        logger.info(f"[Storage] Retrieved file: {file_path} ({len(file_bytes)} bytes)")
        files_data.append((file_bytes, filename))
    
    return files_data


def cleanup_task_files(task_id: str) -> None:
    """
    Phase 31: Garbage collection - delete temp files after task completion.
    
    Args:
        task_id: Business task UUID
        
    Safety:
        - Only deletes files within uploads directory
        - Logs warnings if directory doesn't exist
    """
    task_dir = settings.uploads_path / task_id
    
    if not task_dir.exists():
        logger.warning(f"[Storage] Task directory not found for cleanup: {task_dir}")
        return
    
    try:
        shutil.rmtree(task_dir)
        logger.info(f"[Storage] Cleaned up task directory: {task_dir}")
    except Exception as e:
        logger.error(f"[Storage] Failed to cleanup {task_dir}: {e}")
