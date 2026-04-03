import json
from typing import Sequence

from src.db.client import insert_skill_validation_records
from src.skills.interfaces import ValidationRecord, ValidationSinkSkill


class DbValidationSinkSkill(ValidationSinkSkill):
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def persist(self, records: Sequence[ValidationRecord]) -> int:
        payload = []
        for item in records:
            payload.append(
                {
                    "task_id": item.task_id,
                    "student_id": item.student_id,
                    "question_id": item.question_id,
                    "checker": item.checker,
                    "status": item.status,
                    "confidence": float(item.confidence),
                    "details_json": item.details_json,
                }
            )
        return await insert_skill_validation_records(self._db_path, payload)
