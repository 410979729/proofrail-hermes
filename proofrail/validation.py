"""Validation-suggestion helpers derived from touched files and commands."""

from __future__ import annotations

import re
import shlex
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
_SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_REDIRECT_PREFIX_RE = re.compile(r"^(?P<op>\d*(?:>>?|<<?)|&>|\d*>&)(?P<target>.*)$")
_PYTHON_EXECUTABLE_RE = re.compile(r"^(?:python(?:\d+(?:\.\d+)?)?|pythonw(?:\d+(?:\.\d+)?)?|pypy(?:\d+(?:\.\d+)?)?)$")
_WINDOWS_STYLE_SWITCH_RE = re.compile(r"^/[A-Za-z][A-Za-z0-9?]*$")
_HEREDOC_REDIRECT_RE = re.compile(r"<<-?\s*(?P<quote>['\"]?)(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)?(?P=quote)")
_WINDOWS_SWITCH_COMMANDS = {
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "reg",
    "reg.exe",
    "robocopy",
    "robocopy.exe",
    "sc",
    "sc.exe",
    "schtasks",
    "schtasks.exe",
    "taskkill",
    "taskkill.exe",
    "tasklist",
    "tasklist.exe",
    "wmic",
    "wmic.exe",
}


def changed_path_hints(tool_name: str, args: dict[str, object], command: str = "") -> list[str]:
    # For changed paths, cwd is context, not a mutated file.
    hints = get_path_hints(args, include_cwd=False)
    if hints:
        return hints
    if command:
        # Keep this deliberately conservative. We only extract obvious script/file
        # names for validation suggestions; this is not a shell parser.  Shell
        # syntax tokens are filtered so validate_only cannot get poisoned by
        # phantom targets such as ``KEY=/path`` or ``2>/dev/null``.
        return _command_path_hints(command)
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


def _strip_heredoc_bodies(command: str) -> str:
    """Remove here-doc stdin bodies before shell-token path extraction.

    The body of ``python - <<'PY' ... PY`` is source code, not shell argv.
    Treating it as ordinary shell tokens poisoned strict validation with phantom
    targets such as ``out.write_bytes(b`` or ``Image.Resampling.LANCZOS``.
    """
    lines = command.splitlines()
    if not lines:
        return command
    kept: list[str] = []
    pending_delimiters: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if pending_delimiters:
            stripped = line.strip()
            if stripped == pending_delimiters[0]:
                pending_delimiters.pop(0)
            index += 1
            continue
        kept.append(line)
        delimiters = [match.group("delimiter") for match in _HEREDOC_REDIRECT_RE.finditer(line) if match.group("delimiter")]
        if delimiters:
            pending_delimiters.extend(str(delimiter) for delimiter in delimiters)
        index += 1
    return "\n".join(kept)


def _command_path_hints(command: str) -> list[str]:
    command = _strip_heredoc_bodies(command)
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    out: list[str] = []
    seen: set[str] = set()
    python_arg_mode = False
    skip_next_python_code = False
    windows_switch_mode = bool(parts and _is_windows_switch_command(parts[0]))
    for raw_part in parts:
        if skip_next_python_code:
            skip_next_python_code = False
            python_arg_mode = False
            continue

        if _is_python_executable_token(raw_part):
            # The interpreter itself is context, not a touched path. Enter a
            # narrow Python argv mode so ``python -c '<code with /paths>'``
            # cannot poison validate_only with the inline source string, while
            # keeping real script paths such as ``python scripts/check.py``.
            python_arg_mode = True
            continue

        if python_arg_mode:
            if raw_part == "-c":
                skip_next_python_code = True
                continue
            if raw_part.startswith("-c") and len(raw_part) > 2:
                python_arg_mode = False
                continue
            if raw_part.startswith("-"):
                continue
            python_arg_mode = False

        for candidate in _path_candidates_from_shell_token(raw_part, ignore_windows_switches=windows_switch_mode):
            if candidate and candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out


def _is_python_executable_token(raw_value: str) -> bool:
    value = raw_value.strip().strip("'\"")
    return bool(_PYTHON_EXECUTABLE_RE.fullmatch(Path(value).name))


def _is_windows_switch_command(raw_value: str) -> bool:
    value = raw_value.strip().strip("'\"")
    return Path(value).name.lower() in _WINDOWS_SWITCH_COMMANDS


def _is_windows_style_switch(value: str) -> bool:
    return bool(_WINDOWS_STYLE_SWITCH_RE.fullmatch(value))


def _path_candidates_from_shell_token(raw_value: str, *, ignore_windows_switches: bool = False) -> list[str]:
    value = raw_value.strip().strip("'\"`).,;(){}[]")
    if not value or value in {"|", "||", "&", "&&", ";"}:
        return []
    if value.startswith(("http://", "https://")):
        return []
    if value.startswith("-"):
        return []
    if ignore_windows_switches and _is_windows_style_switch(value):
        return []
    # Environment / shell assignment prefixes are context, not touched paths.
    # Keep the whole token ignored so ``PLUGIN=/path`` cannot become either
    # ``PLUGIN=/path`` or ``/path`` as a pending-verification target.
    if _SHELL_ASSIGNMENT_RE.match(value):
        return []
    if value.startswith(("$", "${")):
        return []

    redirect_match = _REDIRECT_PREFIX_RE.match(value)
    if redirect_match:
        target = redirect_match.group("target").strip().strip("'\"")
        if not target or target.startswith(("&", "$")) or target == "/dev/null":
            return []
        value = target

    wrapper_match = re.fullmatch(r"(?:PosixPath|WindowsPath|Path)\((?P<quote>['\"])(?P<inner>.*?)(?P=quote)\)", value)
    if wrapper_match:
        value = wrapper_match.group("inner")
    if value == "/dev/null" or value.startswith("/dev/fd/"):
        return []
    if _looks_like_path(value):
        return [value]
    return []


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
