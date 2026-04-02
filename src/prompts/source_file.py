import json
from pathlib import Path
from typing import Dict, Mapping

from src.prompts.exceptions import PromptAssetNotFound
from src.prompts.schemas import PromptAsset, PromptAssetMeta, PromptVariant


class FilePromptSource:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    async def get_asset(self, prompt_key: str, locale: str | None = None) -> PromptAsset:
        _ = locale
        path = self._base_dir / f"{prompt_key}.json"
        if not path.exists():
            raise PromptAssetNotFound(f"Prompt asset not found: {prompt_key}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        meta_raw = raw["meta"]
        variants_raw = raw["variants"]
        variables_schema = raw.get("variables_schema", {})
        meta = PromptAssetMeta(
            prompt_key=meta_raw["prompt_key"],
            version=meta_raw["version"],
            version_hash=meta_raw["version_hash"],
            model_scope=list(meta_raw.get("model_scope", [])),
            locale=meta_raw.get("locale"),
            schema_version=int(meta_raw["schema_version"]),
        )
        variants = [
            PromptVariant(
                variant_id=v["variant_id"],
                weight=int(v.get("weight", 1)),
                system_template=v["system_template"],
                user_template=v["user_template"],
            )
            for v in variants_raw
        ]
        return PromptAsset(meta=meta, variables_schema=variables_schema, variants=variants)

    async def list_assets_version_hash(self) -> Mapping[str, str]:
        result: Dict[str, str] = {}
        if not self._base_dir.exists():
            return result
        for file in self._base_dir.glob("*.json"):
            raw = json.loads(file.read_text(encoding="utf-8"))
            meta_raw = raw.get("meta", {})
            prompt_key = str(meta_raw.get("prompt_key", file.stem))
            version_hash = str(meta_raw.get("version_hash", ""))
            if prompt_key and version_hash:
                result[prompt_key] = version_hash
        return result
