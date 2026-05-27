"""Large-output summarization helpers used by transform_tool_result."""

from __future__ import annotations

from .constants import (
    MAX_SUMMARY_THRESHOLD_CHARS,
    MIN_SUMMARY_THRESHOLD_CHARS,
    SUMMARY_KEEP_HEAD,
    SUMMARY_KEEP_TAIL,
    SUMMARY_THRESHOLD_CHARS,
)


def clamp_summary_threshold(configured: int | None) -> int:
    if not isinstance(configured, int):
        return SUMMARY_THRESHOLD_CHARS
    return max(MIN_SUMMARY_THRESHOLD_CHARS, min(MAX_SUMMARY_THRESHOLD_CHARS, configured))


def summarize_large_output(text: str, threshold: int = SUMMARY_THRESHOLD_CHARS) -> str:
    if len(text) <= threshold:
        return text
    scale = min(1.0, threshold / SUMMARY_THRESHOLD_CHARS)
    head_keep = max(200, int(SUMMARY_KEEP_HEAD * scale))
    tail_keep = max(150, int(SUMMARY_KEEP_TAIL * scale))
    omitted = len(text) - head_keep - tail_keep
    return (
        f"{text[:head_keep]}\n\n"
        f"[... {omitted} chars omitted by proofrail ...]\n\n"
        f"{text[-tail_keep:]}"
    )
