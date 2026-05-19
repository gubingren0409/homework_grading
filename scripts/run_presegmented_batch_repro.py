from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cognitive.engines.deepseek_engine import DeepSeekCognitiveEngine
from src.orchestration.paper_workflow import PaperGradingWorkflow
from src.perception.factory import create_perception_engine
from src.schemas.rubric_ir import RubricBundle, TeacherRubric

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}


def load_rubric_bundle(path: Path, *, paper_id: str) -> RubricBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    if "rubrics" in payload:
        bundle = RubricBundle.model_validate(payload)
        if bundle.paper_id:
            return bundle
        return bundle.model_copy(update={"paper_id": paper_id})
    rubric = TeacherRubric.model_validate(payload)
    return RubricBundle(paper_id=paper_id, rubrics=[rubric], question_tree=[])


def collect_student_inputs(students_dir: Path) -> list[tuple[str, list[Path]]]:
    if not students_dir.exists():
        raise FileNotFoundError(f"students_dir not found: {students_dir}")
    entries: list[tuple[str, list[Path]]] = []
    for item in sorted(students_dir.iterdir()):
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
            entries.append((item.name, [item]))
            continue
        if item.is_dir():
            files = sorted(
                entry for entry in item.iterdir()
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            if files:
                entries.append((item.name, files))
    if not entries:
        raise ValueError(f"no supported files found under {students_dir}")
    return entries


def build_batch_row(*, student_file: str, report: Any) -> dict[str, Any]:
    question_report = next(iter(report.per_question.values()))
    answer = report.student_answer_bundle.answers[0] if report.student_answer_bundle and report.student_answer_bundle.answers else None
    return {
        "student_file": student_file,
        "total_score_deduction": report.total_score_deduction,
        "requires_human_review": report.requires_human_review,
        "review_reasons": list(report.review_reasons),
        "question_review_reasons": list(question_report.review_reasons),
        "runtime_profile": report.runtime_profile,
        "answer_text": answer.answer_text if answer is not None else "",
        "extraction_warnings": list(answer.extraction_warnings) if answer is not None else [],
        "extraction_debug": dict(answer.extraction_debug) if answer is not None else {},
    }


async def run_presegmented_batch(
    *,
    students_dir: Path,
    rubric_path: Path,
    output_json: Path,
    label: str,
    paper_id: str,
) -> Path:
    bundle = load_rubric_bundle(rubric_path, paper_id=paper_id)
    question_ids = [rubric.question_id for rubric in bundle.rubrics]
    perception_engine = create_perception_engine()
    workflow = PaperGradingWorkflow(
        perception_engine=perception_engine,
        cognitive_agent=DeepSeekCognitiveEngine(),
    )
    rows: list[dict[str, Any]] = []
    for student_file, paths in collect_student_inputs(students_dir):
        image_bytes_list = [path.read_bytes() for path in paths]
        report = await workflow.run_pipeline_with_presegmented_images(
            image_bytes_list,
            bundle,
            presegmented_question_ids=question_ids,
        )
        rows.append(build_batch_row(student_file=student_file, report=report))

    payload = {
        "label": label,
        "count": len(rows),
        "rows": rows,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeated presegmented batch grading for repro-matrix inputs.")
    parser.add_argument("--students-dir", required=True, help="Directory containing one image per student (or per-student subdirs).")
    parser.add_argument("--rubric-file", required=True, help="TeacherRubric or RubricBundle JSON path.")
    parser.add_argument("--output-json", required=True, help="Where to write the batch result JSON.")
    parser.add_argument("--label", required=True, help="Profile label, e.g. full / fast.")
    parser.add_argument("--paper-id", default="presegmented-batch", help="Paper id used when wrapping a single TeacherRubric.")
    args = parser.parse_args()

    output = asyncio.run(
        run_presegmented_batch(
            students_dir=Path(args.students_dir),
            rubric_path=Path(args.rubric_file),
            output_json=Path(args.output_json),
            label=args.label,
            paper_id=args.paper_id,
        )
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
