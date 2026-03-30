from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ValidationInfo, model_validator


class BoundingBox(BaseModel):
    """Normalized coordinates [0.0, 1.0] for the detected element."""
    x_min: float = Field(..., ge=0.0, le=1.0)
    y_min: float = Field(..., ge=0.0, le=1.0)
    x_max: float = Field(..., ge=0.0, le=1.0)
    y_max: float = Field(..., ge=0.0, le=1.0)


class PerceptionNode(BaseModel):
    """A single structural element extracted from the image."""
    element_id: str
    content_type: Literal[
        "plain_text", 
        "latex_formula", 
        "chemical_equation", 
        "molecular_smiles", 
        "geometry_topology", 
        "coordinate_plot", 
        "circuit_schematic",
        "image_diagram",  # 新增：物理配图/图表
        "table",          # 新增：表格
        "image"           # 新增：通用图片
    ]
    raw_content: str = Field(..., description="The textual representation or LaTeX code.")
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    bbox: Optional[BoundingBox] = None

    @model_validator(mode="after")
    def validate_content_depth(self) -> "PerceptionNode":
        """
        防腐校验：强迫感知层输出高密度信息。
        如果 content_type 是图表或表格，绝对禁止输出无意义的占位符。
        """
        forbidden_placeholders = ["[图片]", "[图表]", "[表格]", "image", "diagram", "table"]
        
        if self.content_type in ["image_diagram", "table", "image"]:
            content_stripped = self.raw_content.strip()
            
            # 1. 拦截已知占位符
            if content_stripped.lower() in forbidden_placeholders:
                raise ValueError(
                    f"Perception failure: node {self.element_id} ({self.content_type}) "
                    f"returned a meaningless placeholder: '{self.raw_content}'"
                )
            
            # 2. 拦截过短的描述（图表和表格通常需要较长的文字描述或 Markdown 结构）
            if len(content_stripped) < 10:
                raise ValueError(
                    f"Perception failure: node {self.element_id} ({self.content_type}) "
                    f"description is too shallow (length < 10): '{self.raw_content}'"
                )
        
        return self


class PerceptionOutput(BaseModel):
    """The complete IR output from the Perception engine."""
    readability_status: Literal["CLEAR", "MINOR_ALTERATION", "HEAVILY_ALTERED", "UNREADABLE"]
    elements: List[PerceptionNode]
    global_confidence: float = Field(..., ge=0.0, le=1.0)
    is_blank: bool = Field(
        False, description="True if the page contains no student handwriting."
    )
    trigger_short_circuit: bool = Field(
        False, description="True if the image is too messy to proceed with logic evaluation."
    )


class LayoutBoundingBox(BaseModel):
    """
    Phase 35: Semantic layout detection region.
    BBox is normalized to [0.0, 1.0] after host-side sanitation.
    """

    target_id: str = Field(..., min_length=1)
    question_no: Optional[str] = None
    region_type: Literal["question_region", "answer_region"] = "answer_region"
    bbox: BoundingBox


class LayoutIR(BaseModel):
    """
    Phase 35: Layout Analysis Agent output contract.
    IMPORTANT:
    - image_width / image_height are injected by host context at validation time,
      never trusted from raw VLM JSON.
    """

    context_type: Literal["REFERENCE", "STUDENT_ANSWER"]
    target_question_no: Optional[str] = None
    page_index: int = Field(default=0, ge=0)
    image_width: Optional[int] = Field(default=None, gt=0)
    image_height: Optional[int] = Field(default=None, gt=0)
    regions: List[LayoutBoundingBox] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def inject_dimensions_from_host(self, info: ValidationInfo) -> "LayoutIR":
        context = info.context or {}
        if self.image_width is None:
            self.image_width = context.get("image_width")
        if self.image_height is None:
            self.image_height = context.get("image_height")

        if self.image_width is None or self.image_height is None:
            raise ValueError(
                "image_width/image_height must be injected by host via model_validate(context=...)."
            )
        return self
