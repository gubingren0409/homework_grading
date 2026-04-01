"""
Phase 32: Storage Adapter Pattern - Distributed Storage Abstraction

Solves horizontal scaling trap where API Gateway and Workers run on different
nodes with isolated filesystems. Provides unified interface for local (dev)
and S3-compatible (production) storage backends.

Architecture:
- BaseStorage: Abstract interface (upload, download, cleanup)
- LocalStorage: Direct filesystem access (single-node dev/testing)
- S3Storage: AWS S3/MinIO client (distributed production)
- Storage Factory: Environment-based backend selection
"""
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Tuple, Dict, Any, BinaryIO
from urllib.parse import urlparse, unquote
import logging

from src.core.config import settings


logger = logging.getLogger(__name__)


class BaseStorage(ABC):
    """
    Abstract storage interface for Claim Check Pattern.
    
    All implementations must support:
    - store_file(): Write file to storage backend
    - retrieve_files(): Read files from storage backend
    - cleanup_task(): Delete all files for a task
    - prepare_payload(): Create reference payload for message queue
    """
    
    @abstractmethod
    def store_file(self, task_id: str, file_bytes: bytes, filename: str) -> str:
        """
        Store file to backend and return reference URI.
        
        Returns:
            Storage reference (e.g., "file:///path" or "s3://bucket/key")
        """
        pass

    def store_fileobj(self, task_id: str, file_obj: BinaryIO, filename: str) -> str:
        """
        Stream-oriented upload path. Default implementation keeps backward
        compatibility by reading bytes and delegating to store_file.
        """
        file_obj.seek(0)
        return self.store_file(task_id, file_obj.read(), filename)
    
    @abstractmethod
    def retrieve_files(self, file_refs: List[str]) -> List[Tuple[bytes, str]]:
        """
        Load files from backend using references.
        
        Returns:
            List of (file_bytes, filename) tuples
        """
        pass
    
    @abstractmethod
    def cleanup_task(self, task_id: str) -> None:
        """Delete all files associated with a task."""
        pass
    
    def prepare_payload(self, file_refs: List[str]) -> Dict[str, Any]:
        """
        Create Claim Check payload (reference-only).
        
        This is backend-agnostic and uses URI references.
        """
        return {"file_refs": file_refs}


class LocalStorage(BaseStorage):
    """
    Local filesystem storage for single-node deployment.
    
    Used for:
    - Development without Docker/S3
    - Testing with local file assertions
    - Single-node deployments with shared NFS/EFS mount
    
    Storage path: {uploads_dir}/{task_id}/filename
    URI format: file:///absolute/path/to/file
    """
    
    def __init__(self, base_path: Path = None):
        """
        Args:
            base_path: Root directory for uploads (defaults to settings.uploads_path)
        """
        self.base_path = base_path or settings.uploads_path
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"[LocalStorage] Initialized with base_path: {self.base_path}")
    
    def store_file(self, task_id: str, file_bytes: bytes, filename: str) -> str:
        """Store file to local filesystem."""
        task_dir = self.base_path / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        
        file_path = task_dir / filename
        file_path.write_bytes(file_bytes)
        
        # Return file:// URI for consistency with S3Storage
        uri = file_path.as_uri()
        logger.info(f"[LocalStorage] Stored: {uri} ({len(file_bytes)} bytes)")
        return uri

    def store_fileobj(self, task_id: str, file_obj: BinaryIO, filename: str) -> str:
        """Store stream to local filesystem without materializing all bytes in memory."""
        task_dir = self.base_path / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        file_path = task_dir / filename
        file_obj.seek(0)
        with file_path.open("wb") as out:
            shutil.copyfileobj(file_obj, out)

        uri = file_path.as_uri()
        logger.info(f"[LocalStorage] Stored stream: {uri}")
        return uri
    
    def retrieve_files(self, file_refs: List[str]) -> List[Tuple[bytes, str]]:
        """Load files from local filesystem."""
        files_data = []
        
        for file_ref in file_refs:
            # Parse file:// URI to local path
            parsed = urlparse(file_ref)
            if parsed.scheme != 'file':
                raise ValueError(f"LocalStorage expects file:// URIs, got: {file_ref}")
            
            # Fix Windows path: file:///C:/path → C:/path (remove leading /)
            path_str = parsed.path
            if path_str.startswith('/') and len(path_str) > 2 and path_str[2] == ':':
                path_str = path_str[1:]  # Remove leading / for Windows absolute paths
            # Decode URL-encoded characters (e.g. Chinese path segments).
            path_str = unquote(path_str)
            
            file_path = Path(path_str)
            
            if not file_path.exists():
                logger.error(f"[LocalStorage] File not found: {file_path}")
                raise FileNotFoundError(f"Claim check failed: {file_ref}")
            
            file_bytes = file_path.read_bytes()
            filename = file_path.name
            
            logger.info(f"[LocalStorage] Retrieved: {file_ref} ({len(file_bytes)} bytes)")
            files_data.append((file_bytes, filename))
        
        return files_data
    
    def cleanup_task(self, task_id: str) -> None:
        """Delete task directory from local filesystem."""
        task_dir = self.base_path / task_id
        
        if not task_dir.exists():
            logger.warning(f"[LocalStorage] Task directory not found: {task_dir}")
            return
        
        try:
            shutil.rmtree(task_dir)
            logger.info(f"[LocalStorage] Cleaned up: {task_dir}")
        except Exception as e:
            logger.error(f"[LocalStorage] Cleanup failed for {task_dir}: {e}")


