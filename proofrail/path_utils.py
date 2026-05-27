"""Path-hint extraction helpers for write detection and audit labels."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_PATH_FIELDS = ("path", "filePath", "file", "target", "cwd")
_MUTATION_PATH_FIELDS = ("path", "filePath", "file", "target")
_PATCH_FILE_RE = re.compile(r"^\*\*\*\s+(?:Update|Delete|Add)\s+File:\s+(.+?)\s*$", re.MULTILINE)


def extract_patch_paths(patch_text: str) -> list[str]:
    if not isinstance(patch_text, str) or not patch_text.strip():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _PATCH_FILE_RE.finditer(patch_text):
        candidate = match.group(1).strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def get_path_hints(
    input_data: dict[str, Any],
    derived_paths: list[str] | tuple[str, ...] | None = None,
    *,
    include_cwd: bool = True,
) -> list[str]:
    fields = _PATH_FIELDS if include_cwd else _MUTATION_PATH_FIELDS
    seen: set[str] = set()
    out: list[str] = []
    for value in list(derived_paths or []):
        if isinstance(value, str):
            candidate = value.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    for field in fields:
        value = input_data.get(field)
        if isinstance(value, str):
            candidate = value.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    patch_text = input_data.get("patch")
    if isinstance(patch_text, str):
        for candidate in extract_patch_paths(patch_text):
            if candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out


def path_exists_from_hint(path_hint: str, base_dir: str | None = None) -> bool:
    if not path_hint or path_hint.startswith(("http://", "https://")):
        return False
    path = Path(path_hint).expanduser()
    if not path.is_absolute():
        path = (Path(base_dir).expanduser() if base_dir else Path.cwd()) / path
    return path.exists()


def mutation_base_dir(input_data: dict[str, Any], base_dir: str | None = None) -> str | None:
    """Return the directory used to resolve relative mutation targets.

    ``cwd`` is context, not a touched file. It should not appear in the list of
    changed paths, but it is still the right base directory for resolving a
    relative path such as ``src/app.py`` or ``app.py``.
    """
    cwd = input_data.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return base_dir


def mutates_existing_path(
    input_data: dict[str, Any],
    derived_paths: list[str] | tuple[str, ...] | None = None,
    base_dir: str | None = None,
) -> bool:
    effective_base = mutation_base_dir(input_data, base_dir)
    return any(path_exists_from_hint(path_hint, effective_base) for path_hint in get_path_hints(input_data, derived_paths, include_cwd=False))
