"""
Phase 32: Storage Adapter Pattern - Integration Tests

Tests for LocalStorage, S3Storage (mocked), and storage factory.
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from src.core.storage_adapter import (
    BaseStorage,
    LocalStorage,
    S3Storage,
    StorageFactory,
)


@pytest.fixture
def temp_storage_dir(tmp_path):
    """Temporary directory for LocalStorage tests."""
    return tmp_path / "uploads"


@pytest.fixture
def local_storage(temp_storage_dir):
    """LocalStorage instance with temp directory."""
    return LocalStorage(base_path=temp_storage_dir)


def test_local_storage_roundtrip(local_storage, temp_storage_dir):
    """
    Test: LocalStorage can store and retrieve files.
    """
    task_id = "test-task-123"
    file_bytes = b"Hello, world!"
    filename = "test.txt"
    
    # Store file
    uri = local_storage.store_file(task_id, file_bytes, filename)
    
    # Verify URI format
    assert uri.startswith("file:///")
    assert filename in uri
    
    # Verify file exists on disk
    expected_path = temp_storage_dir / task_id / filename
    assert expected_path.exists()
    assert expected_path.read_bytes() == file_bytes
    
    # Retrieve file via URI
    retrieved_files = local_storage.retrieve_files([uri])
    assert len(retrieved_files) == 1
    assert retrieved_files[0] == (file_bytes, filename)


def test_local_storage_store_fileobj(local_storage, temp_storage_dir):
    """
    Test: LocalStorage can store stream objects directly.
    """
    import io

    task_id = "stream-task-123"
    payload = io.BytesIO(b"stream-bytes")
    uri = local_storage.store_fileobj(task_id, payload, "stream.txt")
    assert uri.startswith("file:///")
    expected_path = temp_storage_dir / task_id / "stream.txt"
    assert expected_path.exists()
    assert expected_path.read_bytes() == b"stream-bytes"


def test_local_storage_cleanup(local_storage, temp_storage_dir):
    """
    Test: LocalStorage cleanup deletes task directory.
    """
    task_id = "cleanup-test"
    file_bytes = b"data"
    
    # Store file
    local_storage.store_file(task_id, file_bytes, "file1.txt")
    local_storage.store_file(task_id, file_bytes, "file2.txt")
    
    # Verify directory exists
    task_dir = temp_storage_dir / task_id
    assert task_dir.exists()
    assert len(list(task_dir.iterdir())) == 2
    
    # Cleanup
    local_storage.cleanup_task(task_id)
    
    # Verify directory deleted
    assert not task_dir.exists()


def test_local_storage_missing_file(local_storage):
    """
    Test: LocalStorage raises FileNotFoundError for missing files.
    """
    fake_uri = "file:///nonexistent/path/file.txt"
    
    with pytest.raises(FileNotFoundError, match="Claim check failed"):
        local_storage.retrieve_files([fake_uri])


@patch.dict('sys.modules', {'boto3': Mock()})
@patch('src.core.storage_adapter.settings')
def test_s3_storage_store_file(mock_settings):
    """
    Test: S3Storage uploads file to S3 bucket.
    """
    import sys
    mock_boto3 = sys.modules['boto3']
    mock_client = Mock()
    mock_boto3.client.return_value = mock_client
    
    mock_settings.s3_bucket = "test-bucket"
    mock_settings.s3_endpoint_url = None
    mock_settings.aws_access_key_id = "test-key"
    mock_settings.aws_secret_access_key = "test-secret"
    
    storage = S3Storage()
    
    task_id = "s3-task-123"
    file_bytes = b"S3 data"
    filename = "file.txt"
    
    # Store file
    uri = storage.store_file(task_id, file_bytes, filename)
    
    # Verify S3 put_object called
    mock_client.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key=f"{task_id}/{filename}",
        Body=file_bytes,
    )
    
    # Verify URI format
    assert uri == f"s3://test-bucket/{task_id}/{filename}"


@patch.dict('sys.modules', {'boto3': Mock()})
@patch('src.core.storage_adapter.settings')
def test_s3_storage_store_fileobj(mock_settings):
    """
    Test: S3Storage uploads stream object via upload_fileobj.
    """
    import io
    import sys

    mock_boto3 = sys.modules['boto3']
    mock_client = Mock()
    mock_boto3.client.return_value = mock_client

    mock_settings.s3_bucket = "test-bucket"
    mock_settings.s3_endpoint_url = None
    mock_settings.aws_access_key_id = "test-key"
    mock_settings.aws_secret_access_key = "test-secret"

    storage = S3Storage()
    stream = io.BytesIO(b"s3-stream")
    uri = storage.store_fileobj("task-stream", stream, "x.bin")
    mock_client.upload_fileobj.assert_called_once()
    assert uri == "s3://test-bucket/task-stream/x.bin"


@patch.dict('sys.modules', {'boto3': Mock()})
@patch('src.core.storage_adapter.settings')
def test_s3_storage_retrieve_files(mock_settings):
    """
    Test: S3Storage downloads file from S3 bucket.
    """
    import sys
    mock_boto3 = sys.modules['boto3']
    mock_client = Mock()
    mock_boto3.client.return_value = mock_client
    
    # Mock S3 get_object response
    mock_response = {
        'Body': Mock(read=lambda: b"S3 content")
    }
    mock_client.get_object.return_value = mock_response
    
    mock_settings.s3_bucket = "test-bucket"
    mock_settings.s3_endpoint_url = None
    mock_settings.aws_access_key_id = "test-key"
    mock_settings.aws_secret_access_key = "test-secret"
    
    storage = S3Storage()
    
    uri = "s3://test-bucket/task-123/file.txt"
    
    # Retrieve file
    files = storage.retrieve_files([uri])
    
    # Verify S3 get_object called
    mock_client.get_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="task-123/file.txt"
    )
    
    # Verify retrieved data
    assert files == [(b"S3 content", "file.txt")]


@patch.dict('sys.modules', {'boto3': Mock()})
@patch('src.core.storage_adapter.settings')
def test_s3_storage_cleanup(mock_settings):
    """
    Test: S3Storage deletes all objects with task prefix.
    """
    import sys
    mock_boto3 = sys.modules['boto3']
    mock_client = Mock()
    mock_boto3.client.return_value = mock_client
    
    # Mock S3 list_objects_v2 response
    mock_client.list_objects_v2.return_value = {
        'Contents': [
            {'Key': 'task-123/file1.txt'},
            {'Key': 'task-123/file2.txt'},
        ]
    }
    
    mock_settings.s3_bucket = "test-bucket"
    mock_settings.s3_endpoint_url = None
    mock_settings.aws_access_key_id = "test-key"
    mock_settings.aws_secret_access_key = "test-secret"
    
    storage = S3Storage()
    
    # Cleanup
    storage.cleanup_task("task-123")
    
    # Verify S3 delete_objects called
    mock_client.delete_objects.assert_called_once()
    delete_call = mock_client.delete_objects.call_args
    assert delete_call[1]['Bucket'] == "test-bucket"
    assert len(delete_call[1]['Delete']['Objects']) == 2


@patch.dict('os.environ', {'STORAGE_BACKEND': 'local'})
@patch('src.core.storage_adapter.settings')
def test_storage_factory_local(mock_settings):
    """
    Test: StorageFactory creates LocalStorage when STORAGE_BACKEND=local.
    """
    mock_settings.storage_backend = "local"
    mock_settings.uploads_path = Path("/tmp/uploads")
    
    storage = StorageFactory.create()
    
    assert isinstance(storage, LocalStorage)


@patch.dict('sys.modules', {'boto3': Mock()})
@patch.dict('os.environ', {'STORAGE_BACKEND': 's3'})
@patch('src.core.storage_adapter.settings')
def test_storage_factory_s3(mock_settings):
    """
    Test: StorageFactory creates S3Storage when STORAGE_BACKEND=s3.
    """
    import sys
    mock_boto3 = sys.modules['boto3']
    mock_boto3.client.return_value = Mock()
    
    mock_settings.storage_backend = "s3"
    mock_settings.s3_bucket = "test-bucket"
    mock_settings.s3_endpoint_url = None
    mock_settings.aws_access_key_id = "test-key"
    mock_settings.aws_secret_access_key = "test-secret"
    
    storage = StorageFactory.create()
    
    assert isinstance(storage, S3Storage)


def test_storage_factory_invalid_backend():
    """
    Test: StorageFactory raises ValueError for invalid backend.
    """
    with patch('src.core.storage_adapter.settings') as mock_settings:
        mock_settings.storage_backend = "invalid"
        
        with pytest.raises(ValueError, match="Unknown storage backend"):
            StorageFactory.create()


def test_base_storage_prepare_payload():
    """
    Test: BaseStorage.prepare_payload creates reference-only dict.
    """
    # Use LocalStorage as concrete implementation
    storage = LocalStorage()
    
    file_refs = ["file:///path/1", "s3://bucket/path/2"]
    payload = storage.prepare_payload(file_refs)
    
    assert payload == {"file_refs": file_refs}
    assert "content" not in payload  # No embedded content
