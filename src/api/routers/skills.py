from fastapi import APIRouter, HTTPException, Request

from src.perception.factory import create_perception_engine
from src.skills.interfaces import ValidationInput
from src.api.route_helpers import (
    validate_skill_gateway_token as _validate_skill_gateway_token,
)
from src.api.route_models import (
    SkillLayoutParseRequest,
    SkillValidationRequest,
)

router = APIRouter()


@router.post("/skills/layout/parse")
async def skill_layout_parse(payload: SkillLayoutParseRequest, request: Request):
    """
    Internal skill gateway endpoint.
    Used when SKILL_LAYOUT_PARSER_API_URL points to local service.
    """
    import base64
    _validate_skill_gateway_token(request)

    try:
        image_bytes = base64.b64decode(payload.image_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="invalid image_base64") from exc

    engine = create_perception_engine()
    if not hasattr(engine, "extract_layout"):
        raise HTTPException(status_code=501, detail="perception engine does not support layout extraction")

    layout = await engine.extract_layout(
        image_bytes,
        context_type=payload.context_type,
        target_question_no=payload.target_question_no,
        page_index=payload.page_index,
    )
    return {
        "context_type": layout.context_type,
        "target_question_no": layout.target_question_no,
        "page_index": layout.page_index,
        "regions": [
            {
                "target_id": item.target_id,
                "question_no": item.question_no,
                "region_type": item.region_type,
                "bbox": item.bbox.model_dump(),
            }
            for item in layout.regions
        ],
        "warnings": list(layout.warnings),
    }


@router.post("/skills/validate")
async def skill_validate(payload: SkillValidationRequest, request: Request):
    """
    Internal validation gateway endpoint.
    Default implementation is contract-only and returns deterministic structure.
    """
    _validate_skill_gateway_token(request)
    validation_input = ValidationInput(
        task_id=payload.task_id,
        question_id=payload.question_id,
        perception_payload=payload.perception_payload,
        evaluation_payload=payload.evaluation_payload,
        rubric_payload=payload.rubric_payload,
    )
    del validation_input
    return {
        "status": "ok",
        "confidence": 0.0,
        "details": {"mode": "gateway_stub"},
        "warnings": ["No external validator configured, stub response used."],
    }
