from typing import Dict, List, Sequence, Mapping

from src.prompts.exceptions import PromptVariableValidationError
from src.prompts.schemas import PromptVariable


def variables_to_map(variables: Sequence[PromptVariable]) -> Mapping[str, PromptVariable]:
    result: Dict[str, PromptVariable] = {}
    for item in variables:
        if item.name in result:
            raise PromptVariableValidationError(f"Duplicate prompt variable: {item.name}")
        result[item.name] = item
    return result


def validate_variable_kinds(
    *,
    actual: Mapping[str, PromptVariable],
    expected_schema: Mapping[str, str],
) -> None:
    for name, kind in expected_schema.items():
        var = actual.get(name)
        if var is None:
            raise PromptVariableValidationError(f"Missing prompt variable: {name}")
        if var.kind != kind:
            raise PromptVariableValidationError(
                f"Variable kind mismatch for {name}: expected={kind}, actual={var.kind}"
            )


def build_openai_user_content(
    *,
    user_text: str,
    variables: Mapping[str, PromptVariable],
) -> List[dict]:
    content: List[dict] = [{"type": "text", "text": user_text}]
    for item in variables.values():
        if item.kind == "image_base64":
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{item.value}"},
                }
            )
        elif item.kind == "image_url":
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": item.value},
                }
            )
    return content
