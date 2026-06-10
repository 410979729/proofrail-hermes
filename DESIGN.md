# DESIGN

## Goal

Build a **Hermes-native Python plugin** that turns a focused set of runtime workflow guardrails into a clear, testable, publishable implementation, with priority on:

1. accurate hook mapping
2. a simple, explicit state machine
3. test coverage for every rule
4. no cross-language bridge complexity

## Design Principles

### 1. Runtime > prompt

All important constraints should live in Hermes hooks:

- `pre_tool_call`
- `post_tool_call`
- `transform_tool_result`
- `pre_llm_call`
- `on_session_start`
- `on_session_end`
- `on_session_finalize`

Prompt text is only a lightweight reminder layer. It does not carry the real enforcement burden.

### 2. Shared policy, host-native wiring

Policy logic should stay reusable and testable, but host wiring must remain Hermes-native.

Shared policy includes:

- command detection
- low-signal classification
- phase transitions
- result summarization

Hermes adapter responsibilities include:

- `register(ctx)`
- Hermes hook return shapes
- path and tool-name adaptation

### 3. No cross-language bridge in v1

v1 does not use a TS runtime with a Python shim and does not spawn a Node bridge.

Why:

- Hermes plugin entrypoints are naturally Python
- state and error propagation are simplest inside Python
- open-source installation and troubleshooting stay easier for users

### 4. TDD first

Every rule should start with a failing test before implementation.

Current test coverage includes:

- existing-file mutation records an advisory without evidence by default, and blocks only in `strict` mode
- mutation records pending-verification advisories before the next mutation by default, and blocks only in `strict` mode
- repeated low-signal probes record advisories by default, and block only in `strict` mode
- tool-result summarization
- stage-aware `pre_llm_call` context
- session end/finalize cleanup

## Runtime Model

### Session phases

The state machine intentionally keeps only three phases:

- `observe`
- `execute`
- `review`

Meaning:

- `observe`: not enough evidence yet
- `execute`: enough evidence exists for a minimal change
- `review`: a recent mutation happened and validation must run next

### State fields

Each session stores:

- `phase`
- `evidence_count`
- `last_evidence_label`
- `pending_verification`
- `last_mutation_label`
- `consecutive_low_signal`
- `last_low_signal_signature`
- `last_low_signal_intent`
- `last_updated_at`

### State lifecycle

- `on_session_start`: initialize session state
- `post_tool_call`: advance the state machine
- `on_session_end` / `on_session_finalize`: clean up session state
- prune automatically when TTL expires or capacity is exceeded

## Hook Contracts

### pre_tool_call

Returns either `None` to allow the call or, in strict / dangerous-command block paths:

```python
{"action": "block", "message": "..."}
```

Responsibilities:

- dangerous-command policy (`warn`, `allow`, `block`, `approve`)
- non-blocking workflow-risk recording for missing evidence / pending verification / broad evidence / low-signal repeats
- strict compatibility hard blocks for operators that explicitly choose strict mode

### post_tool_call

Responsibilities:

- classify observation / mutation / validation
- update evidence counts
- set `pending_verification`
- maintain the low-signal streak state

### transform_tool_result

Responsibilities:

- summarize oversized tool output
- reduce context pollution

### pre_llm_call

Responsibilities:

- inject phase summaries and runtime reminders
- append context rather than rewriting the system prompt

## Current v0.0.8 release line

The public `v0.0.8` / `0.0.8` release line includes:

1. default `enforcement_mode=advisory`; workflow risks are recorded as advisories and compact next-action cards instead of blocking tool calls
2. explicit `enforcement_mode=strict` compatibility for the older hard-block cooperative modes
3. default `dangerous_command_action=warn`; high-risk commands stay out of a manual approval loop, but they are audited and paired with self-verification reminders
4. a JSONL audit trail for session lifecycle, tool preflight, dangerous commands, tool results, advisories, and large-output summarization
5. a validation-suggestion layer that proposes narrow follow-up checks from touched files and command shape
6. session state for mutation / validation / dangerous-command counts, touched files, validation suggestions, advisories, recent labels, and task-ledger state
7. `pre_llm_call` injection of touched files, suggested validations, dangerous-command audit reminders, compact advisory cards, and final evidence-report requirements
8. explicit forced modes: `gather_target_evidence`, `validate_only`, `change_strategy`, and `user_choice` for strict/classifier paths
9. task-panel handoff framing with allowed / forbidden next actions and mode-specific collaboration wording
10. classifier fallback from unsupported structured output into `RuleBasedGrayAreaClassifier`
11. classifier-to-mode mapping so gray-area decisions become concrete runtime submodes
12. `forced_mode_transition` audit events from classifier, strict block, and tool-observation sources
13. `forward_progress_reopened` semantics when validation clears `validate_only`
14. diagnostic-preserving large-output summaries that keep `FAILED`, `ERROR`, traceback, and assertion lines from omitted middle sections
15. phantom-target recovery hardening for shell assignment tokens, suppression redirects, directory-level targets, Windows slash-style command switches, and handoff wording compatibility
16. behavior-simulation, advisory-runtime, and self-smoke coverage for the runtime path

## Version semantics

- GitHub release/tag line: `v0.0.8`
- Python package version: `0.0.8`

This split is intentional: GitHub tags keep the leading `v`, while Python packaging follows PEP 440.

## Planned Next Steps

1. expose `explain_state()` as a formal Hermes debug tool
2. add diff / mutation review summaries to the final review lane
3. add compaction-related snapshots and recovery anchors
4. add clean-install / wheel / plugin-dir installation smoke tests
5. validate the plugin in a live Hermes rollout
