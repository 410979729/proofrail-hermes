# Changelog

## v0.0.1 - 2026-05-27

- Establish the first public version line for Proofrail.
- Set the public package, docs, metadata, runtime labels, and examples to **Proofrail**.
- Keep the core autonomous coding harness features: evidence-before-mutation, verify-after-mutation, dangerous-command audit, large-output summarization, session task ledger, and validation suggestions.
- `approve` mode currently remains fail-closed: dangerous commands are blocked and require manual confirmation plus manual retry.
- release check blocks `.proofrail/`, `__pycache__/`, `.pytest_cache/`, `*.pyc`, and `*.pyo` from published artifacts.
- Add `scripts/verify.package.py` for wheel content inspection and import smoke verification.
- Current regression suite covers 31 behaviors around hook registration, dangerous command policy, config loading, low-signal blocking, task ledger, validation suggestions, summarization branding, and final report context.
- Use `v0.0.1` for the GitHub release/tag line and `0.0.1` for Python packaging metadata and wheel filenames.
- Document audit log sensitivity boundaries in README / configuration / security docs.
