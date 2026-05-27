"""Best-effort success/failure classification for Hermes tool results."""

from __future__ import annotations

from typing import Any

from .constants import PLAIN_TEXT_FAILURE_PATTERNS
from .text_utils import parse_result_object

_NUMERIC_EXIT_KEYS = ("exitCode", "exit_code", "code", "returnCode", "returncode", "status")
_NUMERIC_HTTP_KEYS = ("statusCode", "httpStatus", "http_status")


def _first_numeric_field(result: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = result.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _looks_like_plain_text_failure(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(phrase in lowered for phrase in ("0 errors", "no errors", "without errors")) and not any(
        phrase in lowered for phrase in ("traceback", "permission denied", "command not found", "no such file or directory")
    ):
        return False
    return any(pattern.search(normalized) for pattern in PLAIN_TEXT_FAILURE_PATTERNS)


def get_tool_result_status(result: Any, error_text: str = "") -> str:
    if error_text.strip():
        return "failure"

    payload = parse_result_object(result)
    if not payload:
        if isinstance(result, str) and _looks_like_plain_text_failure(result):
            return "failure"
        return "unknown"

    exit_code = _first_numeric_field(payload, _NUMERIC_EXIT_KEYS)
    http_status = _first_numeric_field(payload, _NUMERIC_HTTP_KEYS)

    signal = payload.get("signal")
    if isinstance(signal, str) and signal.strip():
        return "failure"
    if isinstance(exit_code, int) and exit_code != 0:
        return "failure"
    if isinstance(http_status, int) and http_status >= 400:
        return "failure"
    if payload.get("success") is False or payload.get("ok") is False:
        return "failure"
    if isinstance(payload.get("error"), str) and payload.get("error", "").strip():
        return "failure"
    if payload.get("errors"):
        return "failure"

    if isinstance(exit_code, int):
        return "success"
    if payload.get("success") is True or payload.get("ok") is True:
        return "success"
    if isinstance(http_status, int) and 200 <= http_status < 400:
        return "success"
    return "unknown"
