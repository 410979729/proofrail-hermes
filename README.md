# LoopCraft

LoopCraft is a Hermes-native **loop engineering** runtime for AI agents. It helps agents work more like careful engineers by turning tool use into a visible observe → plan → act → verify → closeout loop.

The repository still ships the Python package and Hermes plugin key as `proofrail` for compatibility with existing installs, but the product direction and user-facing name are **LoopCraft**.

If you're searching for a **Codex harness**, a **Claude Code harness**, or a general **agent harness** for Hermes, LoopCraft is built for that job: it wraps real tool execution with evidence-first edits, verify-after-mutation workflow, high-risk command handling, long-task context anchors, and Superpowers-inspired methodology cues.

It adds a repeatable execution process around tool use:

- check the relevant skill/methodology before acting
- record advisory risk before mutating existing files or local state
- remind the agent to validate after changes before stacking more mutation
- keep strict hard-block behavior available as an explicit compatibility mode
- track session workflow state with **Observe / Execute / Review** plus LoopCraft loop steps
- detect and handle high-risk commands with extra scrutiny
- summarize oversized tool output before it pollutes model context
- preserve task anchors so long runs lose less state

The goal is simple: fewer blind edits, fewer unverified claims, and more reliable loop-engineered agent execution inside Hermes.

## Why this exists

The same model can feel very different in different agent runtimes.

In one tool it behaves like a chatbot. In another it starts acting more like an engineer: it inspects first, gathers evidence before changing anything, validates after edits, and corrects itself when something fails.

LoopCraft focuses on that execution layer.

It does not try to replace the model. It changes how the agent works during real tool use: how it chooses a method, how it observes, how it plans the smallest action, how it executes, how it validates, and how it self-corrects.

For Hermes, that means a runtime plugin that can:

- record advisory risk for existing-file edits when nearby evidence is missing
- remind the agent to validate after mutation instead of silently stacking changes
- track whether the session is in **Observe**, **Execute**, or **Review**
- flag and handle high-risk command patterns
- reduce context pollution with large-output summarization
- push the agent toward verification and self-correction

## Current status

- Version: `v0.0.11`
- Product name: **LoopCraft**
- Compatibility package/plugin key: `proofrail`
- Host: **Hermes Agent plugin hooks**
- Language: **Python**

> Version note: the GitHub release/tag line is `v0.0.11`, while the Python package and wheel version is `0.0.11` to follow PEP 440. They refer to the same release.

The current main branch is the `v0.0.11` line: LoopCraft is the user-facing product name, while the package/plugin key remains `proofrail` for compatibility. Default workflow risks are recorded as compact runtime guidance, while `enforcement_mode: strict` preserves the older hard-block/cooperative-mode behavior for operators that want it.

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

- **LoopCraft loop panel** — inject a compact loop engineering cycle: skill/methodology check → observe → plan → act → verify → closeout
- **Superpowers-inspired methodology cues** — adapt the useful parts of composable skill workflows (method selection, bite-sized plans, TDD/verification discipline, review/closeout) into Hermes runtime feedback rather than copying Claude-only plugin mechanics
- **non-blocking workflow guidance** — missing evidence, pending verification, broad evidence, and repeated low-signal probes create actionable guidance by default instead of stopping the tool call
- **verify after mutation** — validate changes before continuing
- **strict compatibility mode** — `enforcement_mode: strict` preserves the older hard-block cooperative modes for deployments that need stricter enforcement
- **mode-specific task handoffs** — inject collaboration-framed task panels so the next legal move feels like progress instead of punishment
- **low-signal probe advisories** — warn on repeated no-progress probing loops by default; strict mode can still block them
- **gray-area classifier fallback + mode mapping** — when structured output is unsupported, fall back to rule-based classification; when the classifier does intervene, map it into cooperative runtime modes
- **cleanup reminder only** — LoopCraft never deletes files automatically; closeout guidance asks the agent to report cleanup status and artifact categorization/classification
- **dangerous command audit** — detect high-risk commands and surface them back into reasoning context
- **large output summarization** — compress oversized tool output before reinjection while preserving diagnostic lines from omitted middle sections
- **session-scoped workflow state** — maintain Observe / Execute / Review phase per session
- **audit trail** — JSONL audit events for preflight, mutation, validation, forced-mode transitions, forward-progress reopen events, dangerous commands, and summarization
- **task ledger** — session-level record of evidence, mutations, validation, touched files, and final state
- **validation suggestions** — inject the narrowest plausible verification hints from touched files and command shape

## Current runtime rules

