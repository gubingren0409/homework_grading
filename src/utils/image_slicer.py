from io import BytesIO
import logging
from typing import Dict

from PIL import Image

from src.schemas.perception_ir import LayoutIR

logger = logging.getLogger(__name__)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def slice_image_by_layout(image_bytes: bytes, layout: LayoutIR, output_format: str = "PNG") -> Dict[str, bytes]:
    """
    Phase 35 physical slicer.
    Input:
      - image_bytes: original image binary
      - layout: normalized LayoutIR with bbox x/y in [0,1]
    Output:
      - Dict[target_id, cropped_image_bytes]

    Safety:
      - host-side bounds clamp
      - float->int guarded conversion
      - axis mapping to PIL crop box: [left, upper, right, lower] = [xmin, ymin, xmax, ymax]
    """
    output: Dict[str, bytes] = {}

    with Image.open(BytesIO(image_bytes)) as img:
        # Single decoded image object reused for all crops in this call.
        width, height = img.size
        logger.info("image_slicer_source_image_id=%s", id(img))

        for idx, region in enumerate(layout.regions):
            box = region.bbox
            target_id = region.target_id or f"region_{idx}"
            logger.info("image_slicer_region=%s source_image_id=%s", target_id, id(img))

            xmin = _clamp(box.x_min, 0.0, 1.0)
            ymin = _clamp(box.y_min, 0.0, 1.0)
            xmax = _clamp(box.x_max, 0.0, 1.0)
            ymax = _clamp(box.y_max, 0.0, 1.0)

            # ensure monotonic
            if xmax < xmin:
                xmin, xmax = xmax, xmin
            if ymax < ymin:
                ymin, ymax = ymax, ymin

            # float->int with boundary defense
            left_f = xmin * width
            upper_f = ymin * height
            right_f = xmax * width
            lower_f = ymax * height

            left = max(0, min(width, int(round(left_f))))
            upper = max(0, min(height, int(round(upper_f))))
            right = max(0, min(width, int(round(right_f))))
            lower = max(0, min(height, int(round(lower_f))))
            logger.info(
                "image_slicer_pixels target=%s float_box=[%.3f, %.3f, %.3f, %.3f] int_box=[%s,%s,%s,%s]",
                target_id,
                left_f,
                upper_f,
                right_f,
                lower_f,
                left,
                upper,
                right,
                lower,
            )

            # prevent empty crops (at least 1px when possible)
            if right <= left:
                right = min(width, left + 1)
            if lower <= upper:
                lower = min(height, upper + 1)
            if right <= left or lower <= upper:
                continue

            cropped = img.crop((left, upper, right, lower))
            buf = BytesIO()
            cropped.save(buf, format=output_format)
            output[target_id] = buf.getvalue()

    return output
