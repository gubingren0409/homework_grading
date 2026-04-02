import hashlib
from typing import Sequence


def choose_variant(
    *,
    prompt_key: str,
    bucket_key: str,
    variants: Sequence[str],
    variant_hint: str | None = None,
) -> str:
    if not variants:
        raise ValueError("No variants available for routing")
    if variant_hint and variant_hint in variants:
        return variant_hint
    basis = f"{prompt_key}:{bucket_key}".encode("utf-8")
    digest = hashlib.sha256(basis).hexdigest()
    idx = int(digest[:8], 16) % len(variants)
    return variants[idx]
