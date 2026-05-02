from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

from PIL import Image

from src.schemas.perception_ir import BoundingBox, QuestionAnchor, QuestionAnchorSet, StudentAnswerRegion
from src.skills.interfaces import LayoutParseResult, LayoutRegion

CURRENT_ANCHOR_OVERLAP_BAND = 0.05
NEXT_ANCHOR_OVERLAP_BAND = 0.045
HORIZONTAL_SAFETY_MARGIN = 0.08
ANCHOR_ONLY_REGION_TYPES = {"title"}


@dataclass(frozen=True)
class AnswerRegionSplitResult:
    regions: list[StudentAnswerRegion]
    warnings: list[str] = field(default_factory=list)


class AnswerRegionSplitter:
    def split_document(
        self,
        page_images: list[bytes],
        anchor_sets: list[QuestionAnchorSet],
        layout_results: list[LayoutParseResult],
    ) -> AnswerRegionSplitResult:
        if not (len(page_images) == len(anchor_sets) == len(layout_results)):
            raise ValueError("page_images, anchor_sets, and layout_results must have the same length")

        regions: list[StudentAnswerRegion] = []
        warnings: list[str] = []
        carry_question_no: Optional[str] = None

        for page_index, image_bytes in enumerate(page_images):
            anchors = self._sort_anchors(anchor_sets[page_index].anchors)
            blocks = self._sort_regions(layout_results[page_index].regions)
            warnings.extend(anchor_sets[page_index].warnings)
            warnings.extend(layout_results[page_index].warnings)
            explicit_regions = self._explicit_question_regions(blocks)
            if explicit_regions:
                for explicit_region in explicit_regions:
                    regions.append(
                        self._build_region(
                            image_bytes=image_bytes,
                            question_no=str(explicit_region.question_no),
                            page_index=page_index,
                            bbox=BoundingBox.model_validate(explicit_region.bbox),
                        )
                    )
                carry_question_no = str(explicit_regions[-1].question_no)
                continue

            if not anchors:
                if carry_question_no is None:
                    warnings.append(f"page {page_index}: no question anchors detected")
                    continue
                bbox = self._derive_bbox(
                    anchor=None,
                    band_start=0.0,
                    band_end=1.0,
                    blocks=blocks,
                )
                regions.append(
                    self._build_region(
                        image_bytes=image_bytes,
                        question_no=carry_question_no,
                        page_index=page_index,
                        bbox=bbox,
                    )
                )
                continue

            if carry_question_no is not None and anchors[0].bbox.y_min > 0.0:
                prefix_bbox = self._derive_bbox(
                    anchor=None,
                    band_start=0.0,
                    band_end=anchors[0].bbox.y_min,
                    blocks=blocks,
                )
                regions.append(
                    self._build_region(
                        image_bytes=image_bytes,
                        question_no=carry_question_no,
                        page_index=page_index,
                        bbox=prefix_bbox,
                    )
                )

            for idx, anchor in enumerate(anchors):
                next_anchor_y = anchors[idx + 1].bbox.y_min if idx + 1 < len(anchors) else 1.0
                bbox = self._derive_bbox(
                    anchor=anchor,
                    band_start=anchor.bbox.y_min,
                    band_end=next_anchor_y,
                    blocks=blocks,
                )
                regions.append(
                    self._build_region(
                        image_bytes=image_bytes,
                        question_no=anchor.question_no,
                        page_index=page_index,
                        bbox=bbox,
                    )
                )
            carry_question_no = anchors[-1].question_no

        return AnswerRegionSplitResult(regions=regions, warnings=warnings)

    def _explicit_question_regions(self, regions: list[LayoutRegion]) -> list[LayoutRegion]:
        explicit = [
            region
            for region in regions
            if region.question_no
            and region.region_type in {"question_region", "answer_region"}
            and self._has_usable_area(region)
        ]
        return self._sort_regions(explicit)

    def _has_usable_area(self, region: LayoutRegion) -> bool:
        x_min = float(region.bbox.get("x_min", 0.0))
        y_min = float(region.bbox.get("y_min", 0.0))
        x_max = float(region.bbox.get("x_max", 0.0))
        y_max = float(region.bbox.get("y_max", 0.0))
        return (x_max - x_min) >= 0.02 and (y_max - y_min) >= 0.02

    def _derive_bbox(
        self,
        *,
        anchor: Optional[QuestionAnchor],
        band_start: float,
        band_end: float,
        blocks: list[LayoutRegion],
    ) -> BoundingBox:
        effective_band_start = self._effective_band_start(
            anchor=anchor,
            band_start=band_start,
            band_end=band_end,
        )
        effective_band_end = self._effective_band_end(
            anchor=anchor,
            band_start=band_start,
            band_end=band_end,
        )
        overlapping = [
            block for block in blocks
            if float(block.bbox.get("y_min", 1.0)) < effective_band_end
            and float(block.bbox.get("y_max", 0.0)) > effective_band_start
        ]
        content_blocks = [
            block for block in overlapping
            if block.region_type not in ANCHOR_ONLY_REGION_TYPES
        ]
        if content_blocks:
            x_min = min(float(block.bbox.get("x_min", 0.0)) for block in content_blocks)
            x_max = max(float(block.bbox.get("x_max", 1.0)) for block in content_blocks)
        else:
            x_min = 0.0
            x_max = 1.0

        if anchor is not None:
            x_min = min(x_min, anchor.bbox.x_min)
            x_max = max(x_max, anchor.bbox.x_max)

        return BoundingBox(
            x_min=max(0.0, min(1.0, x_min - HORIZONTAL_SAFETY_MARGIN)),
            y_min=max(0.0, min(1.0, effective_band_start)),
            x_max=max(0.0, min(1.0, x_max + HORIZONTAL_SAFETY_MARGIN)),
            y_max=max(0.0, min(1.0, max(effective_band_start, effective_band_end))),
        )

    def _effective_band_start(
        self,
        *,
        anchor: Optional[QuestionAnchor],
        band_start: float,
        band_end: float,
    ) -> float:
        if anchor is None or band_start <= 0.0:
            return band_start
        band_height = band_end - band_start
        if band_height <= 0.0:
            return band_start
        overlap_band = min(CURRENT_ANCHOR_OVERLAP_BAND, band_height * 0.25)
        return max(0.0, band_start - overlap_band)

    def _effective_band_end(
        self,
        *,
        anchor: Optional[QuestionAnchor],
        band_start: float,
        band_end: float,
    ) -> float:
        if anchor is None or band_end >= 1.0:
            return band_end
        band_height = band_end - band_start
        if band_height <= 0.0:
            return band_end
        overlap_band = min(NEXT_ANCHOR_OVERLAP_BAND, band_height * 0.25)
        return min(1.0, band_end + overlap_band)

    def _build_region(
        self,
        *,
        image_bytes: bytes,
        question_no: str,
        page_index: int,
        bbox: BoundingBox,
    ) -> StudentAnswerRegion:
        return StudentAnswerRegion(
            question_no=question_no,
            page_index=page_index,
            bbox=bbox,
            cropped_image_bytes=self._crop_image(image_bytes, bbox),
        )

    def _crop_image(self, image_bytes: bytes, bbox: BoundingBox) -> bytes:
        with Image.open(BytesIO(image_bytes)) as image:
            width, height = image.size
            left = max(0, min(width, int(round(bbox.x_min * width))))
            upper = max(0, min(height, int(round(bbox.y_min * height))))
            right = max(0, min(width, int(round(bbox.x_max * width))))
            lower = max(0, min(height, int(round(bbox.y_max * height))))

            if right <= left:
                right = min(width, left + 1)
            if lower <= upper:
                lower = min(height, upper + 1)

            cropped = image.crop((left, upper, right, lower))
            buffer = BytesIO()
            cropped = cropped.convert("RGB")
            cropped.save(buffer, format="JPEG", quality=86, optimize=True)
            return buffer.getvalue()

    def _sort_anchors(self, anchors: list[QuestionAnchor]) -> list[QuestionAnchor]:
        return sorted(
            anchors,
            key=lambda anchor: (anchor.page_index, anchor.bbox.y_min, anchor.bbox.x_min, anchor.order_index),
        )

    def _sort_regions(self, regions: list[LayoutRegion]) -> list[LayoutRegion]:
        return sorted(
            regions,
            key=lambda region: (
                float(region.bbox.get("y_min", 1.0)),
                float(region.bbox.get("x_min", 1.0)),
                region.target_id,
            ),
        )