1. **Default `enforcement_mode` is `advisory`**: workflow risks are recorded and injected as compact next-action cards, but normal tool calls continue.
2. **Strict compatibility is explicit**: set `enforcement_mode: strict` to restore the older hard-block cooperative modes for missing evidence, pending verification, broad evidence, and repeated low-signal probes.
3. **Dangerous terminal commands are policy-driven**: `warn` creates advisory + audit + allow in advisory mode, `allow` audits + allows, `block` blocks, and `approve` fail-closes in Hermes until a real approval route exists. `guarded` mode hard-blocks critical dangerous commands under `warn`.
4. **Validation policy defaults to `batch`** with `mutation_batch_max: 5`; `after_each_mutation` and `off` are also supported. In strict mode, `batch` blocks only at the batch limit, while `after_each_mutation` blocks the next mutation immediately.
5. **Large tool output is summarized through `transform_tool_result`** while retaining actionable diagnostics from omitted middle sections.
6. **`pre_llm_call` injects phase-aware runtime context or compact advisory cards**.
7. **After changes, the plugin injects touched files, validation hints, and final evidence-report requirements**.
8. **Classifier interventions can still route the runtime into `change_strategy` or `user_choice` instead of leaving the model to guess the shortest valid next step; classifier `block` / `ask_user` only hard-block in strict mode**.
9. **Successful validation explicitly reopens forward progress and emits a semantic audit event**.

## Configuration

The default configuration is usable as-is and optimized for autonomous execution: `enforcement_mode` defaults to `advisory`, so workflow risks become compact next-action advisories rather than hard blockers. Dangerous commands default to `warn`, meaning they stay in autonomous mode with audit + follow-up verification expectations unless you choose `block`/`approve`.

If your Hermes build exposes `plugins.entries`, you can override settings like this:

```yaml
plugins:
  enabled:
    - proofrail
  entries:
    proofrail:
      enforcement_mode: advisory
      advisory_injection: compact
      validation_policy: batch
      mutation_batch_max: 5
      dangerous_command_action: warn
      summary_threshold_chars: 8000
      low_signal_block_threshold: 2
      audit_enabled: true
      audit_log_path: .proofrail/audit.jsonl
      llm_classifier_enabled: true
      # Leave provider/model unset to inherit the instance's current main model.
      llm_classifier_provider: null
      llm_classifier_model: null
      tool_aliases:
        shell: exec
        run_command: exec
        edit_file: write
        apply_patch: write
```

`llm_classifier_enabled: true` turns on the gray-area classifier path.

- Leave `llm_classifier_provider` and `llm_classifier_model` unset (or `null`) if you want the classifier to follow the instance's current main model automatically.
- Set both fields if you want the classifier to use a dedicated provider/model pair.

Supported tool categories are: `read`, `write`, `exec`, `search`, `network`, and `other`. See `docs/configuration.md` for details.

> Some built-in dangerous-command patterns include common infrastructure/network protection cases such as Tailscale stop/down/logout commands. These are opinionated defaults, not a claim that every deployment uses Tailscale.

## Testing and release hygiene

Core regression coverage currently includes:

- hook registration
- dangerous command detection in warn/audit mode
- default non-blocking guidance behavior for missing evidence, pending verification, dangerous-command warn mode, compact advisory injection, and diagnostic-preserving summaries
- strict compatibility blocking for existing files
- new-file creation allowance
- conservative `patch` mutation handling
- verification-before-next-mutation advisory/default behavior plus strict enforcement compatibility
- low-signal probe advisory/default behavior plus strict blocking compatibility
- large-output summarization
- phase-aware `pre_llm_call` injection
- explicit system-added / non-user provenance markers for injected plugin context
- session end/finalize cleanup
- audit log writing
- touched-file and validation-hint injection
- task ledger lifecycle
- LoopCraft loop engineering positioning and Superpowers-inspired methodology step exposure
- readback validation that clears `pending_verification` when the touched target is directly re-read
- blocked-tool-call feedback reinjected into later reasoning context
- summarize branding regression
- cooperative forced modes with allowed / forbidden action menus
- classifier structured-output fallback to rule-based gray-area review
- classifier-to-mode mapping for `change_strategy` and `user_choice`
- mode-specific collaboration handoff wording
- mode lifecycle audit for `validate_only` entry / clear
- block-driven mode-transition audit for `missing_evidence` and `low_signal_repeat`
- `forward_progress_reopened` semantics after successful validation
- shell-assignment and `/dev/null` redirection filtering so command parsing does not create phantom validation targets
- directory-target readback overlap so a child file inspection can clear a coarse directory validation target
- compatibility wording that keeps Proofrail handoffs actionable for agents (`Fastest valid next action`, allowed / forbidden menus, current-subtask framing)
- end-to-end behavior simulation and local self-smoke of the cooperative runtime path

Run the local verification lane with:

```bash
pytest -q \
  tests/test_advisory_runtime.py \
  tests/test_proofrail.py \
  tests/test_readback_validation_regression.py \
  tests/test_cooperative_modes.py \
  tests/test_classifier_fallback.py \
  tests/test_classifier_mode_mapping.py \
  tests/test_loopcraft_positioning.py \
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
- `docs/loopcraft-functional-roadmap.md`
- `DESIGN.md`
- `SECURITY.md`
- `CONTRIBUTING.md`

## Open-source positioning

This repository is intended to be:

- a Hermes-native runtime harness plugin for autonomous coding agents
- a public example of evidence / mutation / validation workflow discipline in a Python plugin
- a practical starting point for people looking for a Hermes **agent harness**, **Codex harness**, or **Claude Code harness** style execution layer
