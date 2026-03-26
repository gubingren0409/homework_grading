import io
import fitz  # PyMuPDF
from PIL import Image


class UnsupportedFormatError(Exception):
    """Raised when an unsupported file format (e.g., Word) is provided."""
    pass


async def _downsample_and_compress(img_bytes: bytes) -> bytes:
    """
    Standardizes image dimensions to max 2048x2048, strips EXIF,
    and compresses to JPEG 85 quality.
    """
    with Image.open(io.BytesIO(img_bytes)) as img:
        # Strip EXIF and convert to RGB (ensure JPEG compatibility)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        # Resize if dimension exceeds 2048px
        max_size = (2048, 2048)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Save to buffer with quality compression and no EXIF
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue()


async def normalize_to_images(file_bytes: bytes, filename: str) -> list[bytes]:
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
                normalized_images.append(await _downsample_and_compress(page_bytes))
    
    # Case 2: Standard Image formats
    else:
        normalized_images.append(await _downsample_and_compress(file_bytes))

    return normalized_images


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
    all_image_bytes = []
    
    for file_bytes, filename in files_data:
        # Re-use normalized_to_images for consistency and error catching
        images = await normalize_to_images(file_bytes, filename)
        all_image_bytes.extend(images)
        
    return all_image_bytes
