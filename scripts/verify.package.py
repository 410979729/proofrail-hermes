from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def fail(message: str) -> None:
    print(f"[package-verify] FAIL: {message}")
    raise SystemExit(1)


def main() -> None:
    wheels = sorted(DIST.glob('*.whl'))
    if not wheels:
        fail('no wheel found under dist/')
    if len(wheels) != 1:
        fail(f'expected exactly one wheel, found {len(wheels)}: {[w.name for w in wheels]}')

    wheel = wheels[0]
    print(f'[package-verify] wheel={wheel.name}')
    required = {
        'proofrail/__init__.py',
        'proofrail/plugin.py',
        'proofrail/constants.py',
        'proofrail/settings.py',
        'proofrail/task_ledger.py',
        'proofrail/validation.py',
    }

    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
        missing = sorted(required - names)
        if missing:
            fail(f'missing wheel entries: {missing}')

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(wheel) as zf:
            zf.extractall(tmp_path)
        sys.path.insert(0, str(tmp_path))
        try:
            import proofrail  # noqa: F401
            from proofrail import build_runtime_hooks, register  # noqa: F401
            from proofrail.constants import PLUGIN_NAME, PLUGIN_VERSION
        except Exception as exc:
            fail(f'import smoke failed: {exc!r}')
        finally:
            try:
                sys.path.remove(str(tmp_path))
            except ValueError:
                pass

        if PLUGIN_NAME != 'proofrail':
            fail(f'unexpected plugin name constant: {PLUGIN_NAME!r}')
        if PLUGIN_VERSION != 'v0.0.1':
            fail(f'unexpected plugin version constant: {PLUGIN_VERSION!r}')

    print('[package-verify] ok')


if __name__ == '__main__':
    main()
