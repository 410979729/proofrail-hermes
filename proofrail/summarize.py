"""Large-output summarization helpers used by transform_tool_result."""

from __future__ import annotations

import re

from .constants import (
    MAX_SUMMARY_THRESHOLD_CHARS,
    MIN_SUMMARY_THRESHOLD_CHARS,
    PLAIN_TEXT_FAILURE_PATTERNS,
    SUMMARY_KEEP_HEAD,
    SUMMARY_KEEP_TAIL,
    SUMMARY_THRESHOLD_CHARS,
)

_DIAGNOSTIC_LINE_RE = re.compile(r"\b(FAILED|ERROR|Exception|AssertionError|Traceback|failure|failed|fatal|panic)\b", re.I)


def clamp_summary_threshold(configured: int | None) -> int:
    if not isinstance(configured, int):
        return SUMMARY_THRESHOLD_CHARS
    return max(MIN_SUMMARY_THRESHOLD_CHARS, min(MAX_SUMMARY_THRESHOLD_CHARS, configured))


def _preserved_diagnostic_lines(text: str, head_keep: int, tail_keep: int, *, limit: int = 8) -> list[str]:
    middle_start = min(head_keep, len(text))
    middle_end = max(middle_start, len(text) - tail_keep)
    middle = text[middle_start:middle_end]
    out: list[str] = []
    for raw_line in middle.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _DIAGNOSTIC_LINE_RE.search(line) or any(pattern.search(line) for pattern in PLAIN_TEXT_FAILURE_PATTERNS):
            out.append(line[:500])
        if len(out) >= limit:
            break
    return out


def summarize_large_output(text: str, threshold: int = SUMMARY_THRESHOLD_CHARS) -> str:
    if len(text) <= threshold:
        return text
    scale = min(1.0, threshold / SUMMARY_THRESHOLD_CHARS)
    head_keep = max(200, int(SUMMARY_KEEP_HEAD * scale))
    tail_keep = max(150, int(SUMMARY_KEEP_TAIL * scale))
    omitted = len(text) - head_keep - tail_keep
    diagnostics = _preserved_diagnostic_lines(text, head_keep, tail_keep)
    diagnostic_block = ""
    if diagnostics:
        diagnostic_block = "\n\n[proofrail preserved diagnostics from omitted middle]\n" + "\n".join(diagnostics)
    return (
        f"{text[:head_keep]}\n\n"
        f"[... {omitted} chars omitted by proofrail ...]"
        f"{diagnostic_block}\n\n"
        f"{text[-tail_keep:]}"
    )
