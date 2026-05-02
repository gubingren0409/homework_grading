from pathlib import Path

import pytest

from src.orchestration.reference_image_description import (
    describe_markdown_image_assets,
    extract_markdown_image_asset_refs,
    reference_output_to_dense_description,
)
from src.perception.base import BasePerceptionEngine
from src.schemas.perception_ir import BoundingBox, PerceptionNode, PerceptionOutput


class _ReferenceImageEngine(BasePerceptionEngine):
    def __init__(self) -> None:
        self.contexts: list[str] = []

    async def process_image(self, image_bytes: bytes) -> PerceptionOutput:
        del image_bytes
        return self._output("single")

    async def process_images(
        self,
        image_bytes_list: list[bytes],
        *,
        context_type: str = "student_homework",
    ) -> list[PerceptionOutput]:
        self.contexts.append(context_type)
        return [self._output(str(index)) for index, _ in enumerate(image_bytes_list, start=1)]

    def _output(self, suffix: str) -> PerceptionOutput:
        return PerceptionOutput(
            readability_status="CLEAR",
            elements=[
                PerceptionNode(
                    element_id=f"diagram-{suffix}",
                    content_type="image_diagram",
                    raw_content=f"图中左侧为线圈 L，右侧为可变电容 C，二者构成调谐电路 {suffix}。",
                    confidence_score=0.99,
                    bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.8, y_max=0.5),
                )
            ],
            global_confidence=0.99,
        )


def test_extract_markdown_image_asset_refs_dedupes_in_order():
    markdown = """
1．题干
![](images/a.jpg)
![](images/b.jpg)
![](images/a.jpg)
"""

    assert extract_markdown_image_asset_refs(markdown) == ["images/a.jpg", "images/b.jpg"]


def test_reference_output_to_dense_description_keeps_visual_content():
    output = PerceptionOutput(
        readability_status="CLEAR",
        elements=[
            PerceptionNode(
                element_id="table",
                content_type="table",
                raw_content="|组别|U1|U2|\n|---|---|---|",
                confidence_score=1.0,
            )
        ],
        global_confidence=1.0,
    )

    assert reference_output_to_dense_description(output).startswith("表格转写：|组别|U1|U2|")


@pytest.mark.asyncio
async def test_describe_markdown_image_assets_uses_reference_context(tmp_path: Path):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.jpg").write_bytes(b"fake-image-a")
    markdown = "1．如图所示。\n![](images/a.jpg)\n【答案】A"
    engine = _ReferenceImageEngine()

    descriptions = await describe_markdown_image_assets(
        markdown,
        asset_root=tmp_path,
        perception_engine=engine,
    )

    assert engine.contexts == ["REFERENCE"]
    assert "images/a.jpg" in descriptions
    assert "线圈 L" in descriptions["images/a.jpg"]


@pytest.mark.asyncio
async def test_describe_markdown_image_assets_surfaces_missing_asset(tmp_path: Path):
    engine = _ReferenceImageEngine()

    with pytest.raises(FileNotFoundError, match="Markdown image asset not found"):
        await describe_markdown_image_assets(
            "![](images/missing.jpg)",
            asset_root=tmp_path,
            perception_engine=engine,
        )
