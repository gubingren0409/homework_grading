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


def choose_variant_with_weights(
    *,
    prompt_key: str,
    bucket_key: str,
    variants: Sequence[str],
    variant_weights: dict[str, int] | None = None,
    variant_hint: str | None = None,
    sticky_salt: str = "",
) -> str:
    if not variants:
        raise ValueError("No variants available for routing")
    if variant_hint and variant_hint in variants:
        return variant_hint
    if not variant_weights:
        return choose_variant(
            prompt_key=prompt_key,
            bucket_key=bucket_key,
            variants=variants,
            variant_hint=variant_hint,
        )

    weighted_pairs: list[tuple[str, int]] = []
    total_weight = 0
    for variant in variants:
        w = int(variant_weights.get(variant, 0))
        if w > 0:
            weighted_pairs.append((variant, w))
            total_weight += w
    if total_weight <= 0:
        return choose_variant(
            prompt_key=prompt_key,
            bucket_key=bucket_key,
            variants=variants,
            variant_hint=variant_hint,
        )

    basis = f"{prompt_key}:{bucket_key}:{sticky_salt}".encode("utf-8")
    digest = hashlib.sha256(basis).hexdigest()
    pos = int(digest[:8], 16) % total_weight
    acc = 0
    for variant, weight in weighted_pairs:
        acc += weight
        if pos < acc:
            return variant
    return weighted_pairs[-1][0]
