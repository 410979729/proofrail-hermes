# Contributing

## Development

```bash
pytest -q tests/test_proofrail.py
rm -rf __pycache__ proofrail/__pycache__ tests/__pycache__ scripts/__pycache__ .pytest_cache
python scripts/check.release.py
python -m build --wheel
python scripts/verify.package.py
rm -rf __pycache__ proofrail/__pycache__ tests/__pycache__ scripts/__pycache__ build dist *.egg-info .pytest_cache
python scripts/check.release.py
```

## Ground Rules

- Keep the plugin Hermes-first; do not add cross-language bridges unless there is a strong host-level reason.
- Add or update a failing regression test before changing runtime behavior.
- Keep README honest about current capabilities and non-goals.
- Prefer small, reviewable modules over a growing monolithic entrypoint.

## Release Hygiene

Before opening a PR or publishing:

1. Run the focused pytest suite.
2. Run `python scripts/check.release.py`.
3. Build one wheel with `python -m build --wheel`.
4. Run `python scripts/verify.package.py`.
5. Clean generated artifacts and bytecode caches back out of the tree (`__pycache__`, `build`, `dist`, `*.egg-info`, `.pytest_cache`).
6. Re-run `python scripts/check.release.py`.
7. If behavior changes, update `CHANGELOG.md` and relevant docs.
