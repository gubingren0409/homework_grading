import argparse
import copy
import json
import logging
from pathlib import Path

from src.perception.factory import create_perception_engine
from src.utils.image_slicer import slice_image_by_layout

logging.basicConfig(level=logging.INFO, format="%(message)s")

def _pick_sample_image(data_root: Path) -> Path:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    for p in sorted(data_root.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            return p
    raise FileNotFoundError(f"No image file found under {data_root}")


async def run(image_path: Path, context_type: str, target_question: str | None, out_dir: Path) -> None:
    image_bytes = image_path.read_bytes()
    engine = create_perception_engine()
    layout = await engine.extract_layout(
        image_bytes=image_bytes,
        context_type=context_type,
        target_question_no=target_question,
        page_index=0,
    )

    print("=== LayoutIR ===")
    print(f"context_type={layout.context_type} target_question_no={layout.target_question_no}")
    print(f"image_width={layout.image_width} image_height={layout.image_height}")
    print(f"regions={len(layout.regions)}")
    for r in layout.regions:
        print(
            f"- target_id={r.target_id} question_no={r.question_no} "
            f"type={r.region_type} bbox="
            f"[ymin={r.bbox.y_min:.4f}, xmin={r.bbox.x_min:.4f}, ymax={r.bbox.y_max:.4f}, xmax={r.bbox.x_max:.4f}]"
        )

    slices = slice_image_by_layout(image_bytes, layout)
    out_dir.mkdir(parents=True, exist_ok=True)
    print("\n=== Slices ===")
    for target_id, data in slices.items():
        out_file = out_dir / f"{target_id}.png"
        out_file.write_bytes(data)
        print(f"- {target_id}: {len(data)} bytes -> {out_file}")

    # Dirty coordinate stress test: [-50, 200, 1050, 800] in [0,1000] => [0,200,1000,800]
    dirty_payload = {
        "context_type": context_type,
        "target_question_no": target_question,
        "page_index": 0,
        "regions": [
            {
                "target_id": "dirty_case",
                "question_no": target_question,
                "region_type": "answer_region",
                "bbox": [-50, 200, 1050, 800],  # [ymin, xmin, ymax, xmax]
            }
        ],
        "warnings": ["injected-dirty-test"],
    }
    dirty_sanitized = engine._sanitize_layout_coordinates(copy.deepcopy(dirty_payload))
    dirty_layout = type(layout).model_validate(
        dirty_sanitized,
        context={"image_width": layout.image_width, "image_height": layout.image_height},
    )
    dirty_bbox = dirty_layout.regions[0].bbox
    print("\n=== Dirty Clamp Test ===")
    print("dirty_input=[-50,200,1050,800]")
    print(
        "dirty_clamped_normalized="
        f"[ymin={dirty_bbox.y_min:.4f}, xmin={dirty_bbox.x_min:.4f}, ymax={dirty_bbox.y_max:.4f}, xmax={dirty_bbox.x_max:.4f}]"
    )
    print(
        "dirty_clamped_0_1000="
        f"[{int(round(dirty_bbox.y_min*1000))},{int(round(dirty_bbox.x_min*1000))},"
        f"{int(round(dirty_bbox.y_max*1000))},{int(round(dirty_bbox.x_max*1000))}]"
    )
    dirty_slices = slice_image_by_layout(image_bytes, dirty_layout)
    for target_id, data in dirty_slices.items():
        out_file = out_dir / f"{target_id}.png"
        out_file.write_bytes(data)
        print(f"dirty_slice {target_id}: {len(data)} bytes -> {out_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase35 layout+slicer local CLI test")
    parser.add_argument("--image", type=str, default=None, help="Optional image path")
    parser.add_argument(
        "--data_root",
        type=str,
        default="data/3.20_physics/question_13",
        help="Fallback search root when --image is not provided",
    )
    parser.add_argument("--context_type", type=str, default="STUDENT_ANSWER", choices=["REFERENCE", "STUDENT_ANSWER"])
    parser.add_argument("--target_question", type=str, default=None)
    parser.add_argument("--out_dir", type=str, default="outputs/phase35_layout_slices")
    args = parser.parse_args()

    image_path = Path(args.image) if args.image else _pick_sample_image(Path(args.data_root))
    out_dir = Path(args.out_dir)

    import asyncio
    asyncio.run(run(image_path, args.context_type, args.target_question, out_dir))


if __name__ == "__main__":
    main()
