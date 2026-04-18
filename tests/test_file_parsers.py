import io

import fitz
import pytest
from PIL import Image

from src.utils.file_parsers import process_multiple_files, UnsupportedFormatError


def _jpeg_bytes(color: tuple[int, int, int] = (255, 255, 255), size: tuple[int, int] = (64, 64)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _pdf_bytes(page_count: int = 2) -> bytes:
    doc = fitz.open()
    for idx in range(page_count):
        page = doc.new_page(width=300, height=200)
        page.insert_text((36, 72), f"page-{idx + 1}")
    payload = doc.tobytes()
    doc.close()
    return payload


@pytest.mark.asyncio
async def test_process_multiple_files_supports_multi_image_and_pdf():
    files_data = [
        (_jpeg_bytes((255, 0, 0)), "a.jpg"),
        (_jpeg_bytes((0, 255, 0)), "b.jpg"),
        (_pdf_bytes(page_count=2), "ref.pdf"),
    ]
    images = await process_multiple_files(files_data)
    assert len(images) == 4
    assert all(isinstance(item, bytes) and len(item) > 0 for item in images)


@pytest.mark.asyncio
async def test_process_multiple_files_rejects_word_documents():
    files_data = [(b"fake-doc", "reference.docx")]
    with pytest.raises(UnsupportedFormatError):
        await process_multiple_files(files_data)

