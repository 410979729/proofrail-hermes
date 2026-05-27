"""Validation-suggestion helpers derived from touched files and commands."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .path_utils import get_path_hints
from .text_utils import compact_label


_PY_SUFFIXES = {".py"}
_JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
_SHELL_SUFFIXES = {".sh", ".bash", ".zsh"}
_YAML_SUFFIXES = {".yaml", ".yml"}
_JSON_SUFFIXES = {".json"}
_DOC_SUFFIXES = {".md", ".rst", ".txt"}


def changed_path_hints(tool_name: str, args: dict[str, object], command: str = "") -> list[str]:
    # For changed paths, cwd is context, not a mutated file.
    hints = get_path_hints(args, include_cwd=False)
    if hints:
        return hints
    if command:
        # Keep this deliberately conservative. We only extract obvious script/file
        # names for validation suggestions; this is not a shell parser.
        parts = [part.strip("'\"") for part in command.split()]
        return [part for part in parts if _looks_like_path(part)]
    return []


def suggest_validations(
    *,
    tool_name: str,
    args: dict[str, object],
    command: str = "",
    mutating_exec: bool = False,
) -> list[str]:
    suggestions: list[str] = []
    paths = changed_path_hints(tool_name, args, command)
    suffixes = {Path(path).suffix.lower() for path in paths}
    names = {Path(path).name.lower() for path in paths}

    if suffixes & _PY_SUFFIXES or "pyproject.toml" in names:
        suggestions.extend(["python -m py_compile <changed .py files>", "pytest -q"])
    if "plugin.yaml" in names or suffixes & _YAML_SUFFIXES:
        suggestions.append("python -c \"import yaml; yaml.safe_load(open('<changed yaml>'))\"")
    if "pyproject.toml" in names:
        suggestions.append("python -c \"import tomllib; tomllib.load(open('pyproject.toml','rb'))\"")
        suggestions.append("python scripts/check.release.py")
    if suffixes & _JSON_SUFFIXES:
        suggestions.append("python -m json.tool <changed json>")
    if suffixes & _SHELL_SUFFIXES:
        suggestions.append("bash -n <changed shell script>")
    if suffixes & _JS_SUFFIXES or {"package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock"} & names:
        suggestions.append("npm test or pnpm test, plus project lint/build if available")
    if suffixes and suffixes <= _DOC_SUFFIXES:
        suggestions.append("review rendered docs / links if docs changed only")
    if mutating_exec and not suggestions:
        suggestions.append("run the narrowest command that proves the mutation worked")

    return _unique(suggestions)


def summarize_paths(paths: Iterable[str], *, limit: int = 8) -> list[str]:
    out = [compact_label(path, 140) for path in paths if path]
    if len(out) <= limit:
        return out
    return [*out[:limit], f"... {len(out) - limit} more"]


def _looks_like_path(value: str) -> bool:
    if not value or value.startswith("-"):
        return False
    path = Path(value)
    return bool(path.suffix) or "/" in value


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
