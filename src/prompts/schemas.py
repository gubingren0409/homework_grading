from dataclasses import dataclass
from typing import Dict, List, Literal, Mapping, Optional, Sequence


VariableKind = Literal["text", "image_base64", "image_url"]


@dataclass(frozen=True)
class PromptVariable:
    name: str
    kind: VariableKind
    value: str


@dataclass(frozen=True)
class PromptResolveRequest:
    prompt_key: str
    model: str
    trace_id: str
    bucket_key: str
    locale: Optional[str]
    variables: Sequence[PromptVariable]
    max_input_tokens: int
    reserve_output_tokens: int
    variant_hint: Optional[str] = None


@dataclass(frozen=True)
class PromptResolveResult:
    messages: List[Dict[str, object]]
    asset_version: str
    variant_id: str
    token_estimate: int
    cache_level: Literal["L1", "L2", "SOURCE", "LKG"]


@dataclass(frozen=True)
class PromptAssetMeta:
    prompt_key: str
    version: str
    version_hash: str
    model_scope: Sequence[str]
    locale: Optional[str]
    schema_version: int


@dataclass(frozen=True)
class PromptVariant:
    variant_id: str
    weight: int
    system_template: str
    user_template: str


@dataclass(frozen=True)
class PromptAsset:
    meta: PromptAssetMeta
    variables_schema: Mapping[str, VariableKind]
    variants: Sequence[PromptVariant]


@dataclass(frozen=True)
class PromptInvalidationEvent:
    prompt_key: str
    version_hash: str
    source: str


@dataclass(frozen=True)
class PromptLKGSnapshot:
    prompt_key: str
    version_hash: str
    result: PromptResolveResult


@dataclass(frozen=True)
class RefreshReport:
    checked_assets: int
    refreshed_assets: int
    invalidated_assets: int
