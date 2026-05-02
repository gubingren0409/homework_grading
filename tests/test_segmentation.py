from io import BytesIO

from PIL import Image

from src.orchestration.segmentation import (
    CURRENT_ANCHOR_OVERLAP_BAND,
    NEXT_ANCHOR_OVERLAP_BAND,
    AnswerRegionSplitter,
)
from src.schemas.perception_ir import BoundingBox, QuestionAnchor, QuestionAnchorSet
from src.skills.interfaces import LayoutParseResult, LayoutRegion


def _page_bytes(width: int = 100, height: int = 100) -> bytes:
    image = Image.new("RGB", (width, height), color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _crop_size(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(image_bytes)) as image:
        return image.size


def test_answer_region_splitter_slices_single_page_by_anchor_bands():
    splitter = AnswerRegionSplitter()
    image_bytes = _page_bytes()
    anchor_set = QuestionAnchorSet(
        page_index=0,
        anchors=[
            QuestionAnchor(
                raw_label="1.",
                question_no="1",
                page_index=0,
                order_index=0,
                source="perception",
                bbox=BoundingBox(x_min=0.10, y_min=0.10, x_max=0.20, y_max=0.15),
            ),
            QuestionAnchor(
                raw_label="2.",
                question_no="2",
                page_index=0,
                order_index=1,
                source="perception",
                bbox=BoundingBox(x_min=0.12, y_min=0.60, x_max=0.22, y_max=0.65),
            ),
        ],
    )
    layout = LayoutParseResult(
        context_type="STUDENT_ANSWER",
        page_index=0,
        regions=[
            LayoutRegion(
                target_id="b1",
                region_type="text",
                bbox={"x_min": 0.08, "y_min": 0.10, "x_max": 0.72, "y_max": 0.52},
            ),
            LayoutRegion(
                target_id="b2",
                region_type="text",
                bbox={"x_min": 0.14, "y_min": 0.60, "x_max": 0.80, "y_max": 0.92},
            ),
        ],
    )

    result = splitter.split_document([image_bytes], [anchor_set], [layout])

    assert [region.question_no for region in result.regions] == ["1", "2"]
    assert result.regions[0].bbox.y_min == 0.10 - CURRENT_ANCHOR_OVERLAP_BAND
    assert result.regions[0].bbox.y_max == 0.60 + NEXT_ANCHOR_OVERLAP_BAND
    assert result.regions[1].bbox.x_max == 0.88
    assert _crop_size(result.regions[0].cropped_image_bytes) == (88, 59)


def test_answer_region_splitter_uses_full_width_when_only_title_anchors_overlap():
    splitter = AnswerRegionSplitter()
    image_bytes = _page_bytes()
    anchor_set = QuestionAnchorSet(
        page_index=0,
        anchors=[
            QuestionAnchor(
                raw_label="17.",
                question_no="17",
                page_index=0,
                order_index=0,
                source="layout",
                bbox=BoundingBox(x_min=0.04, y_min=0.30, x_max=0.12, y_max=0.34),
            )
        ],
    )
    layout = LayoutParseResult(
        context_type="STUDENT_ANSWER",
        page_index=0,
        regions=[
            LayoutRegion(
                target_id="q17-title",
                region_type="title",
                question_no="17",
                bbox={"x_min": 0.04, "y_min": 0.30, "x_max": 0.12, "y_max": 0.34},
            )
        ],
    )

    result = splitter.split_document([image_bytes], [anchor_set], [layout])

    assert len(result.regions) == 1
    assert result.regions[0].bbox.x_min == 0.0
    assert result.regions[0].bbox.x_max == 1.0
    assert _crop_size(result.regions[0].cropped_image_bytes)[0] == 100


def test_answer_region_splitter_carries_last_question_across_pages_without_anchors():
    splitter = AnswerRegionSplitter()
    page_one = _page_bytes()
    page_two = _page_bytes()
    anchor_sets = [
        QuestionAnchorSet(
            page_index=0,
            anchors=[
                QuestionAnchor(
                    raw_label="3.",
                    question_no="3",
                    page_index=0,
                    order_index=0,
                    source="perception",
                    bbox=BoundingBox(x_min=0.10, y_min=0.70, x_max=0.20, y_max=0.75),
                )
            ],
        ),
        QuestionAnchorSet(page_index=1, anchors=[]),
    ]
    layouts = [
        LayoutParseResult(
            context_type="STUDENT_ANSWER",
            page_index=0,
            regions=[
                LayoutRegion(
                    target_id="b1",
                    region_type="text",
                    bbox={"x_min": 0.10, "y_min": 0.70, "x_max": 0.75, "y_max": 0.95},
                )
            ],
        ),
        LayoutParseResult(
            context_type="STUDENT_ANSWER",
            page_index=1,
            regions=[
                LayoutRegion(
                    target_id="b2",
                    region_type="text",
                    bbox={"x_min": 0.12, "y_min": 0.05, "x_max": 0.78, "y_max": 0.88},
                )
            ],
        ),
    ]

    result = splitter.split_document([page_one, page_two], anchor_sets, layouts)

    assert [region.question_no for region in result.regions] == ["3", "3"]
    assert result.regions[1].page_index == 1
    assert result.regions[1].bbox.y_min == 0.0
    assert result.regions[1].bbox.y_max == 1.0
    assert _crop_size(result.regions[1].cropped_image_bytes) == (82, 100)
