from typing import Sequence


def _count_text_block_chars(block: dict) -> int:
    """Count characters in a text block."""
    if block.get("type") == "text":
        return len(str(block.get("text", "")))
    return 0


def _count_image_block_tokens(block: dict) -> int:
    """Count tokens for an image block."""
    if block.get("type") != "image_url":
        return 0

    image_url = block.get("image_url", {})
    if not isinstance(image_url, dict):
        return 0

    url = str(image_url.get("url", ""))
    if not url:
        return 0

    # Multimodal image payloads are not tokenized like plain text.
    # Use fixed upper-bound costs instead of raw URL/base64 length.
    if url.startswith("data:image/"):
        return 1024
    return 256


def _count_content_blocks(content: list) -> tuple[int, int]:
    """Count characters and image tokens from content blocks."""
    total_chars = 0
    image_tokens = 0

    for block in content:
        if not isinstance(block, dict):
            continue

        total_chars += _count_text_block_chars(block)
        image_tokens += _count_image_block_tokens(block)

    return total_chars, image_tokens


def estimate_tokens(messages: Sequence[dict], model: str) -> int:
    _ = model
    # Conservative approximation in absence of model-specific tokenizer.
    # Keeps guard strict by over-estimating with a fixed framing overhead.
    total_chars = 0
    image_token_cost = 0

    for m in messages:
        content = m.get("content", "")

        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            chars, tokens = _count_content_blocks(content)
            total_chars += chars
            image_token_cost += tokens

    return int(total_chars / 3) + image_token_cost + 64
