import io
import asyncio
import fitz  # PyMuPDF
from PIL import Image
from src.core.config import settings


class UnsupportedFormatError(Exception):
    """Raised when an unsupported file format (e.g., Word) is provided."""
    pass


def _downsample_and_compress(img_bytes: bytes) -> bytes:
    """
    Standardizes image dimensions to max 2048x2048, strips EXIF,
    and compresses to JPEG 85 quality.
    """
    with Image.open(io.BytesIO(img_bytes)) as img:
        # Strip EXIF and convert to RGB (ensure JPEG compatibility)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        if settings.enable_page_deskew_preprocess:
            img = _deskew_image(img)
        
        # Resize if dimension exceeds 2048px
        max_size = (2048, 2048)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Save to buffer with quality compression and no EXIF
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue()


def _deskew_image(img: Image.Image) -> Image.Image:
    angle = _estimate_page_skew_angle(img)
    if abs(angle) < 0.25:
        return img
    return img.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=(255, 255, 255))


def _estimate_page_skew_angle(img: Image.Image) -> float:
    probe = img.convert("L")
    probe.thumbnail((700, 700), Image.Resampling.BILINEAR)
    best_angle = 0.0
    best_score = _horizontal_projection_score(probe)

    for step in range(-6, 7):
        angle = step * 0.5
        if angle == 0.0:
            continue
        rotated = probe.rotate(
            angle,
            resample=Image.Resampling.BILINEAR,
            expand=False,
            fillcolor=255,
        )
        score = _horizontal_projection_score(rotated)
        if score > best_score:
            best_score = score
            best_angle = angle
    return best_angle


def _horizontal_projection_score(img: Image.Image) -> float:
    width, height = img.size
    if width <= 0 or height <= 0:
        return 0.0
    pixels = img.load()
    row_counts = []
    x_start = max(0, int(width * 0.03))
    x_end = min(width, int(width * 0.97))
    for y in range(max(0, int(height * 0.03)), min(height, int(height * 0.97))):
        dark_pixels = 0
        for x in range(x_start, x_end, 3):
            if pixels[x, y] < 190:
                dark_pixels += 1
        row_counts.append(dark_pixels)
    if not row_counts:
        return 0.0
    mean = sum(row_counts) / len(row_counts)
    return sum((count - mean) ** 2 for count in row_counts)


def _normalize_to_images_sync(file_bytes: bytes, filename: str) -> list[bytes]:
    """
    Normalizes input files (PDF or standard images) into a list of JPEG image bytes.
    Includes mandatory downsampling and quality compression.
    """
    normalized_images = []

    if filename.lower().endswith((".doc", ".docx")):
        raise UnsupportedFormatError("Word documents are unsupported. Please convert to PDF.")

    # Case 1: PDF Document
    if filename.lower().endswith(".pdf"):
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=150)
                page_bytes = pix.tobytes("jpeg")
                normalized_images.append(_downsample_and_compress(page_bytes))
    
    # Case 2: Standard Image formats
    else:
        normalized_images.append(_downsample_and_compress(file_bytes))

    return normalized_images


async def normalize_to_images(file_bytes: bytes, filename: str) -> list[bytes]:
    """
    Async wrapper to offload CPU-heavy normalization to worker threads.
    """
    return await asyncio.to_thread(_normalize_to_images_sync, file_bytes, filename)


async def process_multiple_files(files_data: list[tuple[bytes, str]]) -> list[bytes]:
    """
    Flattens a list of (bytes, filename) tuples into a single array of image bytes.
    
    Args:
        files_data: List of (file_content, filename) pairs.
        
    Returns:
        list[bytes]: A flattened list of pure JPEG image streams.
        
    Raises:
        UnsupportedFormatError: If any file is a Word document.
    """
    if not files_data:
        return []

    preprocess_concurrency = max(1, int(settings.file_preprocess_concurrency))
    sem = asyncio.Semaphore(preprocess_concurrency)

    async def _normalize_one(file_bytes: bytes, filename: str) -> list[bytes]:
        async with sem:
            return await normalize_to_images(file_bytes, filename)

    chunks = await asyncio.gather(*[_normalize_one(file_bytes, filename) for file_bytes, filename in files_data])
    all_image_bytes: list[bytes] = []
    for images in chunks:
        all_image_bytes.extend(images)
    return all_image_bytes
