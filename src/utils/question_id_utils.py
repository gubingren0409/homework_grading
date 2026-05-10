from __future__ import annotations

import re

_SUBQUESTION_RE = re.compile(r"^[（(](\d+)[)）]$")
_COMPACT_SUBQUESTION_RE = re.compile(r"^(\d+)\s*[（(](\d+)[)）]$")
_CIRCLED_SUBQUESTION_TOKENS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def is_subquestion_token(token: str) -> bool:
    normalized = token.strip()
    return bool(_SUBQUESTION_RE.fullmatch(normalized)) or normalized in _CIRCLED_SUBQUESTION_TOKENS


def extract_subquestion_slot(question_id: str) -> str | None:
    parts = [part for part in question_id.split("/") if part]
    if not parts:
        return None
    token = parts[-1].strip()
    if is_subquestion_token(token):
        match = _SUBQUESTION_RE.fullmatch(token)
        if match is None:
            return token
        return f"({match.group(1)})"
    compact_match = _COMPACT_SUBQUESTION_RE.fullmatch(token)
    if compact_match is None:
        return None
    return f"({compact_match.group(2)})"


def parent_question_id(question_id: str) -> str:
    parts = [part for part in question_id.split("/") if part]
    if not parts:
        return question_id
    if is_subquestion_token(parts[-1]):
        if len(parts) > 1:
            return "/".join(parts[:-1])
        return question_id
    compact_match = _COMPACT_SUBQUESTION_RE.fullmatch(parts[-1].strip())
    if compact_match is None:
        return question_id
    parent_parts = parts[:-1] + [compact_match.group(1)]
    return "/".join(parent_parts)


def last_numeric_question_token(question_id: str) -> int | None:
    for token in reversed(question_id.split("/")):
        normalized = token.strip()
        if normalized.isdecimal():
            return int(normalized)
        compact_match = _COMPACT_SUBQUESTION_RE.fullmatch(normalized)
        if compact_match is not None:
            return int(compact_match.group(1))
    return None


def question_token_order(token: str) -> int | None:
    normalized = token.strip()
    if normalized.isdecimal():
        return int(normalized)
    match = _SUBQUESTION_RE.fullmatch(normalized)
    if match is not None:
        return int(match.group(1))
    if normalized in _CIRCLED_SUBQUESTION_TOKENS:
        return _CIRCLED_SUBQUESTION_TOKENS.index(normalized) + 1
    return None
