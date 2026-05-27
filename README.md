# Proofrail

Proofrail is a runtime harness plugin for Hermes that helps AI agents work more like careful engineers.

If you're searching for a **Codex harness**, a **Claude Code harness**, or a general **agent harness** for Hermes, Proofrail is built for that job: it wraps real tool execution with evidence-first edits, verify-after-mutation workflow, high-risk command handling, and long-task context anchors.

It adds a repeatable execution process around tool use:

- gather evidence before editing existing files or mutating local state
- validate after every change before the next mutation
- track session workflow state with **Observe / Execute / Review**
- detect and handle high-risk commands with extra scrutiny
- summarize oversized tool output before it pollutes model context
- preserve task anchors so long runs lose less state

The goal is simple: fewer blind edits, fewer unverified claims, and more reliable agent execution inside Hermes.

## Why this exists

The same model can feel very different in different agent runtimes.

In one tool it behaves like a chatbot. In another it starts acting more like an engineer: it inspects first, gathers evidence before changing anything, validates after edits, and corrects itself when something fails.

Proofrail focuses on that execution layer.

It does not try to replace the model. It changes how the agent works during real tool use: how it observes, how it executes, how it validates, and how it self-corrects.

For Hermes, that means a runtime plugin that can:

- block existing-file edits when there is no nearby evidence
- require validation before the next mutation
- track whether the session is in **Observe**, **Execute**, or **Review**
- flag and handle high-risk command patterns
- reduce context pollution with large-output summarization
- push the agent toward verification and self-correction

## Current status

- Version: `v0.0.1`
- Host: **Hermes Agent plugin hooks**
- Language: **Python**

> Version note: the GitHub release/tag line is `v0.0.1`, while the Python package and wheel version is `0.0.1` to follow PEP 440. They refer to the same release.

## Quick start

Install the unpacked plugin directory into:

```text
$HERMES_HOME/plugins/proofrail/
```

Then enable it in the target instance's `config.yaml`:

```yaml
plugins:
  enabled:
    - proofrail
```

If the target instance already has other plugins, append `proofrail` to the existing list instead of replacing the whole array.

## What it does at runtime

- **evidence before mutation** — inspect first, then edit
- **verify after mutation** — validate changes before continuing
- **low-signal probe blocking** — stop repeated no-progress probing loops
- **dangerous command audit** — detect high-risk commands and surface them back into reasoning context
- **large output summarization** — compress oversized tool output before reinjection
- **session-scoped workflow state** — maintain Observe / Execute / Review phase per session
- **audit trail** — JSONL audit events for preflight, mutation, validation, dangerous commands, and summarization
- **task ledger** — session-level record of evidence, mutations, validation, touched files, and final state
- **validation suggestions** — inject the narrowest plausible verification hints from touched files and command shape

## Current runtime rules

1. **Existing files cannot be modified without evidence**
2. **After a mutation, the next mutation must wait until validation runs**
3. **After repeated low-signal probes, the same no-progress loop is blocked**
4. **Dangerous terminal commands default to `warn/audit`, not a manual approval loop**
   - `approve` is currently **fail-closed** in Hermes: it blocks and tells the operator to confirm manually, then retry.
5. **Large tool output is summarized through `transform_tool_result`**
6. **`pre_llm_call` injects phase-aware runtime context**
7. **After changes, the plugin injects touched files, validation hints, and final evidence-report requirements**

## Configuration

The default configuration is usable as-is and optimized for autonomous execution: dangerous commands default to `warn`, meaning they stay in autonomous mode with audit + follow-up verification expectations, but they are still subject to the same evidence-before-mutation and verify-after-mutation guardrails as any other mutating command.

If your Hermes build exposes `plugins.entries`, you can override settings like this:

```yaml
plugins:
  enabled:
    - proofrail
  entries:
    proofrail:
      dangerous_command_action: warn
      summary_threshold_chars: 8000
      low_signal_block_threshold: 2
      audit_enabled: true
      audit_log_path: .proofrail/audit.jsonl
      tool_aliases:
        shell: exec
        run_command: exec
        edit_file: write
        apply_patch: write
```

Supported tool categories are: `read`, `write`, `exec`, `search`, `network`, and `other`. See `docs/configuration.md` for details.

> Some built-in dangerous-command patterns include common infrastructure/network protection cases such as Tailscale stop/down/logout commands. These are opinionated defaults, not a claim that every deployment uses Tailscale.

## Testing and release hygiene

Core regression coverage currently includes:

- hook registration
- dangerous command detection in warn/audit mode
- evidence-before-mutation blocking for existing files
- new-file creation allowance
- conservative `patch` mutation handling
- verification-before-next-mutation enforcement
- low-signal probe blocking
- large-output summarization
- phase-aware `pre_llm_call` injection
- session end/finalize cleanup
- audit log writing
- touched-file and validation-hint injection
- task ledger lifecycle
- summarize branding regression

Run the local verification lane with:

```bash
pytest -q tests/test_proofrail.py
rm -rf __pycache__ proofrail/__pycache__ tests/__pycache__ scripts/__pycache__ .pytest_cache
python scripts/check.release.py
python -m build --wheel
python scripts/verify.package.py
rm -rf __pycache__ proofrail/__pycache__ tests/__pycache__ scripts/__pycache__ build dist *.egg-info .pytest_cache
python scripts/check.release.py
```

## Security and current limits

Proofrail is a workflow harness, not an OS sandbox.

Current boundaries:

- terminal mutation detection is still **best-effort heuristic logic**, not a full shell parser
- dangerous command detection is pattern-based, not semantic shell interpretation
- audit logs may contain command text, paths, tool arguments, and short output previews
- wheel build success does **not** mean Hermes installs the plugin directly from wheel; the primary install shape is still the unpacked plugin directory

See also:

- `docs/architecture.md`
- `docs/configuration.md`
- `DESIGN.md`
- `SECURITY.md`
- `CONTRIBUTING.md`

## Open-source positioning

This repository is intended to be:

- a Hermes-native runtime harness plugin for autonomous coding agents
- a public example of evidence / mutation / validation workflow discipline in a Python plugin
- a practical starting point for people looking for a Hermes **agent harness**, **Codex harness**, or **Claude Code harness** style execution layer
