# LoopCraft Architecture

LoopCraft is a **Hermes-native Python loop engineering runtime** for autonomous coding and operations agents. It implements small, testable runtime primitives in Hermes hook callbacks: evidence-first execution, verify-after-mutation, low-signal probe control, dangerous-command audit, large-output summarization, task self-routing reminders, cleanup-aware closeout, and self-review reminders.

The repository still installs as the Python package and Hermes plugin key `proofrail` for compatibility with existing deployments. User-facing docs, runtime positioning, and product language use **LoopCraft** unless the text is specifically referring to the compatibility package/key, audit path, or legacy strict-mode behavior.

## Layers

### 1. `plugin.py` — Hermes adapter and runtime coordinator

- Exposes `register(ctx)`.
- Reads settings and root dir from Hermes context.
- Registers lifecycle hooks.
- Coordinates runtime state, command/tool classifiers, audit events, validation suggestions, and context injection.

### 2. `settings.py` — configuration boundary

- Reads plugin config from common Hermes config shapes.
- Coerces and clamps values.
- Supports `enforcement_mode`, `advisory_injection`, `validation_policy`, `mutation_batch_max`, `dangerous_command_action`, `summary_threshold_chars`, `low_signal_block_threshold`, `audit_enabled`, `audit_log_path`, `llm_classifier_enabled`, `llm_classifier_provider`, `llm_classifier_model`, and `tool_aliases`.

### 3. `session_state.py` — workflow state machine

- Session store with TTL and max-size pruning.
- `observe` / `execute` / `review` phases.
- Evidence count, pending verification, low-signal state.
- Mutation count, validation count, dangerous command count.
- Touched files and validation suggestions.
- Locked `update()` path for atomic state transitions.

### 4. `audit.py` — append-only audit trail

- Writes JSONL events.
- Records session start/end, preflight decisions, high-risk command observations, tool results, and summarization events.
- Best-effort by design: audit failures never block the task.

### 5. `validation.py` — narrow validation suggestions

- Extracts obvious changed path hints.
- Suggests validation commands based on file types and known project files.
- Keeps the logic heuristic and transparent rather than pretending to be a build-system oracle.

### 6. `tooling.py` — tool and command classification

- Tool name normalization.
- Default + user-configured aliases.
- Dangerous command detection.
- Mutating exec detection.
- Validation exec detection.

### 7. `path_utils.py` — path evidence and mutation hints

- Extracts target paths from tool arguments and patch text.
- Resolves relative paths from `ctx.root_dir`.
- Checks whether a write-like operation touches existing paths.

### 8. `result_status.py`

Unifies success/failure/unknown detection for dict/JSON payloads and plain text failures.

### 9. `summarize.py` and `text_utils.py`

Pure utilities for large-output summarization and text extraction.

## Runtime model

```text
on_session_start
  -> create/snapshot session state
  -> audit session_start

pre_llm_call
  -> inject generated runtime context, phase, touched files, validation suggestions,
     dangerous command audit reminders, self-routing checkpoints, and final
     evidence/cleanup-report requirements

pre_tool_call
  -> classify tool and command
  -> record assistive workflow reminders by default
  -> block only in explicit compatibility strict/guarded paths
  -> dangerous commands default to warn/audit rather than manual approval

post_tool_call
  -> classify result as evidence, mutation, validation, or low-signal
  -> update session state
  -> write audit event

transform_tool_result
  -> summarize large text before context injection
  -> audit summarization

on_session_end / on_session_finalize
  -> audit final state and unverified-mutation warning
  -> clear session state
```

## Policy principles

1. **Autonomy first**: the plugin is designed for Hermes instances that usually run without manual approvals.
2. **Evidence before mutation**: editing existing files or mutating local process state should be preceded by nearby evidence.
3. **Verify after mutation**: after a mutation, the agent should run the narrowest validation before continuing.
4. **Generated context provenance**: injected LoopCraft/Proofrail panels are runtime context, not user speech, and must not be stored in SQL/scope-recall/long-term memory as user facts.
5. **Assistive reminders**: agents should evaluate reminders against the user's request and live evidence, follow applicable reminders, and state why when a reminder is stale or wrong.
6. **Audit over approval**: high-risk commands can be allowed in autonomous mode, but they must be audited and reflected back into the next reasoning context.
7. **Final evidence + cleanup report**: if a session mutates state, the final answer should include root cause, changes, validation, evidence, cleanup status, artifact categorization/classification, and remaining risk.
8. **Defensive, not sandbox**: this plugin is a workflow harness, not an OS permission boundary or full shell parser.

## Current limits

- Terminal parsing is heuristic and not a complete shell parser.
- `explain_state()` is currently a runtime helper, not a first-class Hermes tool schema.
- The validation suggestions are best-effort and should be treated as prompts to self-verify, not as guaranteed complete test plans.
- No durable task ledger or compaction snapshot persistence yet.
- Cleanup is a required closeout reminder after mutations; LoopCraft never deletes files automatically. It only reminds the agent to report cleanup status and artifact categorization/classification.

## Why this shape is open-source friendly

- Small modules with clear boundaries.
- No single giant entrypoint.
- No cross-language bridge.
- Autonomous defaults with explicit configuration boundary.
- Regression tests for behavior-changing rules.

### `task_ledger.py`

Session-level autonomous task ledger. It does not introduce a manual approval loop; it summarizes evidence, mutations, validations, high-risk actions, touched files, task self-routing hints, cleanup-aware closeout requirements, and final state into a task snapshot that can be injected into context and written to the audit log.
