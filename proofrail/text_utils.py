"""Text extraction and normalization utilities for heterogeneous tool results."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


_TEXT_FIELDS = (
    "text",
    "message",
    "output",
    "content",
    "result",
    "stdout",
    "stderr",
    "summary",
    "title",
)


def extract_text_fragments(value: Any, depth: int = 0) -> list[str]:
    if value is None or depth > 5:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(extract_text_fragments(item, depth + 1))
        return out
    if not isinstance(value, dict):
        return []
    out: list[str] = []
    for field in _TEXT_FIELDS:
        if field in value:
            out.extend(extract_text_fragments(value[field], depth + 1))
    return out


def extract_text_from_tool_result(result: Any) -> str:
    payload = parse_result_object(result)
    if payload:
        text = "\n".join(extract_text_fragments(payload))
        return "\n\n".join(part for part in text.split("\n\n") if part).strip()
    if isinstance(result, str):
        return result.strip()
    text = "\n".join(extract_text_fragments(result))
    return "\n\n".join(part for part in text.split("\n\n") if part).strip()


def normalize_signal_text(text: str) -> str:
    return " ".join(text.lower().split()).strip()


def compact_label(text: str, max_length: int = 160) -> str:
    return " ".join(text.split()).strip()[:max_length]


def first_string_field(input_data: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_result_object(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            loaded = json.loads(result)
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}
