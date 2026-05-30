# Changelog

## v0.0.3 - 2026-05-30

- Promote the live Tian Shu build to the formal public `v0.0.3` / `0.0.3` release line.
- Remove the private experimental version suffix from plugin metadata and runtime constants.
- Align package verification with the current release version.

## v0.0.2 - 2026-05-28

- Mark `pre_llm_call` injected guidance, plugin state, and reminders as **system-added / generated / not user-provided** to reduce provenance confusion.
- Reinject the last blocked-tool-call reason/message into later reasoning context so the model can recover without guessing what Proofrail wanted.
- Treat direct readback of a touched target (for example `read_file` or `cat` on the changed path) as narrow validation that clears `pending_verification`.
- Broaden inline Python mutation detection in exec/code payloads, including `code` bodies and common `Path.write_text` / `write_bytes` / `.write(...)` patterns.
- Expand regression coverage for provenance labeling and readback-validation behavior; local verification now runs both `tests/test_proofrail.py` and `tests/test_readback_validation_regression.py`.

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
