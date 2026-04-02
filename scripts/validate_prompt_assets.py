#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence

from jinja2 import Environment, TemplateSyntaxError, meta


VALID_VARIABLE_KINDS = {"text", "image_base64", "image_url"}
_JINJA_ENV = Environment(autoescape=False)


def _template_variables(template_text: str) -> set[str]:
    parsed = _JINJA_ENV.parse(template_text)
    return set(meta.find_undeclared_variables(parsed))


def _iter_prompt_files(repo_root: Path, files: Sequence[str] | None) -> list[Path]:
    if files:
        resolved: list[Path] = []
        for item in files:
            path = Path(item)
            if not path.is_absolute():
                path = (repo_root / path).resolve()
            resolved.append(path)
        return resolved
    prompts_dir = repo_root / "configs" / "prompts"
    return sorted(prompts_dir.glob("*.json"))


def _validate_meta(asset_path: Path, payload: dict, errors: list[str]) -> tuple[str | None, dict[str, str]]:
    meta_obj = payload.get("meta")
    if not isinstance(meta_obj, dict):
        errors.append(f"{asset_path}: missing or invalid 'meta' object")
        return None, {}

    prompt_key = meta_obj.get("prompt_key")
    if not isinstance(prompt_key, str) or not prompt_key.strip():
        errors.append(f"{asset_path}: meta.prompt_key must be non-empty string")
        prompt_key = None
    elif prompt_key != asset_path.stem:
        errors.append(f"{asset_path}: meta.prompt_key '{prompt_key}' must match filename '{asset_path.stem}'")

    version = meta_obj.get("version")
    if not isinstance(version, str) or not version.strip():
        errors.append(f"{asset_path}: meta.version must be non-empty string")

    version_hash = meta_obj.get("version_hash")
    if not isinstance(version_hash, str) or not version_hash.strip():
        errors.append(f"{asset_path}: meta.version_hash must be non-empty string")

    schema_version = meta_obj.get("schema_version")
    if not isinstance(schema_version, int) or schema_version <= 0:
        errors.append(f"{asset_path}: meta.schema_version must be positive int")

    model_scope = meta_obj.get("model_scope")
    if not isinstance(model_scope, list) or not model_scope or not all(isinstance(v, str) and v.strip() for v in model_scope):
        errors.append(f"{asset_path}: meta.model_scope must be non-empty string list")

    locale = meta_obj.get("locale")
    if locale is not None and not isinstance(locale, str):
        errors.append(f"{asset_path}: meta.locale must be string or null")

    return prompt_key, meta_obj


def _validate_variable_schema(asset_path: Path, payload: dict, errors: list[str]) -> dict[str, str]:
    schema_obj = payload.get("variables_schema")
    if not isinstance(schema_obj, dict):
        errors.append(f"{asset_path}: missing or invalid 'variables_schema' object")
        return {}

    validated: dict[str, str] = {}
    for name, kind in schema_obj.items():
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{asset_path}: variables_schema has invalid variable name '{name}'")
            continue
        if not isinstance(kind, str) or kind not in VALID_VARIABLE_KINDS:
            errors.append(
                f"{asset_path}: variables_schema.{name} has invalid kind '{kind}', "
                f"expected one of {sorted(VALID_VARIABLE_KINDS)}"
            )
            continue
        validated[name] = kind
    return validated


def _validate_variants(
    asset_path: Path,
    payload: dict,
    variables_schema: dict[str, str],
    errors: list[str],
) -> None:
    variants = payload.get("variants")
    if not isinstance(variants, list) or not variants:
        errors.append(f"{asset_path}: 'variants' must be a non-empty list")
        return

    seen_variant_ids: set[str] = set()
    used_vars_across_variants: set[str] = set()
    text_variables = {name for name, kind in variables_schema.items() if kind == "text"}

    for index, variant in enumerate(variants):
        label = f"{asset_path}: variants[{index}]"
        if not isinstance(variant, dict):
            errors.append(f"{label} must be an object")
            continue

        variant_id = variant.get("variant_id")
        if not isinstance(variant_id, str) or not variant_id.strip():
            errors.append(f"{label}.variant_id must be non-empty string")
            continue
        if variant_id in seen_variant_ids:
            errors.append(f"{label}.variant_id '{variant_id}' is duplicated")
        seen_variant_ids.add(variant_id)

        weight = variant.get("weight")
        if not isinstance(weight, int) or weight <= 0:
            errors.append(f"{label}.weight must be positive int")

        system_template = variant.get("system_template")
        user_template = variant.get("user_template")
        if not isinstance(system_template, str) or not system_template.strip():
            errors.append(f"{label}.system_template must be non-empty string")
            continue
        if not isinstance(user_template, str) or not user_template.strip():
            errors.append(f"{label}.user_template must be non-empty string")
            continue

        try:
            system_vars = _template_variables(system_template)
            user_vars = _template_variables(user_template)
        except TemplateSyntaxError as exc:
            errors.append(f"{label} has invalid Jinja syntax: {exc.message} (line {exc.lineno})")
            continue

        used_vars = system_vars | user_vars
        used_vars_across_variants.update(used_vars)

        unknown_vars = sorted(v for v in used_vars if v not in variables_schema)
        if unknown_vars:
            errors.append(f"{label} references undeclared variables: {unknown_vars}")

        non_text_vars = sorted(v for v in used_vars if variables_schema.get(v) != "text")
        if non_text_vars:
            errors.append(
                f"{label} references non-text variables in template: {non_text_vars}. "
                "Only text variables can be rendered in templates."
            )

    unused_text_vars = sorted(v for v in text_variables if v not in used_vars_across_variants)
    if unused_text_vars:
        errors.append(f"{asset_path}: text variables declared but never used in templates: {unused_text_vars}")


def validate_prompt_assets(repo_root: Path, files: Sequence[str] | None = None) -> list[str]:
    errors: list[str] = []
    prompt_files = _iter_prompt_files(repo_root, files)
    if not prompt_files:
        errors.append(f"{repo_root / 'configs' / 'prompts'}: no prompt JSON files found")
        return errors

    seen_prompt_keys: set[str] = set()
    for asset_path in prompt_files:
        if not asset_path.exists():
            errors.append(f"{asset_path}: file does not exist")
            continue
        try:
            payload = json.loads(asset_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{asset_path}: invalid JSON: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{asset_path}: root must be JSON object")
            continue

        prompt_key, _meta_obj = _validate_meta(asset_path, payload, errors)
        if prompt_key:
            if prompt_key in seen_prompt_keys:
                errors.append(f"{asset_path}: duplicated prompt_key '{prompt_key}' across assets")
            seen_prompt_keys.add(prompt_key)

        variables_schema = _validate_variable_schema(asset_path, payload, errors)
        _validate_variants(asset_path, payload, variables_schema, errors)
    return errors


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pre-flight validator for prompt assets in configs/prompts/*.json")
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="Specific files to validate. Defaults to all configs/prompts/*.json",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    errors = validate_prompt_assets(repo_root, files=args.files)
    if errors:
        print("Prompt asset pre-flight check failed:")
        for item in errors:
            print(f" - {item}")
        return 1

    validated = _iter_prompt_files(repo_root, args.files)
    print(f"Prompt asset pre-flight check passed ({len(validated)} file(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
