from __future__ import annotations

import ast
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "proofrail"


def fail(message: str) -> None:
    print(f"[release-check] FAIL: {message}")
    raise SystemExit(1)


def parse_simple_plugin_yaml(path: pathlib.Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or ':' not in line:
            continue
        key, value = line.split(':', 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        if value.startswith((""", "'")) and value.endswith((""", "'")) and len(value) >= 2:
            value = value[1:-1]
        data[key] = value
    return data


def main() -> None:
    for path in [ROOT / "__init__.py", *PACKAGE.glob("*.py")]:
        try:
            ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            fail(f"Python syntax error in {path.relative_to(ROOT)}: {exc}")

    with (ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)
    package_version = project.get("project", {}).get("version")
    if not package_version:
        fail("pyproject.toml is missing project.version")

    plugin = parse_simple_plugin_yaml(ROOT / "plugin.yaml")
    if plugin.get("name") != "proofrail":
        fail("plugin.yaml name mismatch")
    plugin_version = plugin.get("version")
    if not plugin_version:
        fail("plugin.yaml is missing version")
    if plugin_version != f"v{package_version}":
        fail("plugin.yaml version must be the public tag form of pyproject.toml (expected v<package_version>)")

    expected = {
        "__init__.py",
        "plugin.py",
        "constants.py",
        "models.py",
        "tooling.py",
        "text_utils.py",
        "path_utils.py",
        "result_status.py",
        "summarize.py",
        "session_state.py",
        "settings.py",
        "audit.py",
        "validation.py",
        "task_ledger.py",
        "task_understanding.py",
    }
    actual = {path.name for path in PACKAGE.glob("*.py")}
    missing = sorted(expected - actual)
    if missing:
        fail(f"missing package modules: {missing}")

    forbidden_paths = [
        ROOT / ".proofrail",
        ROOT / ".pytest_cache",
        ROOT / "build",
        ROOT / "dist",
    ]
    for forbidden in forbidden_paths:
        if forbidden.exists():
            fail(f"generated artifact should not be committed or packaged: {forbidden.relative_to(ROOT)}")

    for cache_dir in ROOT.rglob("__pycache__"):
        fail(f"generated artifact should not be committed or packaged: {cache_dir.relative_to(ROOT)}")

    for pattern in ("*.pyc", "*.pyo"):
        for compiled in ROOT.rglob(pattern):
            fail(f"generated artifact should not be committed or packaged: {compiled.relative_to(ROOT)}")

    for egg_info in ROOT.glob("*.egg-info"):
        fail(f"generated artifact should not be committed or packaged: {egg_info.relative_to(ROOT)}")

    print("[release-check] ok")


if __name__ == "__main__":
    main()
