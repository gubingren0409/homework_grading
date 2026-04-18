import json
from typing import Any


def _to_json_compatible(payload: Any) -> Any:
    if payload is None:
        return None

    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload

    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        return model_dump()

    if isinstance(payload, (dict, list, int, float, bool)):
        return payload

    raise TypeError(f"Unsupported payload type for JSON serialization: {type(payload)!r}")


def _to_json_string(payload: Any) -> str:
    if isinstance(payload, str):
        try:
            json.loads(payload)
            return payload
        except json.JSONDecodeError:
            return json.dumps(payload, ensure_ascii=False)

    normalized = _to_json_compatible(payload)
    return json.dumps(normalized, ensure_ascii=False)

