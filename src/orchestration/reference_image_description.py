from __future__ import annotations

import re
from pathlib import Path

from src.core.config import settings
from src.perception.base import BasePerceptionEngine
from src.schemas.perception_ir import PerceptionOutput

_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\((?P<asset>[^)]+)\)")
_VISUAL_CONTENT_TYPES = {
    "image_diagram",
    "image",
    "coordinate_plot",
    "circuit_schematic",
    "geometry_topology",
}


def extract_markdown_image_asset_refs(markdown_text: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for match in _MARKDOWN_IMAGE_RE.finditer(markdown_text):
        asset_ref = match.group("asset").strip()
        if not asset_ref or asset_ref in seen:
            continue
        seen.add(asset_ref)
        refs.append(asset_ref)
    return refs


def reference_output_to_dense_description(output: PerceptionOutput) -> str:
    def _key(node) -> tuple[float, float, str]:
        if node.bbox is None:
            return (1e9, 1e9, node.element_id)
        return (node.bbox.y_min, node.bbox.x_min, node.element_id)

    parts: list[str] = []
    for node in sorted(output.elements, key=_key):
        content = node.raw_content.strip()
        if not content:
            continue
        if node.content_type == "table":
            parts.append(f"表格转写：{content}")
        elif node.content_type in _VISUAL_CONTENT_TYPES:
            parts.append(content)
        else:
            parts.append(f"{node.content_type}：{content}")
    return "\n".join(parts).strip()


async def describe_markdown_image_assets(
    markdown_text: str,
    *,
    asset_root: Path,
    perception_engine: BasePerceptionEngine,
) -> dict[str, str]:
    asset_refs = extract_markdown_image_asset_refs(markdown_text)
    if not asset_refs:
        return {}

    image_bytes_by_ref: list[tuple[str, bytes]] = []
    for asset_ref in asset_refs:
        image_path = asset_root / asset_ref
        if not image_path.exists():
            raise FileNotFoundError(f"Markdown image asset not found: {image_path}")
        image_bytes_by_ref.append((asset_ref, image_path.read_bytes()))

    descriptions: dict[str, str] = {}
    chunk_size = max(1, int(settings.qwen_batch_max_images))
    for start in range(0, len(image_bytes_by_ref), chunk_size):
        chunk = image_bytes_by_ref[start:start + chunk_size]
        outputs = await perception_engine.process_images(
            [image_bytes for _, image_bytes in chunk],
            context_type="REFERENCE",
        )
        if len(outputs) != len(chunk):
            raise RuntimeError("REFERENCE_IMAGE_DESCRIPTION_OUTPUT_COUNT_MISMATCH")
        for (asset_ref, _), output in zip(chunk, outputs):
            description = reference_output_to_dense_description(output)
            if description:
                descriptions[asset_ref] = description
    return descriptions
