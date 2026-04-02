from typing import Sequence


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
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    total_chars += len(str(block.get("text", "")))
                elif block.get("type") == "image_url":
                    image_url = block.get("image_url", {})
                    if isinstance(image_url, dict):
                        url = str(image_url.get("url", ""))
                        # Multimodal image payloads are not tokenized like plain text.
                        # Use fixed upper-bound costs instead of raw URL/base64 length.
                        if url.startswith("data:image/"):
                            image_token_cost += 1024
                        elif url:
                            image_token_cost += 256
    return int(total_chars / 3) + image_token_cost + 64
