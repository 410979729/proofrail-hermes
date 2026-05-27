from __future__ import annotations

import ast
import pathlib
import sys
import tomllib

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "proofrail"


def fail(message: str) -> None:
    print(f"[release-check] FAIL: {message}")
    raise SystemExit(1)


def main() -> None:
    for path in [ROOT / "__init__.py", *PACKAGE.glob("*.py")]:
        try:
            ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            fail(f"Python syntax error in {path.relative_to(ROOT)}: {exc}")

    with (ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)
    version = project.get("project", {}).get("version")
    if not version:
        fail("pyproject.toml is missing project.version")

    if yaml is not None:
        plugin = yaml.safe_load((ROOT / "plugin.yaml").read_text())
        if plugin.get("name") != "proofrail":
            fail("plugin.yaml name mismatch")
        if plugin.get("version") != version:
            fail("plugin.yaml version does not match pyproject.toml")

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
