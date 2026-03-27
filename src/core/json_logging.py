import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from src.core.trace_context import log_context


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        context = log_context()
        component = context.get("component")
        if not component or component == "-":
            component = record.name
        task_id = context.get("task_id", "-")
        trace_id = context.get("trace_id", "-")
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "trace_id": trace_id,
            "task_id": task_id,
            "component": component,
            "event": record.getMessage(),
        }
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            payload.update(record.extra_fields)
        payload.setdefault("trace_id", trace_id)
        payload.setdefault("task_id", task_id)
        payload.setdefault("component", component)
        payload.setdefault("event", record.getMessage())
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_json_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

