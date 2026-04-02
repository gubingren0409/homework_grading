from jinja2 import Environment, StrictUndefined

from src.prompts.exceptions import PromptRenderError


_env = Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)


def render_template(template_text: str, variables: dict) -> str:
    try:
        tpl = _env.from_string(template_text)
        return tpl.render(**variables)
    except Exception as exc:
        raise PromptRenderError(f"Prompt template render failed: {exc}") from exc
