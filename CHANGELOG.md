# Changelog

## v0.0.9 - 2026-06-11

- Position the project as **LoopCraft**, a Hermes-native loop engineering runtime, while retaining the `proofrail` package/plugin key for compatibility.
- Add `docs/loopcraft-functional-roadmap.md`, a feature roadmap grounded in Superpowers, Codex, Claude Code, Cline, Roo Code, Aider, Continue, and OpenHands patterns.
- Add an advisory-only LoopCraft task-understanding snapshot plus an Agent Self-Routing Checkpoint that prompts the primary agent to decide `intent / domain / risk / stage / next` itself, without strong control or tool-permission effects.
- Mark generated LoopCraft/Proofrail context as non-user, non-memory-provider input, explicitly warning agents not to store it in long-term memory, SQL, or scope-recall as a user fact.
- Add closeout cleanup to final-report requirements so agents must report temporary artifacts deleted and retained backups/artifacts after mutations.
- Reframe reminders as assistive guidance: agents should evaluate them against user intent and live evidence, follow applicable reminders, and state why when a reminder is stale or wrong instead of silently routing around it.

## v0.0.8 - 2026-06-10

- Fix Windows command-line switch parsing in validation target extraction so commands such as `schtasks /End /TN ...` no longer register `/End`, `/TN`, or `/F` as phantom touched paths and cannot deadlock `pending_verification`.
- Keep ordinary POSIX absolute paths such as `/tmp` eligible as validation targets outside Windows command contexts.

## v0.0.7 - 2026-06-05

- Complete advisory-first cleanup for classifier `block` / `ask_user`, stale `validate_only` forced modes, guarded critical dangerous commands, advisory ignored audit events, validation-policy batch semantics, advisory injection modes, default tool aliases, and unknown-target mutating exec advisories.

## v0.0.6 - 2026-06-04

- Make `advisory` the default enforcement mode: missing evidence, pending verification, broad evidence, and repeated low-signal probes now record actionable advisories and allow tool calls instead of hard-blocking by default.
- Preserve the older strict cooperative runtime as explicit `enforcement_mode="strict"` compatibility, with legacy hard-block tests now opting into strict mode.
- Add advisory configuration controls: `enforcement_mode`, `advisory_injection`, `validation_policy`, and `mutation_batch_max`; map legacy `immediate` validation policy to `after_each_mutation` and clamp mutation batches to 1–20.
- Record structured advisory state and audit events with severity, target, fastest next action, allowed next actions, risk-if-ignored, source/tool/command metadata, and whether strict mode would have blocked.
- Change dangerous-command `warn` semantics to advisory + audit + allow, while keeping `block` hard-blocking and `approve` fail-closed until Hermes exposes a real approval route.
- Add compact advisory prompt injection and expose advisory/batch state through `explain_state()` for runtime diagnostics.
- Preserve diagnostic lines from omitted middle sections when summarizing large outputs, including failures, errors, tracebacks, exceptions, and assertion lines.
- Ignore inline Python source passed through `python -c` when extracting validation targets, while still preserving real Python script paths.

## v0.0.5 - 2026-06-02

- Harden cooperative `validate_only` recovery so shell assignment tokens (`NAME=/path`) and suppression redirects (`2>/dev/null`) are not recorded as phantom touched paths, while real output redirections still produce validation targets.
- Allow directory-level pending-verification targets to be cleared by direct child-path readback, preventing impossible validation loops when command heuristics can only identify a coarse directory target.
- Restore compatibility wording in Proofrail handoff panels (`Fastest valid next action`, allowed/forbidden action menus, and “current subtask” framing) so blocked tool calls remain actionable for agents.
- Fix the `ask_user` / `user_choice` approval flow so an explicit user confirmation can approve exactly one matching mutation, consume that approval once, and avoid getting clobbered by unrelated follow-up mutations.
- Add regression coverage for clarify-driven approval consumption and tighten the phase-6 smoke script so it can import the local package reliably when run from `scripts/`.

## v0.0.4 - 2026-06-01

- Promote the cooperative runtime path into the formal `v0.0.4` / `0.0.4` release line instead of leaving it as main-branch-only polish on top of `v0.0.3`.
- Add explicit forced modes (`gather_target_evidence`, `validate_only`, `change_strategy`, `user_choice`) with collaboration-framed handoff panels, allowed/forbidden next actions, and clearer smallest-next-step guidance.
- Add classifier fallback from unsupported structured output into `RuleBasedGrayAreaClassifier`, plus classifier-to-mode mapping so gray-area interventions become concrete runtime submodes.
- Emit cooperative-runtime audit semantics including `forced_mode_transition` and `forward_progress_reopened` when validation clears the review lane.
- Expand regression and smoke coverage with the cooperative-runtime suites and `scripts/phase6.live.smoke.py`; extend CI coverage to Python 3.11 and 3.12.
- Ignore local workspace-only agent files (`AGENTS.md`, `BOOTSTRAP.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `IDENTITY.md`, `.openclaw/`) so the repository can be published without private workspace scaffolding.

## v0.0.3 - 2026-05-30

- Graduate from internal experimental builds into the formal public `v0.0.3` / `0.0.3` release line.
- Remove `-dev` / `-exp` version suffixes from plugin metadata, runtime labels, and audit identifiers.
- Tighten package verification (`scripts/verify.package.py`) to match the current release version instead of a stale snapshot.
- No behavioral changes — hooks, validation logic, and dangerous-command policy are identical to the v0.0.2 runtime.

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