class S3Storage(BaseStorage):
    """
    S3-compatible storage for distributed deployment.
    
    Used for:
    - Production multi-node deployments (AWS S3, MinIO, R2, B2)
    - Kubernetes with independent API/Worker pods
    - High-availability setups with ephemeral nodes
    
    URI format: s3://{bucket}/{task_id}/{filename}
    
    Configuration:
        S3_ENDPOINT_URL: Custom endpoint (e.g., http://minio:9000)
        S3_BUCKET: Bucket name
        AWS_ACCESS_KEY_ID: S3 access key
        AWS_SECRET_ACCESS_KEY: S3 secret key
    """
    
    def __init__(self, bucket: str = None, endpoint_url: str = None):
        """
        Args:
            bucket: S3 bucket name (defaults to settings.s3_bucket)
            endpoint_url: S3 endpoint URL (defaults to settings.s3_endpoint_url)
        """
        # Lazy import boto3 to avoid dependency error when using LocalStorage
        import sys
        if 'boto3' not in sys.modules:
            try:
                import boto3 as _boto3
            except ImportError:
                raise ImportError(
                    "S3Storage requires boto3. Install with: pip install boto3"
                )
        else:
            _boto3 = sys.modules['boto3']
        
        self._boto3 = _boto3
        self.bucket = bucket or settings.s3_bucket
        self.endpoint_url = endpoint_url or settings.s3_endpoint_url
        
        # Initialize S3 client
        self.s3_client = self._boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )
        
        logger.info(f"[S3Storage] Initialized with bucket: {self.bucket}")
    
    def store_file(self, task_id: str, file_bytes: bytes, filename: str) -> str:
        """Upload file to S3."""
        object_key = f"{task_id}/{filename}"
        
        self.s3_client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=file_bytes,
        )
        
        # Return s3:// URI
        uri = f"s3://{self.bucket}/{object_key}"
        logger.info(f"[S3Storage] Stored: {uri} ({len(file_bytes)} bytes)")
        return uri

    def store_fileobj(self, task_id: str, file_obj: BinaryIO, filename: str) -> str:
        """Upload stream to S3-compatible backend."""
        object_key = f"{task_id}/{filename}"
        file_obj.seek(0)
        self.s3_client.upload_fileobj(file_obj, self.bucket, object_key)
        uri = f"s3://{self.bucket}/{object_key}"
        logger.info(f"[S3Storage] Stored stream: {uri}")
        return uri
    
    def retrieve_files(self, file_refs: List[str]) -> List[Tuple[bytes, str]]:
        """Download files from S3."""
        files_data = []
        
        for file_ref in file_refs:
            # Parse s3:// URI
            parsed = urlparse(file_ref)
            if parsed.scheme != 's3':
                raise ValueError(f"S3Storage expects s3:// URIs, got: {file_ref}")
            
            bucket = parsed.netloc
            object_key = parsed.path.lstrip('/')
            filename = Path(object_key).name
            
            # Download from S3
            response = self.s3_client.get_object(Bucket=bucket, Key=object_key)
            file_bytes = response['Body'].read()
            
            logger.info(f"[S3Storage] Retrieved: {file_ref} ({len(file_bytes)} bytes)")
            files_data.append((file_bytes, filename))
        
        return files_data
    
    def cleanup_task(self, task_id: str) -> None:
        """Delete all objects with task_id prefix."""
        prefix = f"{task_id}/"
        
        # List all objects with prefix
        response = self.s3_client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
        
        if 'Contents' not in response:
            logger.warning(f"[S3Storage] No objects found for task: {task_id}")
            return
        
        # Delete all objects
        objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]
        
        self.s3_client.delete_objects(
            Bucket=self.bucket,
            Delete={'Objects': objects_to_delete}
        )
        
        logger.info(f"[S3Storage] Cleaned up {len(objects_to_delete)} objects for task: {task_id}")


class StorageFactory:
    """
    Factory for creating storage backend instances.
    
    Backend selection via environment variable:
        STORAGE_BACKEND=local  → LocalStorage (default)
        STORAGE_BACKEND=s3     → S3Storage
    
    Example:
        >>> storage = StorageFactory.create()
        >>> uri = storage.store_file("task-123", b"data", "file.png")
    """
    
    @staticmethod
    def create() -> BaseStorage:
        """
        Create storage backend based on STORAGE_BACKEND environment variable.
        
        Returns:
            Storage backend instance (LocalStorage or S3Storage)
        """
        backend = settings.storage_backend.lower()
        
        if backend == 's3':
            logger.info("[StorageFactory] Creating S3Storage backend")
            return S3Storage()
        elif backend == 'local':
            logger.info("[StorageFactory] Creating LocalStorage backend")
            return LocalStorage()
        else:
            raise ValueError(
                f"Unknown storage backend: {backend}. "
                f"Valid options: 'local', 's3'"
            )


# Global storage instance (singleton pattern)
storage: BaseStorage = StorageFactory.create()
