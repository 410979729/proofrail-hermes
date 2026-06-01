# Contributing

## Development

```bash
pytest -q \
  tests/test_proofrail.py \
  tests/test_readback_validation_regression.py \
  tests/test_cooperative_modes.py \
  tests/test_classifier_fallback.py \
  tests/test_classifier_mode_mapping.py \
  tests/test_phase4_audit_and_wording.py \
  tests/test_phase5_mode_lifecycle.py \
  tests/test_phase6_behavior_simulation.py
rm -rf __pycache__ proofrail/__pycache__ tests/__pycache__ scripts/__pycache__ .pytest_cache
python3 scripts/check.release.py
python3 -m build --wheel
python3 scripts/verify.package.py
PYTHONPATH=. python3 scripts/phase6.live.smoke.py
rm -rf __pycache__ proofrail/__pycache__ tests/__pycache__ scripts/__pycache__ build dist *.egg-info .pytest_cache
python3 scripts/check.release.py
```

## Ground Rules

- Keep the plugin Hermes-first; do not add cross-language bridges unless there is a strong host-level reason.
- Add or update a failing regression test before changing runtime behavior.
- Keep README honest about current capabilities and non-goals.
- Prefer small, reviewable modules over a growing monolithic entrypoint.

## Release Hygiene

Before opening a PR or publishing:

1. Run the focused pytest suite.
2. Run `python3 scripts/check.release.py`.
3. Build one wheel with `python3 -m build --wheel`.
4. Run `python3 scripts/verify.package.py`.
5. Run `PYTHONPATH=. python3 scripts/phase6.live.smoke.py` when changing cooperative-mode behavior, classifier behavior, or validation lifecycles.
6. Clean generated artifacts and bytecode caches back out of the tree (`__pycache__`, `build`, `dist`, `*.egg-info`, `.pytest_cache`).
7. Re-run `python3 scripts/check.release.py`.
8. If behavior changes, update `CHANGELOG.md` and relevant docs.
