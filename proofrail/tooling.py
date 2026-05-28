"""Tool classification and command-risk heuristics.

These helpers intentionally use conservative regular expressions instead of a
full shell parser. They are workflow signals for the harness, not a security
boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from .constants import DANGEROUS_PATTERNS, DEFAULT_TOOL_ALIASES, MUTATING_EXEC_PATTERNS, TOOL_CATEGORIES, VALIDATION_EXEC_PATTERNS


ToolCategory = Literal["read", "write", "exec", "search", "network", "other"]


def normalize_tool_name(tool_name: str) -> str:
    return (tool_name or "").strip().lower()


def _merged_aliases(tool_aliases: Mapping[str, str] | None = None) -> dict[str, str]:
    aliases = dict(DEFAULT_TOOL_ALIASES)
    for raw_name, raw_category in (tool_aliases or {}).items():
        name = normalize_tool_name(str(raw_name))
        category = str(raw_category or "").strip().lower()
        if name and category in TOOL_CATEGORIES:
            aliases[name] = category
    return aliases


def get_tool_category(tool_name: str, tool_aliases: Mapping[str, str] | None = None) -> ToolCategory:
    normalized = normalize_tool_name(tool_name)
    if normalized in TOOL_CATEGORIES:
        return cast(ToolCategory, normalized)
    return cast(ToolCategory, _merged_aliases(tool_aliases).get(normalized, "other"))


def get_exec_command(input_data: dict[str, object]) -> str:
    """Return the shell command or code body from common Hermes tool argument shapes."""
    for key in ("command", "cmd", "shell_command", "script", "input", "code"):
        value = input_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = input_data.get("args")
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return " ".join(value).strip()
    return ""


def is_dangerous_command(command: str) -> tuple[bool, str | None]:
    for pattern, label in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return True, label
    return False, None


def _looks_like_python_inline_write(command: str) -> bool:
    lowered = command.lower()
    if "open(" in command:
        compact = lowered.replace('"', "'")
        write_tokens = [
            ",'w'",
            ", 'w'",
            ",'a'",
            ", 'a'",
            ",'x'",
            ", 'x'",
            ",'wb'",
            ", 'wb'",
            ",'ab'",
            ", 'ab'",
            ",'xb'",
            ", 'xb'",
        ]
        if any(token in compact for token in write_tokens):
            return True
    write_markers = (
        ".write_text(",
        ".write_bytes(",
        ".writelines(",
        ".write(",
        ".open('w'",
        '.open("w"',
        ".open('a'",
        '.open("a"',
        ".open('x'",
        '.open("x"',
    )
    return any(marker in lowered for marker in write_markers)


def is_likely_mutating_exec(command: str) -> bool:
    return _looks_like_python_inline_write(command) or any(pattern.search(command) for pattern in MUTATING_EXEC_PATTERNS)


def is_likely_validation_exec(command: str) -> bool:
    """Return True for commands whose primary purpose is validation.

    A command that mutates the environment, such as `curl ... | sh`, should not
    be treated as validation even if it contains words like `curl` that can also
    be used for health checks.
    """
    return (not is_likely_mutating_exec(command)) and any(pattern.search(command) for pattern in VALIDATION_EXEC_PATTERNS)
