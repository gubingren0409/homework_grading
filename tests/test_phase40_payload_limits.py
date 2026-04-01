import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from src.core.http_limits import HardBodyLimitMiddleware
from src.api.routes import _store_upload_file_with_limits


class _SlowReceive:
    def __init__(self):
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        await asyncio.sleep(0.2)
        return {"type": "http.request", "body": b"a", "more_body": True}


def test_e05_hard_limit_middleware_returns_413_by_content_length():
    app = FastAPI()
    app.add_middleware(HardBodyLimitMiddleware, max_body_bytes=10, read_timeout_seconds=0.1)

    @app.post("/upload")
    async def upload():
        return {"ok": True}

    client = TestClient(app)
    resp = client.post("/upload", data=b"12345678901", headers={"content-length": "11"})
    assert resp.status_code == 413
    assert resp.json()["detail"] == "payload too large"


@pytest.mark.asyncio
async def test_e05_slow_upload_times_out_with_408(tmp_path: Path):
    class FakeUploadFile:
        filename = "slow.bin"

        def __init__(self):
            self.calls = 0

        async def read(self, size=-1):
            self.calls += 1
            await asyncio.sleep(0.2)
            return b"x"

        async def close(self):
            return None

    upload = FakeUploadFile()

    with patch("src.api.routes.settings.request_body_read_timeout_seconds", 0.05), patch(
        "src.api.routes.settings.max_request_body_bytes", 1024
    ), patch("src.api.routes.settings.upload_chunk_size_bytes", 8), patch(
        "src.api.routes.settings.upload_spool_max_size_bytes", 16
    ):
        with pytest.raises(Exception) as exc_info:
            await _store_upload_file_with_limits("task-slow", upload)  # type: ignore[arg-type]
        assert getattr(exc_info.value, "status_code", None) == 408


@pytest.mark.asyncio
async def test_e05_stream_limit_hits_413_without_buffer_join():
    class FakeUploadFile:
        filename = "large.bin"

        def __init__(self):
            self.parts = [b"a" * 8, b"b" * 8, b""]
            self.idx = 0

        async def read(self, size=-1):
            chunk = self.parts[self.idx]
            self.idx += 1
            return chunk

        async def close(self):
            return None

    upload = FakeUploadFile()
    with patch("src.api.routes.settings.request_body_read_timeout_seconds", 1.0), patch(
        "src.api.routes.settings.max_request_body_bytes", 10
    ), patch("src.api.routes.settings.upload_chunk_size_bytes", 8), patch(
        "src.api.routes.settings.upload_spool_max_size_bytes", 16
    ):
        with pytest.raises(Exception) as exc_info:
            await _store_upload_file_with_limits("task-big", upload)  # type: ignore[arg-type]
        assert getattr(exc_info.value, "status_code", None) == 413
